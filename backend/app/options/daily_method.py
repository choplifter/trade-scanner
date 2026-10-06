"""The daily method: sell put spreads when implied volatility is rich, with a
market filter and hard risk rules -- the rules that held up in the backtest
(app.options.signal_backtest, variant "vol" with market_filter), applied to
today's real chains and the account's real positions.

Run once a day after the close. It proposes; the user places. Three parts:

1. The market light. Red while SPY is below its 200-day average or its ATM
   IV runs more than 20 % above its 20-day mean -- no new premium sold.
2. Exits for the bull put spreads the account holds: half the credit
   earned, a loss of twice the credit, or 21 days left.
3. Entries across the watchlist, richest first: implied rich (IV/RV >= 1.2
   or IV rank >= 60, and neither cheap), no position in the symbol or its
   correlation group, a bull put spread about 45 days out with the short
   put near 0.30 delta and the long one about 3 % of spot below it --
   narrowed until one contract risks at most 1 % of equity -- and all open
   risk together at most 5 % of equity.

The decisions are signal_backtest's own functions (regime, risk_off,
group_of, the constants), so what was tested is what runs. What differs is
only what a backtest cannot have: a listed expiry instead of exactly 45
days, real strikes and deltas, real bid/ask -- the size is set on the
natural credit (the worse fill), the ticket's limit on the mid.

Open positions proposed for closing still count against the caps today:
the close is a proposal too, and until it fills the risk is there.

Pure: chains, closes, IVs and positions in, proposals out. The router
fetches.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

from app.options.chain import Chain
from app.options.signal_backtest import (
    CREDIT_STOP,
    CREDIT_TAKE,
    MAX_TOTAL_RISK,
    MIN_IV,
    RISK_PER_TRADE,
    SHORT_DELTA,
    TIME_STOP_DTE,
    TREND_SMA,
    VOL_SPIKE,
    VOL_SPIKE_WINDOW,
    WIDTH_PCT,
    group_of,
    realized,
    regime,
    risk_off,
)

TARGET_DTE_RANGE = (30, 60)  # the listed expiry nearest the middle (45)


@dataclass
class HeldSpread:
    """A bull put spread the account holds, as the method reads it."""

    id: str
    symbol: str
    expiry: date
    dte: int
    qty: int
    short_strike: float
    long_strike: float
    credit: float  # per share, received
    unrealized_pl: float  # dollars, whole position
    legs: list[tuple[str, int]]  # (occ, signed qty) for a close ticket

    @property
    def max_loss(self) -> float:
        return (self.short_strike - self.long_strike - self.credit) * 100 * self.qty


@dataclass
class Outcome:
    market: dict
    exits: list[dict] = field(default_factory=list)
    entries: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    open_risk: float = 0.0

    def skip(self, symbol: str, reason: str) -> None:
        self.skipped.append({"symbol": symbol, "reason": reason})


def market_light(spy_closes: list[float], spy_ivs: list[float]) -> dict:
    """The filter's verdict with the numbers behind it."""
    sma = sum(spy_closes[-TREND_SMA:]) / TREND_SMA if len(spy_closes) >= TREND_SMA else None
    mean = (
        sum(spy_ivs[-VOL_SPIKE_WINDOW - 1 : -1]) / VOL_SPIKE_WINDOW if len(spy_ivs) > VOL_SPIKE_WINDOW else None
    )
    reason = risk_off(spy_closes, spy_ivs)
    return {
        "status": "red" if reason else "green",
        "reason": reason,
        "spy_close": spy_closes[-1] if spy_closes else None,
        "spy_sma200": None if sma is None else round(sma, 2),
        "spy_iv": spy_ivs[-1] if spy_ivs else None,
        "spy_iv_mean20": None if mean is None else round(mean, 4),
        "spy_iv_limit": None if mean is None else round(mean * VOL_SPIKE, 4),
        "enough_history": sma is not None and mean is not None,
    }


def exit_for(held: HeldSpread) -> str | None:
    """Why a held spread should be closed today, or None."""
    credit_dollars = held.credit * 100 * held.qty
    if credit_dollars > 0 and held.unrealized_pl >= CREDIT_TAKE * credit_dollars:
        return f"target: {held.unrealized_pl:,.0f} of {credit_dollars:,.0f} credit earned (half is the target)"
    if credit_dollars > 0 and -held.unrealized_pl >= CREDIT_STOP * credit_dollars:
        return f"stop: loss {-held.unrealized_pl:,.0f} reached twice the {credit_dollars:,.0f} credit"
    if held.dte <= TIME_STOP_DTE:
        return f"time: {held.dte} days left, {TIME_STOP_DTE} is the limit"
    return None


def _bid(q) -> float | None:
    return q.bid if q is not None and q.bid and q.bid > 0 else None


def put_spread(chain: Chain, equity: float) -> dict | None:
    """The bull put spread on this chain: short put nearest 0.30 delta (bid
    quotes only), long put about WIDTH_PCT of spot below, narrowed a strike
    at a time until one contract fits RISK_PER_TRADE on the natural credit.
    None when no pair fits or the market pays nothing."""
    spot = chain.spot
    puts = [r for r in chain.rows if r.put is not None and _bid(r.put) and r.put.ask and r.put.delta is not None]
    candidates = [r for r in puts if r.strike < spot]
    if not candidates:
        return None
    short_row = min(candidates, key=lambda r: abs(abs(r.put.delta) - SHORT_DELTA))
    lower = sorted((r for r in chain.rows if r.put is not None and r.put.ask and r.strike < short_row.strike),
                   key=lambda r: r.strike)
    if not lower:
        return None
    target_long = short_row.strike - spot * WIDTH_PCT
    # From the target width up toward the short strike: widest first, each
    # step one strike narrower.
    start = min(range(len(lower)), key=lambda i: abs(lower[i].strike - target_long))
    budget = equity * RISK_PER_TRADE
    for row in lower[start:]:
        width = short_row.strike - row.strike
        natural = short_row.put.bid - row.put.ask
        mid = (short_row.put.mid or 0) - (row.put.mid or 0)
        if natural <= 0 or mid <= 0:
            continue
        loss_per = (width - natural) * 100
        if loss_per <= 0 or loss_per > budget:
            continue
        contracts = int(budget // loss_per)
        return {
            "short_strike": short_row.strike,
            "long_strike": row.strike,
            "short_delta": round(short_row.put.delta, 3),
            "width": round(width, 2),
            "credit_mid": round(mid, 2),
            "credit_natural": round(natural, 2),
            "contracts": contracts,
            "max_loss": round(loss_per * contracts, 2),
        }
    return None


def evaluate(
    *,
    today: date,
    equity: float,
    symbols: list[str],
    closes: dict[str, list[float]],
    iv_history: dict[str, list[float]],
    chains: dict[str, Chain],
    atm_iv: dict[str, float | None],
    spy_closes: list[float],
    spy_ivs: list[float],
    held: list[HeldSpread],
) -> Outcome:
    """Today's proposals. `iv_history` holds each symbol's earlier readings
    (not today's); `atm_iv` today's, read on `chains[symbol]`."""
    out = Outcome(market=market_light(spy_closes, spy_ivs))

    for h in held:
        reason = exit_for(h)
        if reason:
            out.exits.append({
                "id": h.id, "symbol": h.symbol, "expiry": h.expiry.isoformat(), "dte": h.dte, "qty": h.qty,
                "strikes": [h.short_strike, h.long_strike], "credit": h.credit, "pnl": round(h.unrealized_pl, 2),
                "reason": reason,
                "close": {"legs": [{"symbol": occ, "qty": q} for occ, q in h.legs], "qty": h.qty},
            })

    open_risk = sum(max(h.max_loss, 0.0) for h in held)
    held_symbols = {h.symbol for h in held}
    held_groups = {group_of(h.symbol) for h in held}

    readings = []
    for symbol in symbols:
        iv = atm_iv.get(symbol)
        rv = realized(closes.get(symbol, []))
        past = iv_history.get(symbol, [])[-252:]
        lo, hi = (min(past), max(past)) if len(past) >= 60 else (None, None)
        rank = 100 * (iv - lo) / (hi - lo) if iv and lo is not None and hi > lo else None
        readings.append((symbol, iv, rv, rank, past))
    # Richest first: when the caps leave room for only some, the most
    # expensive premium gets it.
    readings.sort(key=lambda r: -(r[1] / r[2]) if r[1] and r[2] else 0.0)

    for symbol, iv, rv, rank, past in readings:
        if iv is None:
            out.skip(symbol, "no at-the-money IV on a 30-60 day expiry")
            continue
        if iv < MIN_IV:
            out.skip(symbol, f"IV {iv:.1%} under the {MIN_IV:.0%} floor: the premium cannot carry the bid/ask")
            continue
        verdict = regime(iv, rv, past)
        if verdict != "rich":
            ratio = f"IV/RV {iv / rv:.2f}" if rv else "no realised vol"
            rank_txt = f", rank {rank:.0f} %" if rank is not None else ""
            out.skip(symbol, f"premium not rich ({ratio}{rank_txt})")
            continue
        if out.market["status"] == "red":
            out.skip(symbol, f"market light red: {out.market['reason']}")
            continue
        if symbol in held_symbols:
            out.skip(symbol, "already holds a position")
            continue
        if group_of(symbol) in held_groups:
            out.skip(symbol, f"group '{group_of(symbol)}' already has a position")
            continue
        chain = chains.get(symbol)
        spread = put_spread(chain, equity) if chain is not None else None
        if spread is None:
            out.skip(symbol, "no put spread on the chain fits 1 % of equity at a positive credit")
            continue
        if open_risk + spread["max_loss"] > MAX_TOTAL_RISK * equity:
            out.skip(symbol, f"total risk cap: {open_risk:,.0f} open + {spread['max_loss']:,.0f} > 5 % of equity")
            continue
        open_risk += spread["max_loss"]
        held_symbols.add(symbol)
        held_groups.add(group_of(symbol))
        out.entries.append({
            "symbol": symbol,
            "group": group_of(symbol),
            "expiry": chain.expiry.isoformat(),
            "dte": (chain.expiry - today).days,
            "spot": chain.spot,
            "iv": round(iv, 4),
            "realized_vol": None if rv is None else round(rv, 4),
            "iv_rv": None if not rv else round(iv / rv, 2),
            "iv_rank": None if rank is None else round(rank, 1),
            **spread,
            "ticket": {
                "underlying": symbol,
                "strategy": "bull_put",
                "expiry": chain.expiry.isoformat(),
                "qty": spread["contracts"],
                "long_strike": spread["long_strike"],
                "short_strike": spread["short_strike"],
                "limit_price": spread["credit_mid"],
            },
        })
    out.open_risk = round(open_risk, 2)
    return out


def held_from_groups(groups, today: date) -> list[HeldSpread]:
    """The account's bull put spreads, out of SpreadGroup rows."""
    out = []
    for g in groups:
        if getattr(g, "strategy", None) != "bull_put" or getattr(g, "broken", False):
            continue
        puts = sorted((leg for leg in g.legs if leg.kind == "put"), key=lambda leg: leg.strike)
        if len(puts) != 2 or not (puts[0].qty > 0 > puts[1].qty):
            continue
        credit = -float(g.net_entry)
        if not math.isfinite(credit) or credit <= 0:
            continue
        out.append(HeldSpread(
            id=g.id, symbol=g.underlying, expiry=g.expiry, dte=(g.expiry - today).days, qty=g.qty,
            short_strike=puts[1].strike, long_strike=puts[0].strike, credit=credit,
            unrealized_pl=float(g.unrealized_pl), legs=[(leg.symbol, leg.qty) for leg in puts],
        ))
    return out
