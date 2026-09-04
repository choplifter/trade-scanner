"""ReplayOptionsEngine over synthetic contracts and minute bars -- no
client, the same duck-typed bars test_replay_engine.py uses."""

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from alpaca.trading.enums import AssetStatus

from app.options.chain import ContractMeta
from app.options.occ import format_occ
from app.options.pricing import option_slippage
from app.options.quote_source import ReplayQuoteSource
from app.replay.options_engine import ReplayOptionsEngine, day_of, session_window, synthetic_quote, BarQuote
from app.services.market_clock import ET

DAY = date(2026, 8, 12)
TODAY = date(2026, 9, 4)
EXPIRY_PAST = date(2026, 8, 14)  # expired by "today"
EXPIRY_FUTURE = date(2026, 9, 18)  # still listed


@dataclass
class _Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


def _et(day: date, hh: int, mm: int) -> datetime:
    return datetime.combine(day, time(hh, mm), tzinfo=ET).astimezone(timezone.utc)


def _bars(day: date, closes: dict[tuple[int, int], float]) -> list[_Bar]:
    return [
        _Bar(_et(day, hh, mm), c, c, c, c, 10.0)
        for (hh, mm), c in sorted(closes.items())
    ]


def _meta(underlying: str, expiry: date, kind: str, strike: float, oi: int = 500) -> ContractMeta:
    return ContractMeta(
        symbol=format_occ(underlying, expiry, kind, strike),
        underlying=underlying,
        root=underlying,
        expiry=expiry,
        kind=kind,
        strike=strike,
        open_interest=oi,
        tradable=True,
        close_price=None,
    )


class _Fixture:
    """Records the contracts calls and serves bars per symbol."""

    def __init__(self, bars_by_symbol: dict[str, list[_Bar]], underlying_bars: list[_Bar]):
        self.calls: list[tuple] = []
        self.bars_requests: list[list[str]] = []
        self._bars = bars_by_symbol
        self._underlying = underlying_bars

    async def contracts(self, underlying, status, gte, lte, k_lo, k_hi):
        self.calls.append((status, gte, lte, k_lo, k_hi))
        out = []
        for expiry in (EXPIRY_PAST, EXPIRY_FUTURE):
            past = expiry < TODAY
            if (status == AssetStatus.INACTIVE) != past:
                continue
            if not (gte <= expiry <= lte):
                continue
            for strike in (95.0, 100.0, 105.0):
                for kind in ("call", "put"):
                    out.append(_meta(underlying, expiry, kind, strike))
        return out

    async def bars(self, symbols, start, end):
        self.bars_requests.append(list(symbols))
        return {s: list(self._bars[s]) for s in symbols if s in self._bars}

    async def underlying_bars(self, underlying, lookback_days):
        return list(self._underlying)


def _engine(fixture: _Fixture, spot_fn=None) -> ReplayOptionsEngine:
    return ReplayOptionsEngine(
        None,
        spot_fn=spot_fn,
        contracts_fn=fixture.contracts,
        bars_fn=fixture.bars,
        underlying_bars_fn=fixture.underlying_bars,
        today_fn=lambda: TODAY,
    )


def _default_fixture() -> _Fixture:
    atm_call = format_occ("XYZ", EXPIRY_PAST, "call", 100.0)
    atm_put = format_occ("XYZ", EXPIRY_PAST, "put", 100.0)
    deep_call = format_occ("XYZ", EXPIRY_PAST, "call", 95.0)
    bars = {
        atm_call: _bars(DAY, {(9, 31): 1.50, (9, 45): 1.80, (10, 0): 2.10, (10, 30): 2.40}),
        atm_put: _bars(DAY, {(9, 31): 1.40, (10, 0): 1.10}),
        # The deep call printed once, early.
        deep_call: _bars(DAY, {(9, 32): 5.60}),
    }
    underlying = [
        _Bar(_et(DAY, 9, 30), 99.5, 100.5, 99.0, 100.0, 1000.0),
        _Bar(_et(DAY, 9, 35), 100.0, 100.8, 99.8, 100.4, 1000.0),
        _Bar(_et(DAY, 10, 0), 100.4, 101.0, 100.2, 100.9, 1000.0),
    ]
    return _Fixture(bars, underlying)


def test_contracts_split_past_expiries_as_inactive_and_force_oi_to_zero():
    fixture = _default_fixture()
    engine = _engine(fixture)
    contracts, expiries = asyncio.run(engine.contracts("XYZ", DAY))
    statuses = [call[0] for call in fixture.calls]
    assert AssetStatus.INACTIVE in statuses and AssetStatus.ACTIVE in statuses
    inactive = next(c for c in fixture.calls if c[0] == AssetStatus.INACTIVE)
    assert inactive[1] == DAY and inactive[2] == TODAY - timedelta(days=1)
    active = next(c for c in fixture.calls if c[0] == AssetStatus.ACTIVE)
    assert active[1] == TODAY
    # Strike band around the day's range (99.0 .. 101.0, +-10%).
    assert inactive[3] == round(99.0 * 0.9, 2) and inactive[4] == round(101.0 * 1.1, 2)
    assert all(meta.open_interest == 0 for meta in contracts.values())
    assert [e.expiry for e in expiries] == [EXPIRY_PAST, EXPIRY_FUTURE]
    assert expiries[0].dte == (EXPIRY_PAST - DAY).days


def test_quote_at_never_returns_a_bar_after_as_of_and_keeps_the_last_print():
    fixture = _default_fixture()
    engine = _engine(fixture)
    asyncio.run(engine.ensure_bars("XYZ", DAY, EXPIRY_PAST))
    atm_call = format_occ("XYZ", EXPIRY_PAST, "call", 100.0)
    deep_call = format_occ("XYZ", EXPIRY_PAST, "call", 95.0)
    assert engine.quote_at(atm_call, _et(DAY, 9, 30)) is None
    at_945 = engine.quote_at(atm_call, _et(DAY, 9, 45))
    assert at_945 is not None and at_945.close == 1.80  # a bar stamped at as_of counts
    at_959 = engine.quote_at(atm_call, _et(DAY, 9, 59))
    assert at_959 is not None and at_959.close == 1.80
    # The deep call's only print at 09:32 is still "the price" at 14:00.
    stale = engine.quote_at(deep_call, _et(DAY, 14, 0))
    assert stale is not None and stale.close == 5.60 and stale.at == _et(DAY, 9, 32)
    assert [b.close for b in engine.bars_up_to(atm_call, _et(DAY, 10, 0))] == [1.50, 1.80, 2.10]
    # One request for the whole expiry, both kinds.
    assert len(fixture.bars_requests) == 1 and len(fixture.bars_requests[0]) == 6


def test_chain_is_synthetic_from_the_bar_close_with_solved_iv():
    fixture = _default_fixture()
    engine = _engine(fixture)
    as_of = _et(DAY, 10, 0)
    chain = asyncio.run(engine.chain("XYZ", EXPIRY_PAST, as_of))
    assert chain.feed == "replay" and chain.as_of == as_of
    assert chain.spot == 100.9  # the underlying's own bars, no session engine
    row = next(r for r in chain.rows if r.strike == 100.0)
    call = row.call
    assert call is not None
    slip = option_slippage(2.10)
    assert call.last == 2.10 and call.mid == 2.10
    # Quoted in cents, like a real market.
    assert call.bid == round(2.10 - slip, 2) and call.ask == round(2.10 + slip, 2)
    assert call.open_interest == 0 and call.bid_size is None
    assert call.last_at == as_of
    assert call.iv is not None and call.iv > 0
    assert call.delta is not None and 0 < call.delta < 1
    assert call.theta is not None and call.theta < 0
    # The 105 call never printed: a row with an empty quote, still tradable.
    empty = next(r for r in chain.rows if r.strike == 105.0).call
    assert empty is not None and empty.mid is None and empty.iv is None and empty.tradable


def test_chain_for_an_unknown_expiry_raises_lookup_error():
    engine = _engine(_default_fixture())
    try:
        asyncio.run(engine.chain("XYZ", date(2026, 12, 18), _et(DAY, 10, 0)))
    except LookupError as exc:
        assert "2026-12-18" in str(exc)
    else:
        raise AssertionError("expected LookupError")


def test_spot_prefers_the_session_engine_then_falls_back_to_own_bars():
    async def spot_fn(underlying, at):
        return 123.0 if underlying == "XYZ" else None

    engine = _engine(_default_fixture(), spot_fn=spot_fn)
    assert asyncio.run(engine.spot("XYZ", _et(DAY, 10, 0))) == 123.0
    engine = _engine(_default_fixture())
    assert asyncio.run(engine.spot("XYZ", _et(DAY, 9, 36))) == 100.4
    assert asyncio.run(engine.spot("XYZ", _et(DAY, 9, 0))) is None


def test_leg_quotes_load_bars_on_demand_for_any_contract():
    fixture = _default_fixture()
    engine = _engine(fixture)
    atm_put = format_occ("XYZ", EXPIRY_PAST, "put", 100.0)
    quotes = asyncio.run(engine.leg_quotes([atm_put, "NOTANOCC"], _et(DAY, 10, 5)))
    assert set(quotes) == {atm_put}
    assert quotes[atm_put].last == 1.10
    assert fixture.bars_requests == [[atm_put]]


def test_no_iv_for_a_zero_dte_contract_minutes_before_the_close():
    as_of = _et(EXPIRY_PAST, 15, 59)
    quote = synthetic_quote("X", 100.0, "call", EXPIRY_PAST, BarQuote(1.0, as_of, 1.0), 100.5, as_of)
    assert quote.iv is None and quote.delta is None
    assert quote.mid == 1.0 and quote.bid is not None


def test_replay_quote_source_fixes_the_moment():
    engine = _engine(_default_fixture())
    as_of = _et(DAY, 10, 0)
    source = ReplayQuoteSource(engine, as_of)
    assert source.feed == "replay"
    assert source.now().date() == DAY and source.now().tzinfo is not None
    spot, expiries = asyncio.run(source.expiries("XYZ"))
    assert spot == 100.9 and len(expiries) == 2
    chain = asyncio.run(source.chain("XYZ", EXPIRY_PAST))
    assert chain.as_of == as_of


def test_session_window_and_day_of_are_in_eastern_time():
    start, end = session_window(DAY)
    assert start == _et(DAY, 9, 30) and end == _et(DAY, 16, 15)
    assert day_of(_et(DAY, 23, 30)) == DAY
    assert day_of(datetime(2026, 8, 13, 2, 0, tzinfo=timezone.utc)) == DAY
