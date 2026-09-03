"""A long call/put goes to Alpaca as a plain option limit order; a spread
still goes as MLEG. The preview is stubbed so no chain or account is
needed -- the request builder branch is what is under test."""

import asyncio
from datetime import date

from app.core.config import Settings
from app.options.models import ResolvedSpread, SpreadLeg, SpreadTicket
from app.options.service import OptionsService

EXPIRY = date(2026, 9, 18)


class _Trading:
    def __init__(self):
        self.requests = []

    def submit_order(self, request):
        self.requests.append(request)
        return {"id": "o-1", "status": "accepted"}


def _leg(symbol, kind, strike, side) -> SpreadLeg:
    return SpreadLeg(
        symbol=symbol, kind=kind, strike=strike, expiry=EXPIRY, side=side,
        position_intent="buy_to_open" if side == "buy" else "sell_to_open", mid=1.0,
    )


def _resolved(strategy, legs, limit) -> ResolvedSpread:
    return ResolvedSpread(
        underlying="SPY", strategy=strategy, expiry=EXPIRY, qty=2, direction="debit" if limit > 0 else "credit",
        legs=legs, spot=748.0, width=0.0, net_mid=abs(limit), net_natural=None, limit_price=abs(limit),
        alpaca_limit_price=limit, max_profit=None, max_loss=abs(limit) * 200, breakevens=[], collateral=abs(limit) * 200,
        options_buying_power=10_000.0, dte=16, options_level=2, account="paper", client_order_id="cid-1",
    )


def _service(monkeypatch, resolved) -> tuple[OptionsService, _Trading]:
    trading = _Trading()
    settings = Settings(alpaca_api_key_id="k", alpaca_api_secret_key="s", trading_enabled=True)
    service = OptionsService(clients=None, settings=settings, chain_cache=object())  # type: ignore[arg-type]
    monkeypatch.setattr(OptionsService, "_trading", property(lambda self: trading))

    async def preview(self, ticket):
        return resolved

    monkeypatch.setattr(OptionsService, "preview", preview)
    return service, trading


def test_long_call_is_a_plain_option_limit_order(monkeypatch):
    from alpaca.trading.enums import OrderSide, PositionIntent

    resolved = _resolved("long_call", [_leg("SPY260918C00750000", "call", 750, "buy")], 2.5)
    service, trading = _service(monkeypatch, resolved)
    ticket = SpreadTicket(underlying="SPY", strategy="long_call", expiry=EXPIRY, qty=2, long_strike=750)
    order = asyncio.run(service.submit(ticket))
    assert order["id"] == "o-1"
    (request,) = trading.requests
    assert request.symbol == "SPY260918C00750000" and request.legs is None and request.order_class is None
    assert request.qty == 2 and request.limit_price == 2.5
    assert request.side == OrderSide.BUY and request.position_intent == PositionIntent.BUY_TO_OPEN
    assert request.client_order_id == "cid-1"


def test_a_spread_still_goes_as_mleg_and_a_long_needs_only_level_two(monkeypatch):
    from alpaca.trading.enums import OrderClass

    from app.trading.errors import OptionsNotApproved

    legs = [_leg("SPY260918P00740000", "put", 740, "buy"), _leg("SPY260918P00745000", "put", 745, "sell")]
    resolved = _resolved("bull_put", legs, -1.2)
    service, trading = _service(monkeypatch, resolved)
    ticket = SpreadTicket(underlying="SPY", strategy="bull_put", expiry=EXPIRY, qty=2, long_strike=740, short_strike=745)
    try:
        asyncio.run(service.submit(ticket))
    except OptionsNotApproved as exc:
        assert "level 3" in exc.message and "level 2" in exc.message
    else:
        raise AssertionError("a level-2 account must not place a spread")
    assert trading.requests == []

    resolved.options_level = 3
    asyncio.run(service.submit(ticket))
    (request,) = trading.requests
    assert request.order_class == OrderClass.MLEG and len(request.legs) == 2 and request.limit_price == -1.2


def test_an_uncovered_write_is_refused_before_the_broker(monkeypatch):
    from app.options.models import Coverage, TicketLeg
    from app.trading.errors import OrderRejected

    resolved = _resolved("covered_call", [_leg("SPY260918C00760000", "call", 760, "sell")], -1.5)
    resolved.coverage = Coverage(kind="shares", have=0, need=200, ok=False)
    resolved.options_level = 1
    service, trading = _service(monkeypatch, resolved)
    ticket = SpreadTicket(
        underlying="SPY", strategy="covered_call", expiry=EXPIRY, qty=2,
        legs=[TicketLeg(kind="call", strike=760, side="sell")],
    )
    try:
        asyncio.run(service.submit(ticket))
    except OrderRejected as exc:
        assert "not covered" in exc.message and "200 shares" in exc.message
    else:
        raise AssertionError("an uncovered call must not reach the broker")
    assert trading.requests == []

    # Covered: a plain sell-to-open order goes out.
    resolved.coverage = Coverage(kind="shares", have=200, need=200, ok=True)
    asyncio.run(service.submit(ticket))
    (request,) = trading.requests
    assert request.symbol == "SPY260918C00760000" and request.legs is None
