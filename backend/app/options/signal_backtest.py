"""Backtest two daily signal methods traded as defined-risk option spreads.

The question: does a classic daily signal -- a Donchian breakout (the
Turtles' entry) or a Bollinger-band dip in an uptrend -- carry an edge
once it is traded the way an options account would trade it, with the
structure chosen by the volatility regime and hard risk rules around it?

Each session, after the close:

1. Exits first. An open spread is marked at the day's IV and closed on its
   profit target, its stop, its time stop (21 days left -- the last weeks
   are where gamma takes over, Natenberg ch. 23), or when its signal turns.
2. Entries. A symbol with no open position and a signal gets a spread
   picked by the regime: implied rich (IV/RV >= 1.2 or IV rank >= 60) ->
   sell the vertical in the signal's direction; implied cheap (IV/RV <=
   0.95 or rank <= 30) -> buy it; in between, nothing.
3. Risk: every spread's maximum loss is at most RISK_PER_TRADE of equity
   (contracts follow from that), all open maximum losses together at most
   MAX_TOTAL_RISK, and at most one open position per correlation group
   (SPY, QQQ and IWM fall together; counting them as three would triple
   one bet).

What the prices are: Black-Scholes at the day's at-the-money IV -- the
real one, from the IV history (option_iv_history: our recorder and the
Barchart seed), flat across strikes, no skew, no dividends, an expiry
assumed to exist DTE days out. Fills cross a bid/ask of SPREAD_FRAC of
each leg's price (at least MIN_TICK). So it says whether the signal and
the rules hold up at realistic volatility levels -- not what a real
chain, skew and liquidity would have paid. The response says so.

Pure: closes and IVs in, trades and an equity curve out. The I/O is in
the router.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

from app.options.payoff import bs_price

# "vol" is the control: no price signal at all, a put spread sold whenever
# implied volatility is rich. If the signals add nothing over it, they are
# decoration on the volatility risk premium.
Variant = Literal["trend", "band", "vol"]

# Signals.
TREND_SMA = 200
DONCHIAN_ENTRY = 20
DONCHIAN_EXIT = 10
BOLLINGER_WINDOW = 20
BOLLINGER_WIDTH = 2.0
RV_WINDOW = 20

# Regime (the Screener's bands).
RICH_IV_RV, CHEAP_IV_RV = 1.20, 0.95
RICH_RANK, CHEAP_RANK = 60.0, 30.0
RANK_MIN_SAMPLES = 60

# Structure.
TARGET_DTE = 45
TIME_STOP_DTE = 21
SHORT_DELTA = 0.30  # credit spreads: the short strike
WIDTH_PCT = 0.03  # vertical width as a share of spot

# Exits.
CREDIT_TAKE = 0.50  # close when half the credit is earned
CREDIT_STOP = 2.0  # close when the loss reaches twice the credit
DEBIT_TAKE = 1.00  # close at +100 %
DEBIT_STOP = 0.50  # close at -50 %

# Risk.
RISK_PER_TRADE = 0.01
MAX_TOTAL_RISK = 0.05
SPREAD_FRAC = 0.03
MIN_TICK = 0.02

GROUPS = {
    "equity": {"SPY", "QQQ", "IWM", "DIA", "XLK", "SMH"},
    "rates": {"TLT", "IEF", "LQD", "HYG"},
    "metals": {"GLD", "SLV"},
    "energy": {"USO", "UNG", "XLE"},
}


def group_of(symbol: str) -> str:
    for name, members in GROUPS.items():
        if symbol in members:
            return name
    return symbol


def strike_step(spot: float) -> float:
    return 0.5 if spot < 50 else 1.0 if spot < 200 else 5.0


def round_strike(x: float, step: float) -> float:
    return round(round(x / step) * step, 4)


# --- signals ------------------------------------------------------------------


def _sma(values: list[float], n: int) -> float | None:
    return sum(values[-n:]) / n if len(values) >= n else None


def signal(variant: Variant, closes: list[float]) -> Literal["bull", "bear"] | None:
    """Today's entry signal from closes up to and including today."""
    if variant == "vol":
        return "bull" if len(closes) > RV_WINDOW else None
    if len(closes) < TREND_SMA + 1:
        return None
    price, trend = closes[-1], _sma(closes, TREND_SMA)
    if variant == "trend":
        prior = closes[-DONCHIAN_ENTRY - 1 : -1]
        if price > max(prior) and price > trend:
            return "bull"
        if price < min(prior) and price < trend:
            return "bear"
        return None
    window = closes[-BOLLINGER_WINDOW:]
    mid = sum(window) / len(window)
    sd = math.sqrt(sum((c - mid) ** 2 for c in window) / len(window))
    if price < mid - BOLLINGER_WIDTH * sd and price > trend:
        return "bull"
    if price > mid + BOLLINGER_WIDTH * sd and price < trend:
        return "bear"
    return None


def signal_exit(variant: Variant, direction: str, closes: list[float]) -> bool:
    """Whether the signal that opened a position has run its course. The
    control has none: its positions leave on target, stop or time."""
    if variant == "vol":
        return False
    price = closes[-1]
    if variant == "trend":
        prior = closes[-DONCHIAN_EXIT - 1 : -1]
        return price < min(prior) if direction == "bull" else price > max(prior)
    mid = _sma(closes, BOLLINGER_WINDOW)
    return price >= mid if direction == "bull" else price <= mid


def realized(closes: list[float], n: int = RV_WINDOW) -> float | None:
    if len(closes) < n + 1:
        return None
    rets = [math.log(b / a) for a, b in zip(closes[-n - 1 : -1], closes[-n:])]
    return math.sqrt(sum(r * r for r in rets) / n * 252)


def regime(iv: float, rv: float | None, iv_history: list[float]) -> Literal["rich", "cheap"] | None:
    ratio = iv / rv if rv else None
    rank = None
    if len(iv_history) >= RANK_MIN_SAMPLES:
        lo, hi = min(iv_history), max(iv_history)
        rank = 100 * (iv - lo) / (hi - lo) if hi > lo else None
    rich = (ratio is not None and ratio >= RICH_IV_RV) or (rank is not None and rank >= RICH_RANK)
    cheap = (ratio is not None and ratio <= CHEAP_IV_RV) or (rank is not None and rank <= CHEAP_RANK)
    if rich and not cheap:
        return "rich"
    if cheap and not rich:
        return "cheap"
    return None


# --- pricing ------------------------------------------------------------------


def _years(today: date, expiry: date) -> float:
    return max((expiry - today).days, 0) / 365


def _cost(price: float) -> float:
    """Half the bid/ask of one leg: what crossing it costs against its mid."""
    return max(MIN_TICK, SPREAD_FRAC * price) / 2


@dataclass
class Spread:
    symbol: str
    direction: str  # bull / bear
    kind: str  # credit / debit
    option: str  # put / call
    long_strike: float
    short_strike: float
    expiry: date
    contracts: int
    entry: float  # per share: credit received (credit) or debit paid (debit)
    max_loss: float  # dollars for the whole position
    opened: date
    variant: str

    def mid_value(self, spot: float, iv: float, today: date) -> float:
        """What closing would cost (credit) or bring (debit) at mid, per share."""
        t = _years(today, self.expiry)
        short = bs_price(self.option, spot, self.short_strike, t, iv)
        long = bs_price(self.option, spot, self.long_strike, t, iv)
        return short - long if self.kind == "credit" else long - short

    def close_price(self, spot: float, iv: float, today: date) -> float:
        """Per share, crossing both legs: a credit spread is bought back above
        mid, a debit spread sold below it."""
        t = _years(today, self.expiry)
        legs = [bs_price(self.option, spot, k, t, iv) for k in (self.short_strike, self.long_strike)]
        cross = sum(_cost(p) for p in legs)
        mid = self.mid_value(spot, iv, today)
        return max(0.0, mid + cross) if self.kind == "credit" else max(0.0, mid - cross)

    def pnl(self, close: float) -> float:
        per_share = self.entry - close if self.kind == "credit" else close - self.entry
        return per_share * 100 * self.contracts


def _delta_strike(option: str, spot: float, years: float, iv: float, delta: float, step: float) -> float:
    """The strike whose Black-Scholes delta is `delta` (absolute), on the grid."""
    sd = iv * math.sqrt(years)
    z = _inv_norm(delta)
    # Call delta N(d1) = delta -> d1 = z; put |delta| = N(-d1) -> d1 = -z.
    d1 = z if option == "call" else -z
    strike = spot * math.exp(-(d1 * sd) + 0.5 * sd * sd)
    return round_strike(strike, step)


def _inv_norm(p: float) -> float:
    """Inverse standard normal CDF (Acklam's rational approximation)."""
    a = [-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02, 1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00]
    b = [-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02, 6.680131188771972e01, -1.328068155288572e01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00, -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00, 3.754408661907416e00]
    lo = 0.02425
    if p < lo:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > 1 - lo:
        return -_inv_norm(1 - p)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def build_spread(
    symbol: str, direction: str, kind: str, spot: float, iv: float, today: date, equity: float, variant: str
) -> Spread | None:
    """The vertical for a signal and a regime, sized to RISK_PER_TRADE.

    The width starts at WIDTH_PCT of spot and narrows a strike at a time
    until one contract fits the risk budget: on a $770 SPY a 3 % vertical
    risks ~$1,600 a contract, more than 1 % of a $100k account, and would
    otherwise never be traded at all. None when not even the narrowest
    fits, or the market would not pay for it."""
    step = strike_step(spot)
    expiry = today + timedelta(days=TARGET_DTE)
    t = _years(today, expiry)
    budget = equity * RISK_PER_TRADE
    width = max(step, round_strike(spot * WIDTH_PCT, step))
    while width >= step:
        built = _vertical(direction, kind, spot, iv, t, step, width)
        if built is None:
            return None
        option, short, long, entry, loss_per = built
        if loss_per <= budget:
            contracts = int(budget // loss_per)
            return Spread(symbol, direction, kind, option, long, short, expiry, contracts, round(entry, 4),
                          round(loss_per * contracts, 2), today, variant)
        width = round_strike(width - step, step)
    return None


def _vertical(direction: str, kind: str, spot: float, iv: float, t: float, step: float, width: float):
    """(option, short, long, entry per share, max loss per contract), or None
    when the market would not pay for the spread."""
    if kind == "credit":
        option = "put" if direction == "bull" else "call"
        short = _delta_strike(option, spot, t, iv, SHORT_DELTA, step)
        long = short - width if option == "put" else short + width
    else:
        option = "call" if direction == "bull" else "put"
        long = round_strike(spot, step)
        short = long + width if option == "call" else long - width
    prices = {k: bs_price(option, spot, k, t, iv) for k in (short, long)}
    cross = _cost(prices[short]) + _cost(prices[long])
    if kind == "credit":
        entry = prices[short] - prices[long] - cross
        if entry <= MIN_TICK:
            return None
        return option, short, long, entry, (width - entry) * 100
    entry = prices[long] - prices[short] + cross
    if entry <= 0 or entry >= width:
        return None
    return option, short, long, entry, entry * 100


# --- the walk -----------------------------------------------------------------


@dataclass
class Result:
    variant: str
    trades: list[dict] = field(default_factory=list)
    curve: list[tuple[str, float]] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


def walk(
    variant: Variant,
    closes: dict[str, list[tuple[date, float]]],
    ivs: dict[str, dict[date, float]],
    *,
    starting_equity: float = 100_000.0,
    start: date | None = None,
    end: date | None = None,
) -> Result:
    """Run one signal method over the sessions where IV is known, between
    `start` and `end` when given -- so rules set on one stretch can be
    checked on another they never saw."""
    result = Result(variant)
    days = sorted(
        d for d in {d for series in ivs.values() for d in series}
        if (start is None or d >= start) and (end is None or d <= end)
    )
    by_day = {s: dict(series) for s, series in closes.items()}
    history = {s: [c for _, c in series] for s, series in closes.items()}
    index = {s: {d: i for i, (d, _) in enumerate(series)} for s, series in closes.items()}
    cash = starting_equity
    open_: list[Spread] = []

    for today in days:
        # Exits.
        still: list[Spread] = []
        for pos in open_:
            iv = ivs.get(pos.symbol, {}).get(today)
            spot = by_day.get(pos.symbol, {}).get(today)
            if iv is None or spot is None:
                still.append(pos)
                continue
            past = history[pos.symbol][: index[pos.symbol][today] + 1]
            close = pos.close_price(spot, iv, today)
            pnl = pos.pnl(close)
            risk_basis = pos.entry * 100 * pos.contracts
            reason = None
            if pos.kind == "credit":
                if pnl >= CREDIT_TAKE * risk_basis:
                    reason = "target"
                elif -pnl >= CREDIT_STOP * risk_basis:
                    reason = "stop"
            else:
                if pnl >= DEBIT_TAKE * risk_basis:
                    reason = "target"
                elif -pnl >= DEBIT_STOP * risk_basis:
                    reason = "stop"
            if reason is None and (pos.expiry - today).days <= TIME_STOP_DTE:
                reason = "time"
            if reason is None and signal_exit(variant, pos.direction, past):
                reason = "signal"
            if reason is None:
                still.append(pos)
                continue
            cash += pnl
            result.trades.append({
                "symbol": pos.symbol, "direction": pos.direction, "kind": pos.kind, "option": pos.option,
                "strikes": [pos.short_strike, pos.long_strike], "contracts": pos.contracts,
                "opened": pos.opened.isoformat(), "closed": today.isoformat(), "days": (today - pos.opened).days,
                "entry": pos.entry, "exit": round(close, 4), "pnl": round(pnl, 2), "max_loss": pos.max_loss,
                "reason": reason,
            })
        open_ = still

        # Entries.
        equity = cash
        for symbol in sorted(ivs):
            iv = ivs[symbol].get(today)
            if iv is None or today not in index.get(symbol, {}):
                continue
            if any(p.symbol == symbol for p in open_):
                continue
            past = history[symbol][: index[symbol][today] + 1]
            direction = signal(variant, past)
            if direction is None:
                continue
            iv_past = [v for d, v in sorted(ivs[symbol].items()) if d < today][-252:]
            kind = {"rich": "credit", "cheap": "debit"}.get(regime(iv, realized(past), iv_past) or "")
            if variant == "vol" and kind != "credit":
                continue  # the control only ever sells rich premium
            if kind is None:
                result.skip("regime in between")
                continue
            if any(group_of(p.symbol) == group_of(symbol) for p in open_):
                result.skip("group already open")
                continue
            spread = build_spread(symbol, direction, kind, past[-1], iv, today, equity, variant)
            if spread is None:
                result.skip("does not fit the risk per trade")
                continue
            if sum(p.max_loss for p in open_) + spread.max_loss > MAX_TOTAL_RISK * equity:
                result.skip("total risk cap")
                continue
            open_.append(spread)

        # Mark to market for the curve.
        marked = cash
        for pos in open_:
            iv = ivs.get(pos.symbol, {}).get(today)
            spot = by_day.get(pos.symbol, {}).get(today)
            if iv is not None and spot is not None:
                marked += pos.pnl(pos.close_price(spot, iv, today))
        result.curve.append((today.isoformat(), round(marked, 2)))
    return result


def summarize(result: Result, starting_equity: float) -> dict:
    trades = result.trades
    wins = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [t["pnl"] for t in trades if t["pnl"] <= 0]
    peak, drawdown = starting_equity, 0.0
    for _, value in result.curve:
        peak = max(peak, value)
        drawdown = max(drawdown, (peak - value) / peak if peak else 0.0)
    final = result.curve[-1][1] if result.curve else starting_equity
    by = lambda key: {  # noqa: E731
        k: {"trades": len(g), "pnl": round(sum(t["pnl"] for t in g), 2)}
        for k in sorted({t[key] for t in trades})
        for g in [[t for t in trades if t[key] == k]]
    }
    return {
        "variant": result.variant,
        "trades": len(trades),
        "win_rate": round(len(wins) / len(trades), 3) if trades else None,
        "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
        "profit_factor": round(sum(wins) / -sum(losses), 2) if losses and sum(losses) < 0 else None,
        "total_pnl": round(final - starting_equity, 2),
        "return_pct": round(100 * (final / starting_equity - 1), 2),
        "max_drawdown_pct": round(100 * drawdown, 2),
        "by_kind": by("kind"),
        "by_reason": by("reason"),
        "by_symbol": by("symbol"),
        "skipped": result.skipped,
    }
