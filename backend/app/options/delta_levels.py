"""Where a chosen delta sits on the chain: the put and the call strike whose
|delta| is nearest it, on the 30-60 day expiry. Drawn on the chart as two
lines -- at 16 delta they are roughly one standard deviation either side,
where a textbook iron condor puts its short strikes, and the reader can
hold them against the GEX walls without opening the chain.

Read off the chain's own deltas rather than spot +/- IV * sqrt(t), so the
put side lands further out than the call side when the smile says it
should. The strike is the listed one nearest the target, not an
interpolated price: the line is meant to mark a strike one could sell.
"""

import math
from datetime import date
from statistics import NormalDist

from app.options.chain import Chain
from app.options.chain_fetch import STRIKE_PCT_RANGE
from app.options.iv_recorder import DTE_RANGE
from app.options.screener import CHAIN_WIDTH_MAX, atm_iv_of, pick_expiry, pick_short

DEFAULT_DELTA = 0.16
# Past the target strike, in standard deviations, when the default band
# did not reach it: enough for the nearest strike on the far side to be
# in the chain, so "nearest the target" is not just "the last one fetched".
_EXTRA_SIGMA = 0.5


def _reaches(chain: Chain, kind: str, target: float) -> bool:
    """Whether the chain's furthest out-of-the-money quote on that side is
    already at or below the target |delta| -- otherwise the nearest strike
    is simply the band's edge."""
    deltas = [
        abs(q.delta)
        for row in chain.rows
        for q in ((row.call if kind == "call" else row.put),)
        if q is not None and q.delta is not None and 0 < abs(q.delta) < 1
    ]
    return bool(deltas) and min(deltas) <= target


def width_for(target: float, iv: float, dte: int) -> float:
    """The strike band, as a fraction of spot either side, that holds the
    target delta's strike plus some room beyond it."""
    z = NormalDist().inv_cdf(1 - target)
    needed = (z + _EXTRA_SIGMA) * iv * math.sqrt(max(dte, 1) / 365.0)
    return round(min(max(needed, STRIKE_PCT_RANGE), CHAIN_WIDTH_MAX), 4)


async def delta_levels(service, symbol: str, today: date, target: float = DEFAULT_DELTA) -> dict | None:
    """{expiry, dte, delta, spot, put, call} -- put/call each {strike,
    delta} or None -- or None when the symbol lists no expiry in DTE_RANGE.
    One chain fetch at the default band, a second, wider one only when the
    target lies beyond it (a volatile name, a far delta)."""
    strip = await service.expiries(symbol)
    listed = [date.fromisoformat(e["expiry"]) for e in strip.get("expiries", [])]
    expiry = pick_expiry(listed, today, *DTE_RANGE)
    if expiry is None:
        return None
    dte = (expiry - today).days
    chain: Chain = await service.chain(symbol, expiry)
    if not (_reaches(chain, "put", target) and _reaches(chain, "call", target)):
        iv = atm_iv_of(chain.rows, chain.spot)
        if iv:
            wider = width_for(target, iv, dte)
            if wider > STRIKE_PCT_RANGE:
                chain = await service.chain(symbol, expiry, wider)

    def side(kind: str) -> dict | None:
        picked = pick_short(chain.rows, kind, (target, target))
        return None if picked is None else {"strike": picked["strike"], "delta": picked["delta"]}

    return {
        "symbol": symbol,
        "expiry": expiry.isoformat(),
        "dte": dte,
        "delta": target,
        "spot": chain.spot,
        "put": side("put"),
        "call": side("call"),
    }
