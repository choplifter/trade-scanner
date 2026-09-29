"""The chain a ticket is resolved against (OptionsService._chains_for).

The chain is polled every fifteen seconds, so it is fetched narrow: ±10 %
of spot. An iron condor's wings are not within ±10 % of anything. Seen on
MU at 1,070 with a 600/750 -- 1,350/1,500 condor: every strike listed and
tradable at Alpaca, and the preview answered "No put at strike 600",
because the chain it looked in stopped at 963.

The width the reader picks in the widget cannot fix this. This is the
server resolving a ticket, and it has the strikes in front of it.
"""

import asyncio
from datetime import date, datetime, timezone

import pytest

from app.core.config import Settings
from app.options.chain import Chain, LegQuote, StrikeRow
from app.options.models import SpreadTicket
from app.options.occ import format_occ
from app.options.service import OptionsService

EXPIRY = date(2026, 10, 23)
NOW = datetime(2026, 9, 29, 14, 30, tzinfo=timezone.utc)
SPOT = 1070.0
# Every strike MU lists for this expiry; the source below serves the band
# around spot out of it, the way Alpaca does.
WIDE = [round(k, 1) for k in range(535, 1605, 5)]


def _quote(kind: str, strike: float) -> LegQuote:
    mid = round(max(0.2, 40.0 - abs(strike - SPOT) * 0.03), 2)
    return LegQuote(
        symbol=format_occ("MU", EXPIRY, kind, strike), strike=strike, kind=kind, expiry=EXPIRY,
        bid=round(mid - 0.05, 2), ask=round(mid + 0.05, 2), mid=mid, last=mid, bid_size=5, ask_size=5,
        delta=0.3 if kind == "call" else -0.3, gamma=0.01, theta=-0.05, iv=0.5,
        open_interest=100, tradable=True,
    )


def _chain(strikes: list[float], low: float | None, high: float | None) -> Chain:
    return Chain(
        underlying="MU", expiry=EXPIRY, spot=SPOT, feed="opra", as_of=NOW,
        strike_low=low, strike_high=high,
        rows=[StrikeRow(strike=k, call=_quote("call", k), put=_quote("put", k)) for k in strikes],
    )


class _Source:
    """Alpaca's behaviour in miniature: a band around spot, and only the
    strikes inside it."""

    def __init__(self) -> None:
        self.widths: list[float | None] = []

    async def chain(self, underlying: str, expiry: date, width: float | None = None) -> Chain:
        self.widths.append(width)
        band = width if width is not None else 0.10
        low, high = SPOT * (1 - band), SPOT * (1 + band)
        return _chain([k for k in WIDE if low <= k <= high], round(low, 2), round(high, 2))


def _service(source: _Source) -> OptionsService:
    settings = Settings(alpaca_api_key_id="k", alpaca_api_secret_key="s")
    service = OptionsService(clients=None, settings=settings, chain_cache=object(), account="paper")  # type: ignore[arg-type]
    service._source = source  # type: ignore[attr-defined]
    return service


def _condor(*, long_put=600.0, short_put=750.0, short_call=1350.0, long_call=1500.0) -> SpreadTicket:
    return SpreadTicket(
        underlying="MU", strategy="iron_condor", expiry=EXPIRY, qty=1,
        put_long_strike=long_put, put_short_strike=short_put,
        call_short_strike=short_call, call_long_strike=long_call,
    )


def _chains(ticket: SpreadTicket, source: _Source) -> dict:
    return asyncio.run(_service(source)._chains_for(ticket))


def test_a_condor_reaching_past_the_default_band_gets_a_chain_that_holds_it():
    source = _Source()
    chain = _chains(_condor(), source)[EXPIRY]
    for kind, strike in (("put", 600.0), ("put", 750.0), ("call", 1350.0), ("call", 1500.0)):
        assert chain.quote(kind, strike) is not None, f"{kind} {strike} missing"


def test_the_second_fetch_reaches_past_the_furthest_leg_not_exactly_to_it():
    """A leg sitting on the very edge of the band has no neighbour to roll
    to, and rounding at the boundary is how a strike goes missing again."""
    source = _Source()
    chain = _chains(_condor(), source)[EXPIRY]
    assert chain.strike_low is not None and chain.strike_low < 600.0
    assert chain.strike_high is not None and chain.strike_high > 1500.0


def test_a_ticket_inside_the_default_band_is_fetched_once():
    """The common case must not pay for the rare one: a vertical near the
    money resolves out of the narrow chain, and no second call is made."""
    source = _Source()
    near = SpreadTicket(
        underlying="MU", strategy="bull_put", expiry=EXPIRY, qty=1, long_strike=1000.0, short_strike=1020.0
    )
    _chains(near, source)
    assert source.widths == [None], "a resolvable chain must not be fetched twice"


def test_the_widened_fetch_asks_for_the_reach_its_own_strikes_need():
    source = _Source()
    _chains(_condor(), source)
    assert len(source.widths) == 2 and source.widths[0] is None
    # 600 is 44 % below 1,070; the ask is a tenth beyond that.
    assert source.widths[1] == pytest.approx(0.44 * 1.1, rel=0.02)


def test_the_band_never_exceeds_the_whole_spot():
    """A strike more than 100 % from spot would ask Alpaca for strikes at or
    below zero; strike_band clamps, and the request stays sane."""
    source = _Source()
    _chains(_condor(long_put=5.0), source)
    assert source.widths[1] <= 1.0


def test_a_strike_that_does_not_exist_anywhere_still_fails_rather_than_looping():
    """Widening is not a way to invent a contract. A strike off the board
    is fetched for once and then left to resolve_legs to refuse."""
    source = _Source()
    chain = _chains(_condor(long_put=602.5), source)[EXPIRY]
    assert chain.quote("put", 602.5) is None
    assert len(source.widths) == 2, "one widened attempt, not a retry loop"
