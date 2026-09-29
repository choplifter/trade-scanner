"""Which underlyings are worth writing premium on, and which are worth
buying it -- the Optimizer's question one level up.

The Optimizer takes a symbol and finds the structure; this takes a list of
symbols and finds the ones whose chain is suited to a strategy at all. The
criteria are the ones a premium seller screens on: enough open interest to
get out again, quotes tight enough that crossing does not eat the credit,
an expiry 30-60 days out, no earnings inside it, and short strikes near a
given delta.

Two of the usual criteria are not available here and the rows say so
rather than guessing:

*Contract volume* would need a day bar per contract -- hundreds of calls
per symbol. Open interest and the quoted sizes stand in; they answer "can
this be traded" nearly as well and cost nothing extra.

*IV percentile* needs a year of readings per symbol, and the store fills
only for symbols someone looked at (app.options.iv_history_store). Where
a rank exists it is reported; the measure that always works is implied
against **realised** volatility -- the close-to-close deviation of the last
20 sessions, which needs only the daily bars the app already fetches. IV
well above RV is the premium seller's edge; below it, the buyer's.

Nothing here decides anything. Every row carries the numbers, which
criteria they pass, and a one-line reason; the ranking is by how many
criteria pass and then by the IV/RV ratio, in the direction the strategy
asks for.
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.options.chain import Chain, StrikeRow
from app.trading.errors import OrderRejected

logger = logging.getLogger(__name__)

Bias = Literal["sell_premium", "buy_premium", "neutral"]

# What is being screened for. The shared criteria (open interest, quote
# width, expiry, earnings, the short deltas) describe a chain and apply to
# all of them; each strategy adds what its own shape needs, because a
# chain that suits a cash-secured put can still be useless for a vertical
# -- five-dollar strikes on a fifteen-dollar stock leave no wing to buy.
Strategy = Literal[
    "cash_secured_put",
    "covered_call",
    "credit_spread",
    "iron_condor",
    "debit_spread",
    "long_option",
    "calendar",
]

STRATEGY_BIAS: dict[str, Bias] = {
    "cash_secured_put": "sell_premium",
    "covered_call": "sell_premium",
    "credit_spread": "sell_premium",
    "iron_condor": "sell_premium",
    "debit_spread": "buy_premium",
    "long_option": "buy_premium",
    # A calendar sells the front and buys the back: what it wants is not a
    # level of implied volatility but a *slope* between two expiries.
    "calendar": "neutral",
}

# Strategies whose shape needs a wing beyond the short strike.
SPREAD_STRATEGIES = frozenset({"credit_spread", "iron_condor", "debit_spread"})
# What a credit structure must bring in against the width it risks. The
# number belongs to the delta band it is screened at: a 30-delta short
# brings back about a third of the width, a 15-delta one -- the middle of
# SHORT_DELTA_BAND -- nearer a tenth. Measured on the watchlist at a
# 3 %-of-spot width the real spread is 7-11 % (IWM 253/261 at 9 %, AAPL
# 305/310 at 11 %), so the bar sits at 10 % and separates those from the
# far-out strikes that bring back a rounding error.
MIN_CREDIT_TO_WIDTH = 0.10
# How wide the wing is aimed at, as a share of spot, and how far out one
# is accepted at all. Aimed rather than nearest: the strike next to the
# short is half a point away on an ETF chain, and a 0.50-wide vertical is
# not a trade -- it also reads as a failure on credit-to-width, since the
# ratio falls with the width. Three percent of spot is ~8 points on a 260
# ETF, the width these are actually written at.
WING_TARGET_PCT = 0.03
WING_REACH_PCT = 0.06
# A calendar wants the front expiry priced above the back one. Below this
# the slope is not worth the two commissions.
MIN_TERM_RATIO = 1.03

# The screen's defaults, which are the conventional premium-selling ones.
DTE_RANGE = (30, 60)
# Open interest across the *screened expiry's* strikes, not the whole
# chain: that is what one fetch can see, and the two numbers differ by an
# order of magnitude. Measured this way the liquid index ETFs come in
# around 6-14k (QQQ 7.5k, TLT 13.7k, IWM 5.7k on a 38-day expiry) and a
# single name around 1k, so the bar for "deep enough to get out of" sits
# at 5k. A screen written against a whole-chain number like 50k would
# reject everything and look broken.
MIN_OPEN_INTEREST = 5_000
MAX_SPREAD_FRACTION = 0.10
SHORT_DELTA_BAND = (0.10, 0.20)
# Sessions of closes behind the realised volatility. Twenty is a trading
# month: long enough to be a measure, short enough to still describe now.
REALISED_SESSIONS = 20
# What counts as implied volatility being rich or cheap against realised.
# The gap is normally positive -- the variance risk premium is why selling
# options pays at all -- so "rich" has to mean more than "positive".
RICH_IV_RATIO = 1.20
CHEAP_IV_RATIO = 0.95
# How many symbols one run may price. Each is a chain fetch; the cap is
# what keeps a screen off the broker's rate limit.
MAX_SYMBOLS = 60


class ScreenRequest(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=MAX_SYMBOLS)
    strategy: Strategy = "cash_secured_put"
    dte_min: int = Field(default=DTE_RANGE[0], ge=1, le=400)
    dte_max: int = Field(default=DTE_RANGE[1], ge=1, le=400)
    min_open_interest: int = Field(default=MIN_OPEN_INTEREST, ge=0)
    max_spread_fraction: float = Field(default=MAX_SPREAD_FRACTION, gt=0.0, le=1.0)
    short_delta_min: float = Field(default=SHORT_DELTA_BAND[0], gt=0.0, lt=1.0)
    short_delta_max: float = Field(default=SHORT_DELTA_BAND[1], gt=0.0, lt=1.0)
    # An earnings report inside the expiry is a different trade: the IV is
    # rich because of the print, and it collapses with it.
    avoid_earnings: bool = True

    @property
    def bias(self) -> Bias:
        return STRATEGY_BIAS[self.strategy]

    @model_validator(mode="after")
    def _check(self) -> "ScreenRequest":
        if self.dte_min > self.dte_max:
            raise ValueError("dte_min must not exceed dte_max")
        if self.short_delta_min > self.short_delta_max:
            raise ValueError("short_delta_min must not exceed short_delta_max")
        return self


@dataclass
class Criterion:
    """One line of the screen: what was asked, what was found, and whether
    it passed. `value` is None when the number could not be had at all,
    which is reported as "unknown" and never as a pass."""

    key: str
    label: str
    value: float | None
    passed: bool | None
    detail: str


@dataclass
class Row:
    symbol: str
    expiry: date | None = None
    dte: int | None = None
    spot: float | None = None
    atm_iv: float | None = None
    realised_vol: float | None = None
    iv_rv_ratio: float | None = None
    iv_rank: float | None = None
    iv_rank_samples: int = 0
    open_interest: int = 0
    short_put: dict | None = None
    short_call: dict | None = None
    # A vertical built off each short leg, where the chain has a wing.
    put_spread: dict | None = None
    call_spread: dict | None = None
    # The calendar's slope: the back expiry's ATM IV and front over back.
    back_expiry: date | None = None
    back_iv: float | None = None
    term_ratio: float | None = None
    earnings_date: date | None = None
    criteria: list[Criterion] = field(default_factory=list)
    note: str | None = None

    @property
    def passed(self) -> int:
        return sum(1 for c in self.criteria if c.passed)

    @property
    def scored(self) -> int:
        return sum(1 for c in self.criteria if c.passed is not None)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "expiry": self.expiry.isoformat() if self.expiry else None,
            "dte": self.dte,
            "spot": None if self.spot is None else round(self.spot, 2),
            "atm_iv": None if self.atm_iv is None else round(self.atm_iv, 4),
            "realised_vol": None if self.realised_vol is None else round(self.realised_vol, 4),
            "iv_rv_ratio": None if self.iv_rv_ratio is None else round(self.iv_rv_ratio, 3),
            "iv_rank": self.iv_rank,
            "iv_rank_samples": self.iv_rank_samples,
            "open_interest": self.open_interest,
            "short_put": self.short_put,
            "short_call": self.short_call,
            "put_spread": self.put_spread,
            "call_spread": self.call_spread,
            "back_expiry": self.back_expiry.isoformat() if self.back_expiry else None,
            "back_iv": None if self.back_iv is None else round(self.back_iv, 4),
            "term_ratio": None if self.term_ratio is None else round(self.term_ratio, 3),
            "earnings_date": self.earnings_date.isoformat() if self.earnings_date else None,
            "criteria": [
                {"key": c.key, "label": c.label, "value": c.value, "passed": c.passed, "detail": c.detail}
                for c in self.criteria
            ],
            "passed": self.passed,
            "scored": self.scored,
            "note": self.note,
        }


def realised_vol(closes: list[float], sessions: int = REALISED_SESSIONS) -> float | None:
    """Annualised close-to-close volatility of the last `sessions` returns,
    or None with too few closes. The same units as an implied volatility,
    so the two can be divided."""
    usable = [c for c in closes if c and c > 0]
    if len(usable) < sessions + 1:
        return None
    window = usable[-(sessions + 1) :]
    returns = [math.log(b / a) for a, b in zip(window, window[1:])]
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(variance) * math.sqrt(252)


def atm_row(rows: list[StrikeRow], spot: float) -> StrikeRow | None:
    quoted = [r for r in rows if (r.call and r.call.iv) or (r.put and r.put.iv)]
    return min(quoted, key=lambda r: abs(r.strike - spot)) if quoted else None


def atm_iv_of(rows: list[StrikeRow], spot: float) -> float | None:
    row = atm_row(rows, spot)
    if row is None:
        return None
    ivs = [q.iv for q in (row.call, row.put) if q is not None and q.iv]
    return sum(ivs) / len(ivs) if ivs else None


def spread_fraction(bid: float | None, ask: float | None) -> float | None:
    """What crossing this quote costs against its own mid. None when the
    contract is not two-sided -- which is worse than wide, not better."""
    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
        return None
    mid = (bid + ask) / 2
    return (ask - bid) / mid if mid > 0 else None


def pick_short(rows: list[StrikeRow], kind: str, band: tuple[float, float]) -> dict | None:
    """The strike whose |delta| sits nearest the middle of `band`, with the
    numbers a screen judges it by. None when no contract of that kind is
    quoted with a delta."""
    lo, hi = band
    target = (lo + hi) / 2
    best: tuple[float, StrikeRow] | None = None
    for row in rows:
        quote = row.call if kind == "call" else row.put
        if quote is None or quote.delta is None:
            continue
        delta = abs(quote.delta)
        if not 0 < delta < 1:
            continue
        distance = abs(delta - target)
        if best is None or distance < best[0]:
            best = (distance, row)
    if best is None:
        return None
    row = best[1]
    quote = row.call if kind == "call" else row.put
    assert quote is not None
    frac = spread_fraction(quote.bid, quote.ask)
    # Judged on the number that is shown: a delta the card prints as 0.10
    # must not be marked out of a band that starts at 0.10 because the
    # float behind it is 0.09999999998.
    delta = round(abs(quote.delta or 0.0), 4)
    return {
        "symbol": quote.symbol,
        "strike": row.strike,
        "delta": delta,
        "in_band": lo <= delta <= hi,
        "bid": quote.bid,
        "ask": quote.ask,
        "mid": quote.mid,
        "open_interest": quote.open_interest,
        "spread_fraction": None if frac is None else round(frac, 4),
    }


def pick_wing(rows: list[StrikeRow], kind: str, short_strike: float, spot: float) -> dict | None:
    """The long leg of a vertical: the listed strike further out of the
    money whose distance from the short is nearest WING_TARGET_PCT of spot,
    accepted out to WING_REACH_PCT, quoted on both sides. None when the
    chain has no wing to buy -- the answer for a five-dollar-strike chain
    on a cheap stock, and the reason a vertical screen is not just a
    premium screen."""
    reach = spot * WING_REACH_PCT
    target = spot * WING_TARGET_PCT
    candidates = []
    for row in rows:
        quote = row.call if kind == "call" else row.put
        if quote is None or quote.mid is None:
            continue
        further = row.strike > short_strike if kind == "call" else row.strike < short_strike
        if not further or abs(row.strike - short_strike) > reach:
            continue
        candidates.append((abs(abs(row.strike - short_strike) - target), row, quote))
    if not candidates:
        return None
    _distance, row, quote = min(candidates, key=lambda c: c[0])
    return {
        "symbol": quote.symbol,
        "strike": row.strike,
        "mid": quote.mid,
        "bid": quote.bid,
        "ask": quote.ask,
        "open_interest": quote.open_interest,
        "spread_fraction": spread_fraction(quote.bid, quote.ask),
        "width": round(abs(row.strike - short_strike), 4),
    }


def vertical_of(short: dict | None, wing: dict | None) -> dict | None:
    """(credit, width, credit/width) for a short leg and its wing, or None
    when either is missing a price. The ratio is what says whether the
    premium is worth the width it risks."""
    if short is None or wing is None or short.get("mid") is None or wing.get("mid") is None:
        return None
    width = wing["width"]
    credit = round(short["mid"] - wing["mid"], 4)
    if width <= 0:
        return None
    return {
        "short_strike": short["strike"],
        "long_strike": wing["strike"],
        "width": width,
        "credit": credit,
        "credit_to_width": round(credit / width, 4),
        "wing_open_interest": wing["open_interest"],
        "wing_spread_fraction": wing["spread_fraction"],
    }


def pick_expiry(expiries: list[date], today: date, dte_min: int, dte_max: int) -> date | None:
    """The listed expiry inside the window, nearest its middle -- the one a
    screen means by "30 to 60 days"."""
    target = (dte_min + dte_max) / 2
    inside = [e for e in expiries if dte_min <= (e - today).days <= dte_max]
    return min(inside, key=lambda e: abs((e - today).days - target)) if inside else None


def _criteria(row: Row, req: ScreenRequest) -> list[Criterion]:
    """The screen, read off a priced row. Each criterion answers with a
    number and a verdict, or None when the number is not knowable."""
    out: list[Criterion] = []
    oi_ok = row.open_interest >= req.min_open_interest
    out.append(
        Criterion(
            "open_interest",
            "Open interest",
            float(row.open_interest),
            oi_ok,
            f"{row.open_interest:,} across this expiry's fetched strikes, wanted {req.min_open_interest:,}+",
        )
    )

    fracs = [
        leg["spread_fraction"]
        for leg in (row.short_put, row.short_call)
        if leg is not None and leg["spread_fraction"] is not None
    ]
    widest = max(fracs) if fracs else None
    out.append(
        Criterion(
            "spread",
            "Quote width",
            None if widest is None else round(widest, 4),
            None if widest is None else widest <= req.max_spread_fraction,
            "no two-sided quote at the short strikes"
            if widest is None
            else f"{widest:.1%} of mid at the wider short leg, wanted under {req.max_spread_fraction:.0%}",
        )
    )

    out.append(
        Criterion(
            "dte",
            "Days to expiry",
            None if row.dte is None else float(row.dte),
            row.dte is not None,
            f"{row.dte} days ({row.expiry})" if row.dte is not None else f"no expiry listed {req.dte_min}-{req.dte_max} days out",
        )
    )

    if req.avoid_earnings:
        inside = row.earnings_date is not None and row.expiry is not None and row.earnings_date <= row.expiry
        out.append(
            Criterion(
                "earnings",
                "Earnings clear",
                None,
                (not inside) if row.earnings_date is not None else None,
                f"reports {row.earnings_date}, inside this expiry" if inside
                else f"next report {row.earnings_date}, after this expiry" if row.earnings_date
                else "no report date known",
            )
        )

    for leg, label in ((row.short_put, "Short put delta"), (row.short_call, "Short call delta")):
        key = "short_put_delta" if "put" in label.lower() else "short_call_delta"
        out.append(
            Criterion(
                key,
                label,
                None if leg is None else leg["delta"],
                None if leg is None else bool(leg["in_band"]),
                "no quoted delta on that side"
                if leg is None
                else f"{leg['delta']:.2f} at {leg['strike']:g}, wanted {req.short_delta_min:.2f}-{req.short_delta_max:.2f}",
            )
        )

    if req.strategy in SPREAD_STRATEGIES:
        # An iron condor needs both wings; a vertical needs the one on its
        # own side, and the put side is the one a screen defaults to.
        pairs = [row.put_spread, row.call_spread] if req.strategy == "iron_condor" else [row.put_spread]
        found = [p for p in pairs if p is not None]
        out.append(
            Criterion(
                "wing",
                "Wing to buy",
                None if not found else float(min(p["width"] for p in found)),
                len(found) == len(pairs),
                "no listed strike further out within "
                f"{WING_REACH_PCT:.0%} of spot -- nothing to cap the risk with"
                if len(found) < len(pairs)
                else " and ".join(f"{p['long_strike']:g} against {p['short_strike']:g} ({p['width']:g} wide)" for p in found),
            )
        )
        if req.strategy == "debit_spread":
            # A debit vertical pays the credit rather than taking it, so
            # what matters is that the same pair is quoted at all.
            pass
        else:
            ratios = [p["credit_to_width"] for p in found]
            worst = min(ratios) if ratios else None
            out.append(
                Criterion(
                    "credit_to_width",
                    "Credit vs width",
                    None if worst is None else round(worst, 4),
                    None if worst is None else worst >= MIN_CREDIT_TO_WIDTH,
                    "no priced pair to measure"
                    if worst is None
                    else f"{worst:.0%} of the width comes back as credit, wanted {MIN_CREDIT_TO_WIDTH:.0%}+",
                )
            )

    if req.strategy == "calendar":
        out.append(
            Criterion(
                "term_structure",
                "Front over back",
                row.term_ratio,
                None if row.term_ratio is None else row.term_ratio >= MIN_TERM_RATIO,
                "no second expiry to compare"
                if row.term_ratio is None
                else f"front IV {row.atm_iv:.0%} against {row.back_expiry} at {row.back_iv:.0%} = {row.term_ratio:.2f}x, "
                f"wanted {MIN_TERM_RATIO:.2f}+",
            )
        )

    ratio = row.iv_rv_ratio
    if req.bias == "buy_premium":
        passed = None if ratio is None else ratio <= CHEAP_IV_RATIO
        want = f"wanted at or under {CHEAP_IV_RATIO:.2f} (implied cheap against realised)"
    elif req.bias == "sell_premium":
        passed = None if ratio is None else ratio >= RICH_IV_RATIO
        want = f"wanted {RICH_IV_RATIO:.2f}+ (implied rich against realised)"
    else:
        passed = ratio is not None
        want = "reported, not judged"
    out.append(
        Criterion(
            "iv_vs_rv",
            "IV vs realised",
            ratio,
            passed,
            "no implied or realised volatility to compare"
            if ratio is None
            else f"IV {row.atm_iv:.0%} against RV {row.realised_vol:.0%} = {ratio:.2f}x; {want}",
        )
    )
    return out


def rank_rows(rows: list[Row], bias: Bias) -> list[Row]:
    """Most criteria passed first; ties broken by the IV/RV ratio in the
    direction the bias asks for, then by open interest. A row that could
    not be priced sorts last however few criteria it failed."""

    def key(row: Row):
        ratio = row.iv_rv_ratio
        if ratio is None:
            vol_key = 0.0
        else:
            vol_key = ratio if bias == "sell_premium" else (-ratio if bias == "buy_premium" else 0.0)
        return (-(row.expiry is not None), -row.passed, -vol_key, -row.open_interest)

    return sorted(rows, key=key)


async def _priced(
    service,
    symbol: str,
    req: ScreenRequest,
    today: date,
    closes: list[float],
    earnings_calendar,
    iv_store,
) -> Row:
    """One symbol's row: its expiry in the window, that chain's numbers, and
    the criteria read off them. Never raises -- a symbol that cannot be
    priced comes back as a row with a note, because a screen of sixty names
    should not fail on one of them."""
    row = Row(symbol=symbol)
    try:
        # A calendar's back leg sits past the picker's 60-day strip, so that
        # screen (and only it) asks for the full board of listed expiries.
        strip = await service.expiries(symbol, board=True) if req.strategy == "calendar" else await service.expiries(symbol)
    except Exception as exc:
        row.note = f"no expiries: {exc}"
        return row
    row.spot = strip.get("spot")
    listed = [date.fromisoformat(e["expiry"]) for e in strip.get("expiries", [])]
    expiry = pick_expiry(listed, today, req.dte_min, req.dte_max)
    if expiry is None:
        row.note = f"no listed expiry {req.dte_min}-{req.dte_max} days out"
        row.criteria = _criteria(row, req)
        return row
    row.expiry, row.dte = expiry, (expiry - today).days

    try:
        chain: Chain = await service.chain(symbol, expiry)
    except Exception as exc:
        row.note = f"no chain: {exc}"
        row.criteria = _criteria(row, req)
        return row

    row.spot = chain.spot or row.spot
    row.open_interest = sum(
        q.open_interest for r in chain.rows for q in (r.call, r.put) if q is not None and q.open_interest
    )
    row.atm_iv = atm_iv_of(chain.rows, chain.spot)
    row.realised_vol = realised_vol(closes)
    if row.atm_iv and row.realised_vol:
        row.iv_rv_ratio = row.atm_iv / row.realised_vol
    band = (req.short_delta_min, req.short_delta_max)
    row.short_put = pick_short(chain.rows, "put", band)
    row.short_call = pick_short(chain.rows, "call", band)
    if req.strategy in SPREAD_STRATEGIES:
        for leg, kind, attr in ((row.short_put, "put", "put_spread"), (row.short_call, "call", "call_spread")):
            if leg is None:
                continue
            wing = pick_wing(chain.rows, kind, leg["strike"], chain.spot)
            setattr(row, attr, vertical_of(leg, wing))
    if req.strategy == "calendar":
        # The slope needs a second expiry, so this is the one strategy that
        # costs two chain fetches a symbol. Roughly twice the front's DTE,
        # which is where a calendar's back leg usually sits.
        back = pick_expiry(listed, today, req.dte_min * 2, req.dte_max * 3)
        if back is not None and back != expiry:
            try:
                back_chain = await service.chain(symbol, back)
            except Exception:
                logger.debug("Screener: no back chain for %s %s", symbol, back, exc_info=True)
            else:
                row.back_expiry = back
                row.back_iv = atm_iv_of(back_chain.rows, back_chain.spot)
                if row.atm_iv and row.back_iv:
                    row.term_ratio = row.atm_iv / row.back_iv

    if iv_store is not None and row.atm_iv:
        try:
            rank, samples = await iv_store.rank(symbol, row.atm_iv)
            row.iv_rank = None if rank is None else round(rank.percent, 1)
            row.iv_rank_samples = samples
        except Exception:
            logger.debug("IV rank lookup failed for %s", symbol, exc_info=True)
    if earnings_calendar is not None:
        try:
            earnings = await earnings_calendar.next_earnings(symbol)
            row.earnings_date = earnings.report_date if earnings is not None else None
        except Exception:
            logger.debug("Earnings lookup failed for %s", symbol, exc_info=True)

    row.criteria = _criteria(row, req)
    return row


async def screen_underlyings(
    service,
    clients,
    req: ScreenRequest,
    *,
    today: date | None = None,
    earnings_calendar=None,
    iv_store=None,
) -> dict:
    """The screen: one row per symbol, ranked, with what every criterion
    found. One chain fetch per symbol (cached five minutes like every other
    chain in the app) and one batched bars call for all of them."""
    from app.market_data.bars import get_daily_bars_multi

    symbols = list(dict.fromkeys(s.upper() for s in req.symbols))
    if not symbols:
        raise OrderRejected("No symbols to screen", field="symbols")
    today = today or datetime.now(timezone.utc).date()

    closes: dict[str, list[float]] = {}
    try:
        # Calendar days, so a 20-session window survives weekends and holidays.
        bars = await get_daily_bars_multi(clients, symbols, lookback_days=45)
        closes = {sym.upper(): [float(b.close) for b in rows] for sym, rows in bars.items()}
    except Exception:
        logger.warning("Screener: daily bars failed, realised vol unavailable", exc_info=True)

    rows = await asyncio.gather(
        *(
            _priced(service, symbol, req, today, closes.get(symbol, []), earnings_calendar, iv_store)
            for symbol in symbols
        )
    )
    ranked = rank_rows(list(rows), req.bias)
    return {
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "strategy": req.strategy,
        "bias": req.bias,
        "criteria": {
            "dte": [req.dte_min, req.dte_max],
            "min_open_interest": req.min_open_interest,
            "max_spread_fraction": req.max_spread_fraction,
            "short_delta": [req.short_delta_min, req.short_delta_max],
            "avoid_earnings": req.avoid_earnings,
            "rich_iv_ratio": RICH_IV_RATIO,
            "cheap_iv_ratio": CHEAP_IV_RATIO,
        },
        "rows": [r.to_dict() for r in ranked],
        "disclaimer": (
            "Contract volume is not read (a day bar per contract); open interest and quoted size stand in. "
            "IV percentile appears once a symbol has 20 recorded sessions; until then the measure is implied "
            "against realised volatility. Criteria describe a chain -- they are not a view on the underlying."
        ),
    }
