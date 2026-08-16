from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from app.scanners.rvol_backtest import (
    NORMALIZED,
    RAW,
    first_crossings,
    observe_rvol,
    sweep_thresholds,
    trailing_avg_daily_volume,
)
from app.services.market_clock import ET

# A real NYSE trading day (Monday), so is_regular_session_bar's calendar
# lookup actually finds a session rather than silently dropping every bar.
_TRADING_DAY = date(2026, 1, 5)


@dataclass
class _Bar:
    close: float
    volume: float
    timestamp: datetime
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0

    def __post_init__(self):
        self.open = self.open or self.close
        self.high = self.high or self.close
        self.low = self.low or self.close


def _session_bars(closes_and_volumes, day: date = _TRADING_DAY, start_minute: int = 30) -> list[_Bar]:
    """5-minute regular-session bars starting at 09:30 ET on `day`."""
    bars = []
    for i, (close, volume) in enumerate(closes_and_volumes):
        stamp = datetime.combine(day, time(9, start_minute), tzinfo=ET) + timedelta(minutes=5 * i)
        bars.append(_Bar(close=close, volume=volume, timestamp=stamp))
    return bars


def _daily_bars(day_count: int, volume: float, end: date = _TRADING_DAY) -> list[_Bar]:
    """Daily bars ending the day *before* `end`, plus `end` itself -- enough
    history for trailing_avg_daily_volume to warm up.
    """
    return [
        _Bar(
            close=10.0,
            volume=volume,
            timestamp=datetime.combine(end - timedelta(days=day_count - 1 - i), time(0, 0), tzinfo=ET),
        )
        for i in range(day_count)
    ]


# A flat curve: 50% of the day done by every bucket. Makes the normalized
# denominator exactly half the raw one, so normalized RVOL must come out at
# exactly 2x raw -- an arithmetic fact a test can assert on without
# depending on any real volume profile's shape.
_HALF_CURVE = [(time(0, 0), 0.5)]


def test_trailing_avg_skips_days_without_enough_history():
    bars = _daily_bars(10, volume=1_000.0)
    assert trailing_avg_daily_volume({"AAA": bars}) == {}


def test_trailing_avg_excludes_the_day_itself():
    # 20 days at 1,000 then a huge-volume final day. That final day's own
    # average must be 1,000 -- if its own volume leaked into the window the
    # RVOL denominator would absorb the very spike it's meant to measure.
    bars = _daily_bars(20, volume=1_000.0)
    spike_day = date(2026, 1, 6)
    bars.append(_Bar(close=10.0, volume=999_999.0, timestamp=datetime.combine(spike_day, time(0, 0), tzinfo=ET)))

    per_date = trailing_avg_daily_volume({"AAA": bars})["AAA"]
    assert per_date[spike_day] == 1_000.0


def test_observe_rvol_normalizes_by_the_session_fraction():
    bars = _session_bars([(10.0, 500.0), (11.0, 500.0), (12.0, 500.0)])
    avg = {"AAA": {_TRADING_DAY: 1_000.0}}

    records = observe_rvol({"AAA": bars}, avg, _HALF_CURVE)

    assert records
    for record in records:
        # Half the denominator, so exactly double the RVOL -- the whole
        # point of the flag, and the reason the 15x threshold can't carry
        # over unchanged.
        assert record[NORMALIZED] == record[RAW] * 2
    # Volume accumulates through the session rather than resetting per bar.
    assert records[0][RAW] == 500.0 / 1_000.0
    assert records[1][RAW] == 1_000.0 / 1_000.0


def test_observe_rvol_measures_to_session_close_by_default():
    bars = _session_bars([(10.0, 500.0), (11.0, 500.0), (20.0, 500.0)])
    avg = {"AAA": {_TRADING_DAY: 1_000.0}}

    records = observe_rvol({"AAA": bars}, avg, _HALF_CURVE)

    # The last bar can't be an entry -- no forward move to measure -- so two
    # records, both scored against the 20.0 close.
    assert len(records) == 2
    assert round(records[0]["pct_change_since_entry"], 2) == 100.0
    assert round(records[1]["pct_change_since_entry"], 2) == round((20.0 - 11.0) / 11.0 * 100, 2)


def test_observe_rvol_drops_entries_without_a_full_horizon():
    bars = _session_bars([(10.0, 500.0), (11.0, 500.0), (12.0, 500.0)])
    avg = {"AAA": {_TRADING_DAY: 1_000.0}}

    # Bars are 5 minutes apart, so a 30-minute horizon never closes inside
    # this 15-minute session slice -- every entry is dropped rather than
    # scored against a shorter window.
    assert observe_rvol({"AAA": bars}, avg, _HALF_CURVE, horizon_minutes=30) == []


def test_observe_rvol_skips_symbols_without_a_warmed_up_baseline():
    bars = _session_bars([(10.0, 500.0), (11.0, 500.0)])
    assert observe_rvol({"AAA": bars}, {}, _HALF_CURVE) == []


def test_observe_rvol_resets_cumulative_volume_each_trading_day():
    day_one = _session_bars([(10.0, 500.0), (11.0, 500.0)])
    day_two = _session_bars([(10.0, 500.0), (11.0, 500.0)], day=date(2026, 1, 6))
    avg = {"AAA": {_TRADING_DAY: 1_000.0, date(2026, 1, 6): 1_000.0}}

    records = observe_rvol({"AAA": day_one + day_two}, avg, _HALF_CURVE)

    by_date = {r["trading_date"]: r for r in records}
    # Each day's first bar sees only its own 500 shares -- day two must not
    # inherit day one's accumulated volume.
    assert by_date["2026-01-05"][RAW] == 0.5
    assert by_date["2026-01-06"][RAW] == 0.5


def test_first_crossings_takes_one_entry_per_symbol_per_day():
    records = [
        {"symbol": "AAA", "trading_date": "2026-01-05", "timestamp": "2026-01-05T09:35", RAW: 20.0},
        {"symbol": "AAA", "trading_date": "2026-01-05", "timestamp": "2026-01-05T09:40", RAW: 25.0},
        {"symbol": "AAA", "trading_date": "2026-01-06", "timestamp": "2026-01-06T09:35", RAW: 30.0},
    ]
    crossings = first_crossings(records, RAW, threshold=15.0)

    # Two entries, not three: the second bar of 2026-01-05 is the same event
    # still in progress, not independent evidence.
    assert [c["timestamp"] for c in crossings] == ["2026-01-05T09:35", "2026-01-06T09:35"]


def test_first_crossings_uses_the_earliest_qualifying_bar():
    records = [
        {"symbol": "AAA", "trading_date": "2026-01-05", "timestamp": "2026-01-05T10:00", RAW: 40.0},
        {"symbol": "AAA", "trading_date": "2026-01-05", "timestamp": "2026-01-05T09:35", RAW: 20.0},
    ]
    crossings = first_crossings(records, RAW, threshold=15.0)

    # Input order must not decide the entry -- it's the first crossing by
    # time, which is when a live scanner would have shown it.
    assert [c["timestamp"] for c in crossings] == ["2026-01-05T09:35"]


def test_sweep_reports_both_definitions_and_counts_distinct_symbols():
    records = [
        {"symbol": "AAA", "trading_date": "2026-01-05", "timestamp": "1", RAW: 20.0, NORMALIZED: 40.0,
         "pct_change_since_entry": -5.0},
        {"symbol": "BBB", "trading_date": "2026-01-05", "timestamp": "2", RAW: 20.0, NORMALIZED: 40.0,
         "pct_change_since_entry": 5.0},
    ]
    rows = sweep_thresholds(records, thresholds=[15.0, 30.0])
    by_key = {(r["definition"], r["threshold"]): r for r in rows}

    # Both symbols clear 15x raw; neither clears 30x raw; both clear 30x
    # normalized -- the definitions have to be swept independently.
    assert by_key[(RAW, 15.0)]["sample_size"] == 2
    assert by_key[(RAW, 15.0)]["distinct_symbols"] == 2
    assert by_key[(RAW, 30.0)]["sample_size"] == 0
    assert by_key[(NORMALIZED, 30.0)]["sample_size"] == 2
    assert by_key[(RAW, 15.0)]["win_rate"] == 50.0
    # Two entries is far below the noise floor and must be flagged as such.
    assert by_key[(RAW, 15.0)]["sufficient_sample"] is False
