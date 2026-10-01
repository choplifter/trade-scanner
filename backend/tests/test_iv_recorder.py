"""The IV recorder: who gets a reading, when the pass is due, and what one
pass writes. No network and no clock -- the session moment is handed in."""

import asyncio
from datetime import date, datetime, timedelta

import pytest

from app.options.chain import Chain, LegQuote, StrikeRow
from app.options.iv_recorder import (
    DTE_RANGE,
    MAX_SYMBOLS,
    OPEN_SETTLE_MINUTES,
    due,
    record_once,
    symbols_to_record,
)
from app.services.market_clock import ET

TODAY = date(2026, 10, 1)
EXPIRY = TODAY + timedelta(days=45)


class _Uni:
    def __init__(self, price, dollar_vol):
        self.prev_close = price
        self.avg_dollar_vol_20d = dollar_vol


def _et(hour: int, minute: int = 0, day: date = TODAY) -> datetime:
    return datetime(day.year, day.month, day.day, 0, 0, tzinfo=ET) + timedelta(hours=hour, minutes=minute)


# --- who ---------------------------------------------------------------------


def test_the_watchlist_comes_first_and_thin_names_survive_there():
    universe = {"BIG": _Uni(100.0, 900e6), "MID": _Uni(100.0, 100e6), "THIN": _Uni(100.0, 1e6)}
    out = symbols_to_record(universe, watchlist=["thin", "OWN"])
    assert out[:2] == ["THIN", "OWN"], "a name someone follows is worth a row"
    assert out[2:] == ["BIG", "MID"], "then the liquid ones, richest first"


def test_the_universe_is_cut_at_the_limit_and_deduplicated():
    universe = {f"S{i}": _Uni(100.0, 100e6 + i) for i in range(10)}
    out = symbols_to_record(universe, watchlist=["S9"], limit=4)
    assert out == ["S9", "S8", "S7", "S6"], "no symbol twice, most liquid first"
    assert len(symbols_to_record({f"S{i}": _Uni(50.0, 500e6) for i in range(400)})) == MAX_SYMBOLS


def test_a_universe_without_a_price_is_skipped_rather_than_recorded_at_zero():
    assert symbols_to_record({"BROKEN": _Uni(0.0, 900e6)}) == []


# --- when --------------------------------------------------------------------


def test_the_pass_waits_for_the_open_to_settle():
    assert not due(_et(9, 35), None), "the first half hour prices the gap, not the day"
    assert due(_et(9, 30) + timedelta(minutes=OPEN_SETTLE_MINUTES), None)
    assert due(_et(14, 0), None)


def test_one_pass_a_session_and_none_outside_it():
    assert not due(_et(14, 0), TODAY), "already recorded today"
    assert due(_et(14, 0), TODAY - timedelta(days=1)), "a new session is a new reading"
    assert not due(_et(7, 0), None), "premarket is not a comparable reading"
    assert not due(_et(18, 0), None), "nor is after hours"


# --- what --------------------------------------------------------------------


def _chain(symbol: str, iv: float | None, spot: float = 100.0) -> Chain:
    def quote(kind: str) -> LegQuote:
        return LegQuote(
            symbol=f"{symbol}X", strike=spot, kind=kind, expiry=EXPIRY, bid=1.0, ask=1.1, mid=1.05,
            last=1.0, bid_size=1, ask_size=1, delta=0.5, gamma=0.0, theta=0.0, iv=iv,
            open_interest=10, tradable=True,
        )

    return Chain(
        underlying=symbol, expiry=EXPIRY, spot=spot, feed="opra", as_of=None,
        rows=[StrikeRow(strike=spot, call=quote("call"), put=quote("put"))],
    )


class _Service:
    def __init__(self, chains: dict[str, Chain], expiries: dict[str, list[date]] | None = None):
        self.chains = chains
        self.expiries_by_symbol = expiries or {s: [EXPIRY] for s in chains}
        self.fetched: list[str] = []

    async def expiries(self, symbol: str) -> dict:
        listed = self.expiries_by_symbol.get(symbol, [])
        return {
            "underlying": symbol,
            "spot": 100.0,
            "expiries": [{"expiry": e.isoformat(), "dte": (e - TODAY).days, "contract_count": 10} for e in listed],
        }

    async def chain(self, symbol: str, expiry: date) -> Chain:
        self.fetched.append(symbol)
        if symbol not in self.chains:
            raise LookupError(symbol)
        return self.chains[symbol]


class _Store:
    def __init__(self):
        self.rows: list[tuple] = []

    async def record(self, symbol, session_date, atm_iv, dte):
        self.rows.append((symbol, session_date, round(atm_iv, 4), dte))


def test_a_pass_records_the_atm_iv_of_an_expiry_in_the_window():
    service = _Service({"A": _chain("A", 0.42), "B": _chain("B", 0.31)})
    store = _Store()
    recorded, attempted = asyncio.run(record_once(service, store, ["A", "B"], TODAY))
    assert (recorded, attempted) == (2, 2)
    assert store.rows == [("A", TODAY, 0.42, 45), ("B", TODAY, 0.31, 45)]
    assert DTE_RANGE[0] <= 45 <= DTE_RANGE[1]


def test_a_symbol_with_no_expiry_in_the_window_costs_no_chain_fetch():
    near = TODAY + timedelta(days=5)
    service = _Service({"A": _chain("A", 0.42)}, expiries={"A": [near]})
    store = _Store()
    recorded, _ = asyncio.run(record_once(service, store, ["A"], TODAY))
    assert recorded == 0 and store.rows == []
    assert service.fetched == [], "the window is checked before the chain is paid for"


def test_a_chain_without_an_iv_is_not_recorded_as_a_zero():
    service = _Service({"A": _chain("A", None)})
    store = _Store()
    recorded, _ = asyncio.run(record_once(service, store, ["A"], TODAY))
    assert recorded == 0 and store.rows == []


def test_one_symbol_failing_does_not_stop_the_pass():
    service = _Service({"B": _chain("B", 0.25)})  # "A" raises LookupError
    store = _Store()
    recorded, attempted = asyncio.run(record_once(service, store, ["A", "B"], TODAY))
    assert (recorded, attempted) == (1, 2)
    assert [r[0] for r in store.rows] == ["B"]


def test_readings_accumulate_into_a_rank(tmp_path):
    """The point of the whole loop: twenty sessions of readings is what
    turns an IV into an IV rank."""
    from app.options.iv_history_store import MIN_SAMPLES, IvHistoryStore

    store = IvHistoryStore(str(tmp_path / "iv.sqlite3"))
    asyncio.run(store.init_schema())
    # A board of expiries rather than one date: every past session needs an
    # expiry inside its own 30-60 day window, which a single fixed one
    # stops being after a fortnight (the recorder is right to skip those).
    board = [TODAY + timedelta(days=d) for d in range(0, 140, 7)]
    service = _Service({"A": _chain("A", 0.30)}, expiries={"A": board})

    for i in range(MIN_SAMPLES - 1):
        asyncio.run(record_once(service, store, ["A"], TODAY - timedelta(days=MIN_SAMPLES - i)))
    rank, samples = asyncio.run(store.rank("A", 0.30))
    assert rank is None and samples == MIN_SAMPLES - 1, "below the floor it says how far it has got"

    service.chains["A"] = _chain("A", 0.50)
    asyncio.run(record_once(service, store, ["A"], TODAY))
    rank, samples = asyncio.run(store.rank("A", 0.50))
    assert samples == MIN_SAMPLES
    assert rank is not None and rank.percent == pytest.approx(100.0), "today is the top of its own range"


# --- one stretch of the curve, or the rank means nothing -----------------------


def test_a_reading_from_the_wrong_part_of_the_curve_is_not_recorded(tmp_path):
    """The day MU reported, its 1-DTE chain priced 178 % against the
    51-DTE chain's 57 %. Both are real; in one history they are noise."""
    from app.options.iv_history_store import COMPARABLE_DTE, IvHistoryStore

    store = IvHistoryStore(str(tmp_path / "iv.sqlite3"))
    asyncio.run(store.init_schema())
    asyncio.run(store.record("MU", TODAY, 1.78, 1))
    assert asyncio.run(store.history("MU")) == [], "a one-day expiry is a different measurement"

    asyncio.run(store.record("MU", TODAY, 0.575, 51))
    assert asyncio.run(store.history("MU")) == [0.575]
    assert COMPARABLE_DTE[0] <= 51 <= COMPARABLE_DTE[1]


def test_rows_written_before_the_band_existed_are_ignored_on_read(tmp_path):
    """No migration: the filter sits on the read side, so the rows already
    in the file stop counting without being deleted."""
    import sqlite3

    from app.options.iv_history_store import IvHistoryStore

    path = str(tmp_path / "iv.sqlite3")
    store = IvHistoryStore(path)
    asyncio.run(store.init_schema())
    with sqlite3.connect(path) as conn:  # straight past record()'s guard
        conn.execute(
            "INSERT INTO option_iv_history (symbol, session_date, atm_iv, dte, recorded_at) VALUES (?, ?, ?, ?, ?)",
            ("MU", TODAY.isoformat(), 1.78, 1, "2026-10-01T12:00:00+00:00"),
        )
    assert asyncio.run(store.history("MU")) == []
    rank, samples = asyncio.run(store.rank("MU", 0.60))
    assert rank is None and samples == 0


def test_the_events_block_skips_a_chain_outside_the_band(tmp_path):
    from app.options.events import _iv_rank_block
    from app.options.iv_history_store import IvHistoryStore

    store = IvHistoryStore(str(tmp_path / "iv.sqlite3"))
    asyncio.run(store.init_schema())

    near = asyncio.run(_iv_rank_block(store, "MU", 1.78, 1, TODAY))
    assert near["rank"] is None and near["samples"] == 0
    assert asyncio.run(store.history("MU")) == [], "looking at a 0DTE chain must not write one"

    normal = asyncio.run(_iv_rank_block(store, "MU", 0.57, 45, TODAY))
    assert normal["atm_iv"] == pytest.approx(0.57)
    assert asyncio.run(store.history("MU")) == [0.57]
