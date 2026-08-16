from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from app.scanners import screener
from app.scanners.backtest import (
    intraday_would_help,
    supported_fields,
    unsupported_filters,
)
from app.scanners.intraday_backtest import (
    benchmark_to_close,
    build_rows_by_timestamp,
    previous_closes,
    replication_factor,
    sample_picks,
    simulate_intraday_screen,
)
from app.services.market_clock import ET

_DAY = date(2026, 1, 5)  # a real NYSE session
_WINDOW = timedelta(minutes=60)


@dataclass
class _Bar:
    volume: float
    timestamp: datetime
    close: float = 10.0
    open: float = None  # type: ignore[assignment]
    high: float = None  # type: ignore[assignment]
    low: float = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.open is None:
            self.open = self.close
        if self.high is None:
            self.high = self.close
        if self.low is None:
            self.low = self.close


def _session(
    closes: list[float],
    volume: float = 100_000.0,
    start: time = time(9, 30),
    day: date = _DAY,
    green: bool = True,
):
    """Green by default: the volume-rate fields are long-only, so a flat bar
    would null them and most fixtures would test nothing."""
    first = datetime.combine(day, start, tzinfo=ET)
    return [
        _Bar(
            volume=volume,
            close=c,
            open=c - 1 if green else c + 1,
            timestamp=first + timedelta(minutes=5 * i),
        )
        for i, c in enumerate(closes)
    ]


def _daily(closes: list[float], volume: float = 1_000_000.0, end: date = _DAY):
    return [
        _Bar(
            volume=volume,
            close=c,
            timestamp=datetime.combine(end - timedelta(days=len(closes) - 1 - i), time(0, 0), tzinfo=ET),
        )
        for i, c in enumerate(closes)
    ]


_FLAT_CURVE = [(time(0, 0), 0.1), (time(23, 0), 1.0)]


# --- resolution-aware field support -------------------------------------------


def test_intraday_resolution_accepts_the_volume_rate_fields():
    screen = screener.Screen(filters=[screener.Filter(field="rvol_1h", op="gt", value=2)])
    assert unsupported_filters(screen, "daily") == ["rvol_1h"]
    assert unsupported_filters(screen, "intraday") == []


def test_some_fields_are_unsupported_at_every_resolution():
    for field in ("spread_pct", "is_stale"):
        screen = screener.Screen(filters=[screener.Filter(field=field, op="gt", value=1)])
        assert unsupported_filters(screen, "intraday") == [field], field


def test_intraday_would_help_only_names_what_switching_fixes():
    screen = screener.Screen(
        filters=[
            screener.Filter(field="rvol_1h", op="gt", value=2),
            screener.Filter(field="float_shares", op="lt", value=1e7),
        ]
    )
    # Switching resolution fixes one of these and not the other -- telling the
    # user to delete rvol_1h would be the wrong advice.
    assert intraday_would_help(screen) == ["rvol_1h"]


def test_intraday_supports_a_superset_of_daily():
    assert supported_fields("daily") < supported_fields("intraday")


# --- row construction ---------------------------------------------------------


def test_previous_closes_uses_the_prior_session():
    daily = _daily([10.0, 11.0, 12.0])
    per_date = previous_closes({"AAA": daily})["AAA"]
    assert per_date[_DAY] == 11.0


def test_rows_carry_session_to_date_values_not_the_finished_day():
    bars = _session([10.0, 11.0, 12.0])
    rows_by_ts, exits = build_rows_by_timestamp(
        {"AAA": bars}, {"AAA": {_DAY: 1_000_000.0}}, {"AAA": {_DAY: 10.0}}, _FLAT_CURVE, _WINDOW
    )

    ordered = [rows_by_ts[ts][0] for ts in sorted(rows_by_ts)]
    # Cumulative volume grows through the session -- at bar 1 the screen must
    # see one bar's volume, not the whole day's.
    assert [r.volume_today for r in ordered] == [100_000.0, 200_000.0, 300_000.0]
    # Running high, not the finished day's high.
    assert [r.day_high for r in ordered] == [10.0, 11.0, 12.0]
    # Exit is that session's close.
    assert exits[("AAA", _DAY)] == 12.0


def test_gap_is_measured_against_the_previous_close():
    bars = _session([11.0])
    rows_by_ts, _ = build_rows_by_timestamp(
        {"AAA": bars}, {"AAA": {_DAY: 1_000_000.0}}, {"AAA": {_DAY: 10.0}}, _FLAT_CURVE, _WINDOW
    )
    row = next(iter(rows_by_ts.values()))[0]
    assert round(row.pct_change, 2) == 10.0


def test_rows_are_skipped_without_a_warmed_up_baseline_or_prev_close():
    bars = _session([10.0, 11.0])
    assert build_rows_by_timestamp({"AAA": bars}, {}, {"AAA": {_DAY: 10.0}}, _FLAT_CURVE, _WINDOW)[0] == {}
    assert build_rows_by_timestamp({"AAA": bars}, {"AAA": {_DAY: 1.0}}, {}, _FLAT_CURVE, _WINDOW)[0] == {}


def test_volume_fields_warm_up_the_same_way_the_live_field_does():
    # 25 bars from 09:30 -> 09:30..11:30. volume_1h needs one full window
    # (12 bars, from 10:30); volume_surge needs two (24 bars, from 11:30).
    # Matching volume_surge._anchor exactly, or the backtest would validate
    # a signal the live scanner never emits.
    bars = _session([10.0] * 25)
    rows_by_ts, _ = build_rows_by_timestamp(
        {"AAA": bars}, {"AAA": {_DAY: 1_000_000.0}}, {"AAA": {_DAY: 9.0}}, _FLAT_CURVE, _WINDOW
    )
    ordered = [rows_by_ts[ts][0] for ts in sorted(rows_by_ts)]

    assert ordered[0].volume_1h is None
    assert ordered[11].volume_1h is not None
    assert ordered[11].volume_surge is None
    assert ordered[23].volume_surge is not None


def test_volume_fields_are_long_only():
    red = _session([10.0] * 25, green=False)
    rows_by_ts, _ = build_rows_by_timestamp(
        {"AAA": red}, {"AAA": {_DAY: 1_000_000.0}}, {"AAA": {_DAY: 11.0}}, _FLAT_CURVE, _WINDOW
    )
    ordered = [rows_by_ts[ts][0] for ts in sorted(rows_by_ts)]
    # Warmed up by bar 24, but red -- so no surge reading at all.
    assert ordered[-1].volume_surge is None
    assert ordered[-1].rvol_1h is None


# --- screening ----------------------------------------------------------------


def _inputs(bars):
    return (
        {"AAA": bars},
        {"AAA": {_DAY: 1_000_000.0}},
        {"AAA": {_DAY: 10.0}},
        _FLAT_CURVE,
    )


def test_every_qualifying_bar_becomes_a_pick():
    # Up 10% from the first bar onward, so all four bars qualify.
    bars = _session([11.0, 11.0, 11.0, 11.0])
    b, avg, prev, curve = _inputs(bars)
    picks = simulate_intraday_screen(
        b, avg, prev, curve,
        screener.Screen(filters=[screener.Filter(field="pct_change", op="gt", value=5)]),
        0.0, _WINDOW,
    )
    assert len(picks) == 4
    assert {p["view"] for p in picks} == {"screen"}


def test_replication_factor_exposes_repeated_events():
    bars = _session([11.0, 11.0, 11.0, 11.0])
    b, avg, prev, curve = _inputs(bars)
    picks = simulate_intraday_screen(
        b, avg, prev, curve,
        screener.Screen(filters=[screener.Filter(field="pct_change", op="gt", value=5)]),
        0.0, _WINDOW,
    )
    rep = replication_factor(picks)

    # Four picks, but only one symbol-day behind them -- the number that says
    # the sample carries less evidence than its size suggests.
    assert rep["sample_size"] == 4
    assert rep["distinct_symbol_days"] == 1
    assert rep["picks_per_event"] == 4.0


def test_picks_are_held_to_the_session_close():
    bars = _session([11.0, 11.0, 22.0])
    b, avg, prev, curve = _inputs(bars)
    picks = simulate_intraday_screen(
        b, avg, prev, curve,
        screener.Screen(filters=[screener.Filter(field="pct_change", op="gt", value=5)]),
        0.0, _WINDOW,
    )
    first = min(picks, key=lambda p: p["timestamp"])
    assert round(first["pct_change_since_entry"], 2) == 100.0


def test_benchmark_is_measured_over_the_same_intraday_window():
    # SPY 100 -> 110 across the session, so its move *from the first bar* to
    # the close is +10%, and from the second bar it's 0%. Keying alpha on the
    # entry bar is what makes those different; a daily close-to-close figure
    # would wrongly give both entries the same benchmark.
    spy = _session([100.0, 110.0])
    to_close = benchmark_to_close(spy)
    stamps = sorted(to_close)
    assert round(to_close[stamps[0]], 2) == 10.0
    assert round(to_close[stamps[1]], 2) == 0.0


def test_alpha_uses_the_benchmark_move_from_the_entry_bar():
    bars = _session([11.0, 22.0])
    spy = _session([100.0, 110.0])
    b, avg, prev, curve = _inputs(bars)
    picks = simulate_intraday_screen(
        b, avg, prev, curve,
        screener.Screen(filters=[screener.Filter(field="pct_change", op="gt", value=5)]),
        0.0, _WINDOW, benchmark_to_close(spy),
    )
    first = min(picks, key=lambda p: p["timestamp"])
    # Entered on the first bar, so the benchmark's own first-bar-to-close move.
    assert round(first["benchmark_pct_change_since_entry"], 2) == 10.0
    assert round(first["alpha_vs_benchmark"], 2) == round(first["pct_change_since_entry"] - 10.0, 2)


def test_alpha_is_none_when_the_benchmark_has_no_bar_for_that_moment():
    bars = _session([11.0, 22.0])
    b, avg, prev, curve = _inputs(bars)
    picks = simulate_intraday_screen(
        b, avg, prev, curve,
        screener.Screen(filters=[screener.Filter(field="pct_change", op="gt", value=5)]),
        0.0, _WINDOW, {},
    )
    assert all(p["alpha_vs_benchmark"] is None for p in picks)


def test_a_screen_matching_nothing_yields_no_picks():
    bars = _session([10.0, 10.0])
    b, avg, prev, curve = _inputs(bars)
    picks = simulate_intraday_screen(
        b, avg, prev, curve,
        screener.Screen(filters=[screener.Filter(field="pct_change", op="gt", value=50)]),
        0.0, _WINDOW,
    )
    assert picks == []


def test_sample_picks_spans_the_whole_period_not_just_the_newest():
    """Taking the newest N made a 15-day backtest look like one afternoon:
    300 of 5,027 picks covered only 12:30-15:55 of the final day."""
    picks = [
        {"symbol": "AAA", "trading_date": f"2026-01-{day:02d}", "timestamp": f"2026-01-{day:02d}T{9 + h:02d}:30:00-05:00"}
        for day in range(1, 16)
        for h in range(7)
    ]
    sampled = sample_picks(picks, limit=10)

    assert len(sampled) == 10
    dates = {p["trading_date"] for p in sampled}
    # Spread across the range rather than clustered at one end.
    assert min(dates) == "2026-01-01"
    assert max(dates) == "2026-01-15"
    # Newest first, as displayed.
    assert sampled[0]["timestamp"] > sampled[-1]["timestamp"]


def test_sample_picks_returns_everything_when_under_the_limit():
    picks = [{"symbol": "AAA", "trading_date": "2026-01-05", "timestamp": f"2026-01-05T10:{m:02d}:00-05:00"} for m in (0, 5, 10)]
    assert len(sample_picks(picks, limit=100)) == 3
