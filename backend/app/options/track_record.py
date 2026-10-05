"""Whether the dashboard's predictions come true.

The ticket and the Screener put numbers on every structure -- the chance
of profit, the chance of touching a breakeven, the breakeven volatility
against a forecast -- and nothing so far checked them. This module does,
two ways:

* Forward: every Screener row with a valued structure (once a day per
  structure) and every ticket sent is stored with what was predicted for
  it. After its expiry the underlying's close settles it: the P/L had the
  structure been held to expiry, filled at the natural -- the same fill
  the predicted chance assumes -- and whether a breakeven was touched on
  the way. Exact, but the first answers arrive with the first expiries.

* Historical: the same question asked of the past year at once, from the
  stored at-the-money IV (Barchart's daily series) and the stock's closes.
  On each sampled day a condor is built at several short deltas, priced
  flat at that day's IV, its chance of profit read off the lognormal the
  IV implies, and settled at the close 45 days later. No smile, no quotes
  and no bid/ask -- so it tests the implied volatility itself as a
  forecaster (Natenberg ch. 20, "Implied Volatility as a Predictor"), not
  the dashboard's pricing of a real chain.

What counts as held to expiry is a choice: a managed trade closed early
ends elsewhere. The prediction being tested is defined at expiry, so the
outcome is too.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from statistics import NormalDist

from app.options.payoff import bs_price, intrinsic
from app.services.market_clock import ET

logger = logging.getLogger(__name__)

MULTIPLIER = 100

_SCHEMA = """
CREATE TABLE IF NOT EXISTS option_predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,             -- screen | ticket
    account TEXT NOT NULL DEFAULT '', -- paper | live | sim for a ticket
    strategy TEXT NOT NULL,
    underlying TEXT NOT NULL,
    expiry TEXT NOT NULL,
    legs TEXT NOT NULL,               -- [{kind, strike, side, ratio, expiry}]
    price REAL NOT NULL,              -- per share at the natural, + paid / - received
    spot REAL NOT NULL,
    chance REAL,
    touch REAL,
    touch_at REAL,
    breakeven_vol REAL,
    forecast_vol REAL,
    atm_iv REAL,
    max_loss REAL,                    -- per structure, negative
    recorded_on TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    ref TEXT,
    status TEXT NOT NULL DEFAULT 'open',   -- open | resolved | unresolvable
    settle REAL,
    settled_on TEXT,
    pnl REAL,                         -- per structure at expiry, at `price`
    won INTEGER,
    touched INTEGER,
    UNIQUE (source, account, underlying, strategy, expiry, legs, recorded_on)
);
CREATE INDEX IF NOT EXISTS idx_option_predictions_status ON option_predictions (status, expiry);
"""


@dataclass(frozen=True)
class Leg:
    kind: str
    strike: float
    side: str  # buy | sell
    ratio: int = 1
    expiry: date | None = None

    @property
    def sign(self) -> int:
        return 1 if self.side == "buy" else -1


def value_at(legs: list[Leg], settle: float) -> float:
    """The package's value per share when every leg expires at `settle`."""
    return sum(leg.sign * leg.ratio * intrinsic(leg.kind, settle, leg.strike) for leg in legs)


def settle_outcome(legs: list[Leg], price: float, settle: float) -> tuple[float, bool]:
    """(P/L per structure, won) for a package bought (+) or sold (-) at
    `price` per share and held to expiry."""
    pnl = (value_at(legs, settle) - price) * MULTIPLIER
    return pnl, pnl > 0


def touched_level(level: float | None, spot: float, highs_lows: list[tuple[float, float]]) -> bool | None:
    """Whether the underlying traded at `level` on any of the sessions, from
    whichever side of it `spot` was on."""
    if level is None or not highs_lows:
        return None
    if level >= spot:
        return any(high >= level for high, _ in highs_lows)
    return any(low <= level for _, low in highs_lows)


class PredictionStore:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    async def init_schema(self) -> None:
        def run() -> None:
            with self._connect() as conn:
                conn.executescript(_SCHEMA)

        await asyncio.to_thread(run)

    async def record(self, rows: list[dict]) -> int:
        """Insert, ignoring a structure already recorded today from the
        same source. Best-effort: a failed write must not fail a screen or
        an order. Returns how many were new."""
        if not rows:
            return 0
        now = datetime.now(UTC)
        today = now.astimezone(ET).date().isoformat()

        def run() -> int:
            added = 0
            with self._connect() as conn:
                for r in rows:
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO option_predictions (source, account, strategy, underlying, expiry, legs, "
                        "price, spot, chance, touch, touch_at, breakeven_vol, forecast_vol, atm_iv, max_loss, "
                        "recorded_on, recorded_at, ref) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            r["source"], r.get("account") or "", r["strategy"], r["underlying"].upper(),
                            r["expiry"], json.dumps(r["legs"], sort_keys=True), r["price"], r["spot"],
                            r.get("chance"), r.get("touch"), r.get("touch_at"), r.get("breakeven_vol"),
                            r.get("forecast_vol"), r.get("atm_iv"), r.get("max_loss"),
                            today, now.isoformat(timespec="seconds"), r.get("ref"),
                        ),
                    )
                    added += cur.rowcount
            return added

        try:
            return await asyncio.to_thread(run)
        except Exception:
            logger.exception("Failed to record %d prediction(s)", len(rows))
            return 0

    async def due(self, today: date) -> list[dict]:
        """Open predictions whose expiry has passed (today's settle once the
        session is over is the caller's call -- it passes tomorrow's date)."""

        def run() -> list[dict]:
            with self._connect() as conn:
                return [
                    dict(r)
                    for r in conn.execute(
                        "SELECT * FROM option_predictions WHERE status = 'open' AND expiry < ?", (today.isoformat(),)
                    )
                ]

        return await asyncio.to_thread(run)

    async def settle(self, pid: int, *, status: str, settle=None, settled_on=None, pnl=None, won=None, touched=None) -> None:
        def run() -> None:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE option_predictions SET status = ?, settle = ?, settled_on = ?, pnl = ?, won = ?, touched = ? "
                    "WHERE id = ?",
                    (status, settle, settled_on, pnl, None if won is None else int(won), None if touched is None else int(touched), pid),
                )

        await asyncio.to_thread(run)

    async def all(self) -> list[dict]:
        def run() -> list[dict]:
            with self._connect() as conn:
                return [dict(r) for r in conn.execute("SELECT * FROM option_predictions ORDER BY recorded_at DESC")]

        return await asyncio.to_thread(run)


# --- recording -----------------------------------------------------------------


def from_screen_row(row: dict, strategy: str) -> dict | None:
    """The structure a Screener row valued, as a prediction -- the same legs
    and price its chance was computed for (screener._outcome_for)."""
    outcome = row.get("outcome")
    if not outcome or not row.get("expiry") or not row.get("spot"):
        return None
    legs: list[dict] = []
    if strategy in ("cash_secured_put", "covered_call"):
        leg = row.get("short_put") if strategy == "cash_secured_put" else row.get("short_call")
        if not leg or leg.get("mid") is None:
            return None
        kind = "put" if strategy == "cash_secured_put" else "call"
        legs = [{"kind": kind, "strike": leg["strike"], "side": "sell", "ratio": 1}]
        bid = leg.get("bid")
        natural = bid if bid is not None and bid > 0 else leg["mid"]
        price = -natural
    else:
        pairs = [row.get("put_spread"), row.get("call_spread")] if strategy == "iron_condor" else [row.get("put_spread")]
        if any(p is None for p in pairs):
            return None
        credit = 0.0
        cross = 0.0
        for pair, kind in zip(pairs, ("put", "call")):
            legs.append({"kind": kind, "strike": pair["short_strike"], "side": "sell", "ratio": 1})
            legs.append({"kind": kind, "strike": pair["long_strike"], "side": "buy", "ratio": 1})
            credit += pair["credit"]
            cross += pair.get("cross", 0.0) or 0.0
        if strategy == "debit_spread":
            for leg in legs:
                leg["side"] = "buy" if leg["side"] == "sell" else "sell"
            price = abs(credit) + cross
        else:
            price = -(credit - cross)
    return {
        "source": "screen",
        "strategy": strategy,
        "underlying": row["symbol"],
        "expiry": row["expiry"],
        "legs": legs,
        "price": round(price, 4),
        "spot": row["spot"],
        "chance": outcome.get("win_probability"),
        "touch": None,
        "touch_at": None,
        "breakeven_vol": outcome.get("breakeven_vol"),
        "forecast_vol": row.get("forecast_vol"),
        "atm_iv": row.get("atm_iv"),
        "max_loss": outcome.get("max_loss"),
    }


def from_resolved_spread(spread, account: str, ref: str | None) -> dict | None:
    """A sent ticket as a prediction, priced at the natural its chance of
    profit assumes (falling back to the limit when no natural is quoted)."""
    if spread.chance is None:
        return None
    expiries = {leg.expiry for leg in spread.legs}
    legs = [
        {"kind": leg.kind, "strike": leg.strike, "side": leg.side, "ratio": leg.ratio_qty,
         **({"expiry": leg.expiry.isoformat()} if len(expiries) > 1 else {})}
        for leg in spread.legs
    ]
    natural = spread.net_natural if spread.net_natural is not None else spread.limit_price
    price = natural if spread.direction == "debit" else -natural
    forecast = (spread.vol_forecast or {}).get("forecast") if spread.vol_forecast else None
    return {
        "source": "ticket",
        "account": account,
        "strategy": spread.strategy,
        "underlying": spread.underlying,
        "expiry": spread.expiry.isoformat(),
        "legs": legs,
        "price": round(price, 4),
        "spot": spread.spot,
        "chance": spread.chance,
        "touch": spread.touch,
        "touch_at": spread.touch_at,
        "breakeven_vol": spread.breakeven_vol,
        "forecast_vol": forecast if forecast is not None else spread.realised_vol,
        "atm_iv": spread.atm_iv,
        "max_loss": (spread.max_loss / spread.qty) if spread.max_loss is not None and spread.qty else None,
        "ref": ref,
    }


# --- resolving -----------------------------------------------------------------


def _bar_day(bar) -> date | None:
    ts = getattr(bar, "timestamp", None)
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return (ts if ts.tzinfo else ts.replace(tzinfo=UTC)).astimezone(ET).date()
    return ts


def _sessions(bars) -> list[tuple[date, float, float, float]]:
    """(day, high, low, close), ascending."""
    out = []
    for bar in bars or []:
        day = _bar_day(bar)
        if day is None or getattr(bar, "close", None) is None:
            continue
        out.append((day, float(bar.high), float(bar.low), float(bar.close)))
    out.sort()
    return out


def resolve_one(pred: dict, sessions: list[tuple[date, float, float, float]]) -> dict:
    """Settle one prediction against the underlying's sessions. Returns the
    fields to store; status 'unresolvable' for a multi-expiry structure (its
    later leg has no expiry value yet) or when the expiry session is
    missing from the data."""
    legs_raw = json.loads(pred["legs"])
    if any("expiry" in leg for leg in legs_raw):
        return {"status": "unresolvable"}
    expiry = date.fromisoformat(pred["expiry"])
    recorded = date.fromisoformat(pred["recorded_on"])
    upto = [s for s in sessions if s[0] <= expiry]
    if not upto or (expiry - upto[-1][0]).days > 4:
        return {"status": "unresolvable"}
    settle_day, _, _, settle = upto[-1]
    legs = [Leg(l["kind"], float(l["strike"]), l["side"], int(l.get("ratio", 1))) for l in legs_raw]
    pnl, won = settle_outcome(legs, float(pred["price"]), settle)
    window = [(h, low) for d, h, low, _ in upto if d >= recorded]
    touched = touched_level(pred.get("touch_at"), float(pred["spot"]), window)
    return {
        "status": "resolved",
        "settle": settle,
        "settled_on": settle_day.isoformat(),
        "pnl": round(pnl, 2),
        "won": won,
        "touched": touched,
    }


async def resolve_due(store: PredictionStore, clients, today: date | None = None) -> int:
    """Settle every open prediction whose expiry is behind us. One batched
    bars call for all the underlyings involved."""
    today = today or datetime.now(UTC).astimezone(ET).date()
    due = await store.due(today)
    if not due or clients is None:
        return 0
    from app.market_data.bars import get_daily_bars_multi

    symbols = sorted({p["underlying"] for p in due})
    earliest = min(date.fromisoformat(p["recorded_on"]) for p in due)
    try:
        bars = await get_daily_bars_multi(clients, symbols, lookback_days=(today - earliest).days + 10)
    except Exception:
        logger.exception("Bars for settling predictions failed")
        return 0
    settled = 0
    for pred in due:
        result = resolve_one(pred, _sessions(bars.get(pred["underlying"], [])))
        await store.settle(pred["id"], **result)
        settled += result["status"] == "resolved"
    logger.info("Track record: settled %d of %d due prediction(s)", settled, len(due))
    return settled


# --- reporting -----------------------------------------------------------------

CHANCE_BUCKETS = [(0.0, 0.3), (0.3, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01)]
MARGIN_BUCKETS = [(-99.0, -5.0), (-5.0, -2.0), (-2.0, 0.0), (0.0, 2.0), (2.0, 5.0), (5.0, 99.0)]


def wilson(wins: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """The 95 % Wilson interval of a win rate -- honest at small counts,
    where a plain p +/- 2 sigma runs below zero."""
    if n <= 0:
        return None
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def _summary(items: list[dict]) -> dict:
    n = len(items)
    wins = sum(1 for i in items if i["won"])
    ci = wilson(wins, n)
    risk = [i["pnl"] / abs(i["max_loss"]) for i in items if i.get("max_loss")]
    return {
        "n": n,
        "predicted": round(sum(i["chance"] for i in items) / n, 4) if n else None,
        "actual": round(wins / n, 4) if n else None,
        "ci": [round(ci[0], 4), round(ci[1], 4)] if ci else None,
        "avg_pnl": round(sum(i["pnl"] for i in items) / n, 2) if n else None,
        "avg_return_on_risk": round(sum(risk) / len(risk), 4) if risk else None,
    }


def report(items: list[dict]) -> dict:
    """Calibration of resolved predictions: overall, by predicted chance
    (does 70 % win 70 % of the time?), by the breakeven-vol margin (does a
    positive margin win more?), and the touch prediction. `items` carry
    chance, won, pnl, max_loss, and optionally margin / touch / touched."""
    usable = [i for i in items if i.get("chance") is not None and i.get("won") is not None]
    by_chance = []
    for lo, hi in CHANCE_BUCKETS:
        bucket = [i for i in usable if lo <= i["chance"] < hi]
        if bucket:
            by_chance.append({"range": [lo, min(hi, 1.0)], **_summary(bucket)})
    with_margin = [i for i in usable if i.get("margin") is not None]
    by_margin = []
    for lo, hi in MARGIN_BUCKETS:
        bucket = [i for i in with_margin if lo <= i["margin"] < hi]
        if bucket:
            by_margin.append({"range": [lo, hi], **_summary(bucket)})
    touch = [i for i in usable if i.get("touch") is not None and i.get("touched") is not None]
    return {
        "overall": _summary(usable),
        "by_chance": by_chance,
        "by_margin": by_margin,
        "touch": (
            {
                "n": len(touch),
                "predicted": round(sum(i["touch"] for i in touch) / len(touch), 4),
                "actual": round(sum(1 for i in touch if i["touched"]) / len(touch), 4),
            }
            if touch
            else None
        ),
    }


def forward_items(rows: list[dict]) -> list[dict]:
    """Resolved predictions as report items, with the margin read the way
    the ticket reads it: a credit wants the forecast below the breakeven."""
    out = []
    for r in rows:
        if r["status"] != "resolved":
            continue
        margin = None
        if r.get("breakeven_vol") is not None and r.get("forecast_vol") is not None:
            gap = (r["breakeven_vol"] - r["forecast_vol"]) * 100
            margin = gap if r["price"] < 0 else -gap
        out.append(
            {
                "chance": r["chance"],
                "won": bool(r["won"]),
                "pnl": r["pnl"],
                "max_loss": r.get("max_loss"),
                "margin": margin,
                "touch": r.get("touch"),
                "touched": None if r.get("touched") is None else bool(r["touched"]),
                "source": r["source"],
                "strategy": r["strategy"],
            }
        )
    return out


# --- the historical reconstruction ---------------------------------------------

HIST_DTE = 45
HIST_STEP_SESSIONS = 5
HIST_SHORT_DELTAS = (0.10, 0.16, 0.25, 0.35)
HIST_WING_DELTA = 0.05
_N = NormalDist()


def strike_for_delta(kind: str, spot: float, sigma: float, years: float, delta: float) -> float:
    """The (continuous) strike whose Black-Scholes |delta| is `delta`, zero
    rate -- what a chain's strike nearest that delta approximates."""
    root = sigma * math.sqrt(years)
    d1 = _N.inv_cdf(delta) if kind == "call" else _N.inv_cdf(1 - delta)
    return spot * math.exp(-d1 * root + 0.5 * sigma * sigma * years)


def lognormal_between(spot: float, lo: float, hi: float, sigma: float, years: float) -> float:
    """P(lo < S_T < hi) under the zero-drift lognormal the IV implies."""
    root = sigma * math.sqrt(years)
    mu = math.log(spot) - 0.5 * sigma * sigma * years

    def cdf(x: float) -> float:
        return _N.cdf((math.log(x) - mu) / root) if x > 0 else 0.0

    return max(0.0, cdf(hi) - cdf(lo))


def touch_probability(spot: float, barrier: float, sigma: float, years: float) -> float:
    """Reflection-principle chance of trading at `barrier` before expiry."""
    if sigma <= 0 or years <= 0:
        return 0.0
    root = sigma * math.sqrt(years)
    distance = abs(math.log(barrier / spot))
    return min(1.0, 2 * (1 - _N.cdf(distance / root)))


def historical_items(
    iv_by_symbol: dict[str, list[tuple[date, float]]],
    sessions_by_symbol: dict[str, list[tuple[date, float, float, float]]],
    *,
    last_day: date,
) -> list[dict]:
    """Condors rebuilt on past days from the stored ATM IV and settled at the
    close HIST_DTE days later -- see the module docstring for what this
    does and does not test."""
    from app.options.iv_context import forecast_vol

    years = HIST_DTE / 365.0
    out: list[dict] = []
    for symbol, ivs in iv_by_symbol.items():
        sessions = sessions_by_symbol.get(symbol) or []
        if len(sessions) < 30:
            continue
        index = {s[0]: i for i, s in enumerate(sessions)}
        ivs = sorted(ivs)
        for k, (day, sigma) in enumerate(ivs):
            if k % HIST_STEP_SESSIONS or day not in index or sigma <= 0:
                continue
            expiry = day + timedelta(days=HIST_DTE)
            if expiry > last_day:
                continue
            i = index[day]
            spot = sessions[i][3]
            upto = [s for s in sessions[i:] if s[0] <= expiry]
            if not upto or (expiry - upto[-1][0]).days > 4:
                continue
            settle = upto[-1][3]
            closes = [s[3] for s in sessions[: i + 1]]
            fc = forecast_vol(closes, HIST_DTE)
            for short_delta in HIST_SHORT_DELTAS:
                kp = strike_for_delta("put", spot, sigma, years, short_delta)
                kc = strike_for_delta("call", spot, sigma, years, short_delta)
                wp = strike_for_delta("put", spot, sigma, years, HIST_WING_DELTA)
                wc = strike_for_delta("call", spot, sigma, years, HIST_WING_DELTA)
                legs = [Leg("put", wp, "buy"), Leg("put", kp, "sell"), Leg("call", kc, "sell"), Leg("call", wc, "buy")]
                credit = sum(-leg.sign * bs_price(leg.kind, spot, leg.strike, years, sigma) for leg in legs)
                if credit <= 0:
                    continue
                be_lo, be_hi = kp - credit, kc + credit
                chance = lognormal_between(spot, be_lo, be_hi, sigma, years)
                nearest = be_lo if spot - be_lo < be_hi - spot else be_hi
                pnl, won = settle_outcome(legs, -credit, settle)
                window = [(h, low) for _, h, low, _ in upto[1:]]
                width = max(kp - wp, wc - kc)
                out.append(
                    {
                        "symbol": symbol,
                        "day": day.isoformat(),
                        "short_delta": short_delta,
                        "chance": chance,
                        "won": won,
                        "pnl": pnl,
                        "max_loss": -(width - credit) * MULTIPLIER,
                        # Flat pricing: the breakeven vol is the IV itself.
                        "margin": (sigma - fc.forecast) * 100 if fc else None,
                        "touch": touch_probability(spot, nearest, sigma, years),
                        "touched": touched_level(nearest, spot, window),
                    }
                )
    return out


# Symbols need this many stored IV readings to be rebuilt -- half a year.
HIST_MIN_READINGS = 120
_HIST_CACHE: dict[date, dict] = {}


def iv_series(db_path: str, min_readings: int = HIST_MIN_READINGS) -> dict[str, list[tuple[date, float]]]:
    """Each symbol's stored ATM IV by session, for the symbols with enough
    of it (in practice the Barchart-seeded year)."""
    conn = sqlite3.connect(db_path, timeout=30.0)
    try:
        rows = conn.execute("SELECT symbol, session_date, atm_iv FROM option_iv_history").fetchall()
    finally:
        conn.close()
    out: dict[str, list[tuple[date, float]]] = {}
    for symbol, day, iv in rows:
        if iv and iv > 0:
            out.setdefault(symbol.upper(), []).append((date.fromisoformat(day), float(iv)))
    return {k: sorted(v) for k, v in out.items() if len(v) >= min_readings}


async def historical_report(db_path: str, clients, today: date | None = None) -> dict:
    """The reconstruction's report, computed once a day: one bars call for
    every symbol (two and a half years, so the earliest sample still has a
    year of closes behind its forecast)."""
    today = today or datetime.now(UTC).astimezone(ET).date()
    if today in _HIST_CACHE:
        return _HIST_CACHE[today]
    series = await asyncio.to_thread(iv_series, db_path)
    if not series or clients is None:
        return {"available": False, "reason": "no stored IV series long enough to rebuild from"}
    from app.market_data.bars import get_daily_bars_multi

    try:
        bars = await get_daily_bars_multi(clients, sorted(series), lookback_days=920)
    except Exception:
        logger.exception("Bars for the historical track record failed")
        return {"available": False, "reason": "daily bars unavailable"}
    sessions = {sym: _sessions(bars.get(sym, [])) for sym in series}
    items = await asyncio.to_thread(historical_items, series, sessions, last_day=today - timedelta(days=1))
    first = min((i["day"] for i in items), default=None)
    last = max((i["day"] for i in items), default=None)
    body = {
        "available": bool(items),
        "symbols": len({i["symbol"] for i in items}),
        "from": first,
        "to": last,
        "dte": HIST_DTE,
        "step_sessions": HIST_STEP_SESSIONS,
        "by_delta": [
            {"short_delta": d, **_summary([i for i in items if i["short_delta"] == d])}
            for d in HIST_SHORT_DELTAS
            if any(i["short_delta"] == d for i in items)
        ],
        **report(items),
    }
    _HIST_CACHE.clear()
    _HIST_CACHE[today] = body
    return body
