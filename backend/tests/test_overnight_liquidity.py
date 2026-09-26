"""The overnight-liquidity measurement (app.overnight.liquidity): German
windows mapped to the right time and feed, spreads per minute not per
quote."""

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from app.overnight.liquidity import WINDOWS, bar_stats, is_overnight, spread_stats, window_utc

UTC = timezone.utc
DAX_OPEN = next(w for w in WINDOWS if w.key == "dax_open")


@pytest.mark.parametrize(
    "day, utc_start, overnight",
    [
        # Summer: Berlin UTC+2, New York UTC-4 -- 09:00 in Germany is 03:00 there.
        (date(2026, 9, 21), datetime(2026, 9, 21, 7, 0, tzinfo=UTC), True),
        # Europe back on winter time, the US not yet (27 Oct 2026): 09:00 in
        # Germany is 08:00 UTC = 04:00 New York, already US premarket.
        (date(2026, 10, 27), datetime(2026, 10, 27, 8, 0, tzinfo=UTC), False),
        # Both on winter time: 09:00 in Germany is 03:00 in New York again.
        (date(2026, 12, 1), datetime(2026, 12, 1, 8, 0, tzinfo=UTC), True),
    ],
)
def test_the_dax_open_window_maps_through_both_time_zones(day, utc_start, overnight):
    start, end = window_utc(day, DAX_OPEN)
    assert start == utc_start and (end - start).total_seconds() == 3600
    assert is_overnight(start) is overnight


def _quote(minute, bid, ask, second=0):
    return SimpleNamespace(timestamp=datetime(2026, 9, 21, 7, minute, second, tzinfo=UTC), bid_price=bid, ask_price=ask)


def test_the_spread_is_the_median_of_each_minutes_last_quote():
    start, end = datetime(2026, 9, 21, 7, 0, tzinfo=UTC), datetime(2026, 9, 21, 8, 0, tzinfo=UTC)
    quotes = [
        # A burst of wide quotes in minute 0, ending tight: one minute, one vote.
        *[_quote(0, 500.0, 500.50, s) for s in range(50)],
        _quote(0, 500.0, 500.02, 59),
        _quote(1, 500.0, 500.02),
        _quote(2, 500.0, 500.10),
        _quote(3, 0, 500.10),  # one-sided, ignored
    ]
    cents, bps = spread_stats(quotes, start, end)
    assert cents == pytest.approx(2.0)
    assert bps == pytest.approx(0.02 / 500.01 * 10_000)


def test_no_quotes_means_no_spread_rather_than_zero():
    start, end = datetime(2026, 9, 21, 7, tzinfo=UTC), datetime(2026, 9, 21, 8, tzinfo=UTC)
    assert spread_stats([], start, end) == (None, None)


def test_bar_stats_count_minutes_volume_range_and_move():
    start, end = datetime(2026, 9, 21, 7, tzinfo=UTC), datetime(2026, 9, 21, 8, tzinfo=UTC)

    def bar(minute, o, h, lo, c, v):
        return SimpleNamespace(
            timestamp=datetime(2026, 9, 21, 7, minute, tzinfo=UTC), open=o, high=h, low=lo, close=c, volume=v, vwap=c
        )

    bars = [bar(0, 100, 101, 99.5, 100.5, 10), bar(30, 100.5, 102, 100, 101, 20)]
    stats = bar_stats(bars, start, end)
    assert stats["minutes"] == 60 and stats["minutes_traded"] == 2
    assert stats["volume"] == 30
    assert stats["range_pct"] == pytest.approx(2.5)
    assert stats["move_pct"] == pytest.approx(1.0)


def test_spread_samples_spread_across_the_whole_window():
    from app.overnight.liquidity import sample_times

    start, end = window_utc(date(2026, 9, 21), DAX_OPEN)
    times = sample_times(start, end)
    assert len(times) == 12
    assert times[0] == start and times[-1] == end - (times[1] - times[0])
