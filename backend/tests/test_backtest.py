from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from app.scanners.backtest import simulate_from_bars


@dataclass
class _Bar:
    close: float
    volume: float
    timestamp: datetime


def _daily_bars(start: date, closes: list[float], volume: float = 100_000.0) -> list[_Bar]:
    """One bar per calendar day starting at `start` (no weekend-skipping --
    the date-keyed grouping under test doesn't care about real trading
    calendars, just that each bar carries a distinct date).
    """
    return [
        _Bar(close=c, volume=volume, timestamp=datetime.combine(start + timedelta(days=i), datetime.min.time(), tzinfo=timezone.utc))
        for i, c in enumerate(closes)
    ]


def test_simulate_from_bars_empty_for_short_history():
    # Needs _WARMUP_TRADING_DAYS (20) + horizon_days (1) + 1 bars minimum.
    bars = _daily_bars(date(2026, 1, 1), [100.0] * 10)
    picks = simulate_from_bars({"AAA": bars}, min_dollar_volume=0.0, horizon_days=1)
    assert picks == []


def test_simulate_from_bars_basic_gainer_win():
    # 20 flat warmup days, then a +10% gap day, then a further gain the
    # next day (a "win" by the pct_change_since_entry > 0 convention).
    closes = [100.0] * 20 + [110.0, 115.0]
    bars = _daily_bars(date(2026, 1, 1), closes)
    picks = simulate_from_bars({"AAA": bars}, min_dollar_volume=0.0, horizon_days=1)

    assert len(picks) == 1
    pick = picks[0]
    assert pick["symbol"] == "AAA"
    assert pick["view"] == "gainers"
    assert round(pick["entry_pct_change"], 2) == 10.0
    assert round(pick["pct_change_since_entry"], 2) == round((115.0 - 110.0) / 110.0 * 100, 2)


def test_simulate_from_bars_basic_loser():
    closes = [100.0] * 20 + [90.0, 85.0]
    bars = _daily_bars(date(2026, 1, 1), closes)
    picks = simulate_from_bars({"AAA": bars}, min_dollar_volume=0.0, horizon_days=1)

    assert len(picks) == 1
    assert picks[0]["view"] == "losers"
    assert round(picks[0]["entry_pct_change"], 2) == -10.0


def test_simulate_from_bars_min_dollar_volume_filters_out_thin_symbols():
    closes = [100.0] * 20 + [110.0, 115.0]
    bars = _daily_bars(date(2026, 1, 1), closes, volume=10.0)  # $1,100 dollar volume on the entry day
    picks = simulate_from_bars({"AAA": bars}, min_dollar_volume=1_000_000.0, horizon_days=1)
    assert picks == []


def test_simulate_from_bars_groups_by_calendar_date_not_list_index():
    # AAA has a normal, unbroken run of daily bars. BBB is "new" and only
    # starts partway through AAA's window -- so BBB's list index 0 lines up
    # with a much later calendar date than AAA's index 0. If rows were
    # grouped by list index instead of date, BBB's early entries would get
    # cross-ranked against AAA's much-earlier days instead of never
    # existing for those dates.
    start = date(2026, 1, 1)
    aaa_closes = [100.0] * 20 + [110.0, 111.0]  # entry on day 20 (2026-01-21)
    aaa = _daily_bars(start, aaa_closes)

    # BBB's series starts 20 calendar days later, so its list index 20
    # (its own entry day) actually falls on a *different* calendar date
    # than AAA's index 20.
    bbb_start = start + timedelta(days=20)
    bbb_closes = [50.0] * 20 + [55.0, 56.0]
    bbb = _daily_bars(bbb_start, bbb_closes)

    picks = simulate_from_bars({"AAA": aaa, "BBB": bbb}, min_dollar_volume=0.0, horizon_days=1)

    aaa_pick = next(p for p in picks if p["symbol"] == "AAA")
    bbb_pick = next(p for p in picks if p["symbol"] == "BBB")
    # Each symbol's pick is dated to its own actual entry bar, not smeared
    # onto a shared index-based date.
    assert aaa_pick["trading_date"] == (start + timedelta(days=20)).isoformat()
    assert bbb_pick["trading_date"] == (bbb_start + timedelta(days=20)).isoformat()
    assert aaa_pick["trading_date"] != bbb_pick["trading_date"]


def test_simulate_from_bars_respects_horizon_days():
    closes = [100.0] * 20 + [110.0, 111.0, 112.0, 120.0]
    bars = _daily_bars(date(2026, 1, 1), closes)
    picks = simulate_from_bars({"AAA": bars}, min_dollar_volume=0.0, horizon_days=3)

    assert len(picks) == 1
    # Entry day 20 (close 110.0), horizon 3 trading days later is index 23 (close 120.0).
    assert round(picks[0]["pct_change_since_entry"], 2) == round((120.0 - 110.0) / 110.0 * 100, 2)
