"""Levels price keeps returning to, and what counts as a return.

The counting rule is the whole indicator. A naive "bars touching the level"
count, or even "runs of touching bars", measures where the stock spent time
rather than which prices it respected -- measured on real minute bars those
versions scored 106 kisses on AAPL and 108 on MARA over ten sessions, for a
method whose threshold is three. The band width and the departure rule are
what fix that, so both are pinned here.
"""

from types import SimpleNamespace

import numpy as np
import pytest

from app.indicators import market_structure as ms
from app.indicators.context import build_context
from app.market_data import levels


def _band(df):
    """levels.touch_band over a chart frame.

    The ported function takes plain arrays -- that independence from pandas
    is what lets the scanner and the backtest call it -- so the frame is
    unpacked here instead of at every call site.
    """
    return levels.touch_band(
        df["high"].to_numpy(dtype=float),
        df["low"].to_numpy(dtype=float),
        df["close"].to_numpy(dtype=float),
    )


def _count(pairs, level, tolerance=1.0):
    """pairs: (high, low) per bar."""
    highs = np.array([h for h, _ in pairs], dtype=float)
    lows = np.array([l for _, l in pairs], dtype=float)
    return levels.count_visits(highs, lows, level, tolerance)


# --- what counts as a kiss ----------------------------------------------


def test_a_single_visit_counts_once():
    assert _count([(100, 100), (200, 200)], level=100) == 1


def test_consecutive_bars_on_the_level_are_one_visit():
    """Price sitting on a level for an hour is one test of it, not sixty."""
    assert _count([(100, 100)] * 60, level=100) == 1


def test_leaving_and_returning_counts_again():
    bars = [(100, 100), (200, 200), (100, 100), (200, 200), (100, 100)]

    assert _count(bars, level=100) == 3


def test_price_must_clear_the_departure_band_to_re_arm():
    """The fix for the 106-kiss result. Hovering just outside the touch band
    is still 'at' the level -- only a real departure makes the next touch a
    new visit."""
    just_outside = 100 + 1.0 * levels._DEPARTURE_MULTIPLE  # inside the departure band
    bars = [(100, 100), (just_outside, just_outside)] * 10

    assert _count(bars, level=100) == 1


def test_clearing_the_departure_band_does_re_arm():
    clear = 100 + 1.0 * levels._DEPARTURE_MULTIPLE + 1.0
    bars = [(100, 100), (clear, clear), (100, 100), (clear, clear)]

    assert _count(bars, level=100) == 2


def test_a_bar_straddling_the_level_touches_it():
    """The band is tested against the bar's whole range, not its close."""
    assert _count([(150, 50)], level=100) == 1


def test_a_level_never_reached_scores_zero():
    assert _count([(10, 9)] * 20, level=100) == 0


# --- the band -----------------------------------------------------------


def _bars(pairs, close=None):
    return [
        SimpleNamespace(
            timestamp=np.datetime64("2026-08-20T14:00") + np.timedelta64(i, "m"),
            open=(h + l) / 2,
            high=h,
            low=l,
            close=close[i] if close else (h + l) / 2,
            volume=1_000,
            vwap=None,
        )
        for i, (h, l) in enumerate(pairs)
    ]


def _anchored(bars, timeframe="1Min"):
    """Bars in the anchor slot -- the hourly series compute() reads, whatever
    resolution the chart itself is drawing."""
    return build_context("X", [], [], [], timeframe, bars)


def test_the_band_tracks_the_span_on_screen():
    """The unit that survives both a $2 gapper next to a $300 large cap and a
    minute chart next to a weekly one. A percentage of price measured
    volatility instead of structure; a multiple of ATR held within a timeframe
    but collapsed across them, leaving daily charts with no levels at all."""
    narrow = _anchored(_bars([(105.0, 95.0)] * 200))
    wide = _anchored(_bars([(150.0, 50.0)] * 200))

    assert _band(wide.hourly_bars) == pytest.approx(
        _band(narrow.hourly_bars) * 10
    )


def test_the_band_is_the_configured_fraction_of_the_span():
    ctx = _anchored(_bars([(120.0, 100.0)] * 200))

    assert _band(ctx.hourly_bars) == pytest.approx(20.0 * levels._RANGE_FRACTION_PCT / 100)


def test_the_band_has_a_floor_for_a_barely_moving_symbol():
    """Every bar identical -- ATR is zero, and a zero-width band would touch
    nothing at all."""
    frozen = _anchored(_bars([(100.0, 100.0)] * 200))

    assert _band(frozen.hourly_bars) == pytest.approx(100.0 * levels._MIN_TOLERANCE_PCT / 100)


# --- the indicator ------------------------------------------------------


def _zigzag_pairs(cycles, low=100.0, high=110.0, dwell=12):
    pairs = []
    for _ in range(cycles):
        pairs += [(low + 0.02, low - 0.02)] * dwell
        pairs += [(high + 0.02, high - 0.02)] * dwell
    return _bars(pairs)


def _zigzag(cycles, low=100.0, high=110.0, dwell=12):
    return _anchored(_zigzag_pairs(cycles, low, high, dwell))


def test_no_levels_without_enough_history():
    assert ms.compute(_anchored(_bars([(100, 99)] * 5))) == {}


def test_no_levels_from_an_empty_context():
    assert ms.compute(_anchored([])) == {}


def test_a_repeatedly_tested_price_becomes_a_level():
    result = ms.compute(_zigzag(cycles=5))

    assert result, "a price touched on five separate swings should qualify"
    assert any(abs(price - 100.0) < 1.0 for price in result.values())


def test_a_price_touched_fewer_than_three_times_is_not_a_level():
    """The threshold is the method's own: two touches is a line through any
    two points."""
    assert ms.compute(_zigzag(cycles=2)) == {}


def test_at_most_max_levels_are_returned():
    result = ms.compute(_zigzag(cycles=8, dwell=11))

    assert len(result) <= ms.MAX_LEVELS


def test_labels_carry_the_kiss_count_and_stay_unique():
    result = ms.compute(_zigzag(cycles=5))

    assert len(set(result)) == len(result)
    for key in result:
        assert key.split("x")[0].isdigit(), key
        assert int(key.split("x")[0]) >= ms.MIN_KISSES


def test_every_emitted_label_has_a_colour():
    """Keys are generated, so a mismatch with COLORS would silently render
    every line in the loader's grey default."""
    result = ms.compute(_zigzag(cycles=5))

    assert result
    for key in result:
        assert key in ms.COLORS, f"{key} would fall back to grey"


def test_the_same_bars_give_the_same_labels():
    """Two runs relabelling the same lines would make the chart flicker
    between polls."""
    assert ms.compute(_zigzag(cycles=5)) == ms.compute(_zigzag(cycles=5))


def test_drawn_levels_are_kept_apart():
    ctx = _zigzag(cycles=6, dwell=11)
    result = ms.compute(ctx)
    tolerance = _band(ctx.hourly_bars)
    prices = sorted(result.values())

    for lower, upper in zip(prices, prices[1:]):
        assert upper - lower > tolerance * levels._SEPARATION_MULTIPLE


# --- which bars it reads ------------------------------------------------


def test_levels_come_from_the_anchor_not_the_minute_feed():
    """The minute feed is deliberately ignored. Here the two disagree by
    construction -- minute bars near 100, hourly near 500 -- and every level
    must come from the hourly series."""
    minute = _zigzag_pairs(cycles=5, low=100.0, high=110.0)
    hourly = _zigzag_pairs(cycles=5, low=500.0, high=560.0)
    ctx = build_context("X", minute, [], [], "1Min", hourly)

    result = ms.compute(ctx)

    assert result
    assert all(400 < price < 700 for price in result.values()), result


def test_the_same_levels_are_drawn_on_every_chart_below_the_anchor():
    """The whole point of anchoring. Marking a level on the hourly chart and
    dropping to 5m to time the entry only works if the line does not move --
    re-measuring per timeframe produced two disjoint sets on real data
    (MARA: 9.03-10.64 on minutes against 11.39-14.87 on hours)."""
    hourly = _zigzag_pairs(cycles=5)

    drawn = [
        ms.compute(build_context("X", _bars([(1.0, 1.0)] * 50), [], [], tf, hourly))
        for tf in ("1Min", "1Hour")
    ]

    assert drawn[0] == drawn[1]
    assert drawn[0]


# --- telling two levels from one zone -----------------------------------


def _arrays(pairs):
    return (
        np.array([h for h, _ in pairs], dtype=float),
        np.array([l for _, l in pairs], dtype=float),
    )


def _visits(price, times, away):
    """`times` separate visits to `price`, each followed by a trip to `away`."""
    pairs = []
    for _ in range(times):
        pairs += [(price, price)] * 3
        pairs += [(away, away)] * 3
    return pairs


def test_two_peaks_with_a_trough_between_them_are_separate_levels():
    """The MARA case: 6 visits at 11.00, a sag to 4 across 11.10-11.20, 6
    again at 11.40. Judging on distance alone threw the lower one away and
    drew a single line 0.40 above where price had been turning."""
    highs, lows = _arrays(_visits(11.00, 6, 20.0) + _visits(11.40, 6, 20.0))

    assert levels.is_separated(highs, lows, 11.00, 11.40, band=0.05)


def test_one_broad_shelf_is_not_split_in_two():
    """Price grinding across a whole band is one level, however wide."""
    highs, lows = _arrays(_visits(11.20, 8, 20.0))

    assert not levels.is_separated(highs, lows, 11.15, 11.25, band=0.30)


def test_levels_closer_than_one_band_are_never_both_drawn():
    """A trough can be real and the pair still undrawable -- two lines closer
    together than the band they are measured with overlap on screen."""
    ctx = _anchored(_zigzag_pairs(cycles=6, low=100.0, high=140.0, dwell=11))
    result = ms.compute(ctx)
    tolerance = _band(ctx.hourly_bars)
    prices = sorted(result.values())

    for lower, upper in zip(prices, prices[1:]):
        assert upper - lower > tolerance * levels._MIN_SEPARATION_MULTIPLE


# --- where the line lands -----------------------------------------------


def test_the_scan_moves_the_line_towards_the_busier_price():
    """A cluster's median says where its pivots sit, not where price kept
    turning. The drawn number is what a stop gets placed against, so it has
    to be the second.

    Note the scan can only discriminate where the profile actually varies:
    if the band is wide enough to cover the real level from anywhere in the
    zone, every candidate ties and the nominal centre stands. That is correct
    -- there is nothing to choose between them.
    """
    highs, lows = _arrays(_visits(100.0, 6, 130.0) + _visits(100.6, 3, 130.0))
    nominal, tolerance = 100.45, 0.25

    best = levels.best_in_zone(highs, lows, nominal, tolerance)

    assert best < nominal, "should slide towards the six-visit price, not the three"
    assert levels.count_visits(highs, lows, best, tolerance) > levels.count_visits(
        highs, lows, nominal, tolerance
    )


def test_the_scan_stays_inside_its_own_band():
    highs, lows = _arrays(_visits(100.0, 6, 130.0))

    best = levels.best_in_zone(highs, lows, level=105.0, band=0.5)

    assert 104.5 <= best <= 105.5


# --- the vectorised counter ---------------------------------------------


def test_counting_matches_a_plain_sequential_walk():
    """The counter was rewritten as a vectorised pass so the scans above
    could afford to call it hundreds of times. This pins the rewrite against
    the obvious implementation it replaced."""
    rng = np.random.default_rng(20260820)
    highs = 100 + rng.normal(0, 2, 400).cumsum() / 10
    lows = highs - rng.uniform(0.05, 0.4, 400)
    tolerance = 0.3
    departure = tolerance * levels._DEPARTURE_MULTIPLE

    for level in (99.0, 100.0, 101.0, 102.5):
        expected, armed = 0, True
        for high, low in zip(highs, lows):
            if low <= level + tolerance and high >= level - tolerance:
                if armed:
                    expected += 1
                    armed = False
            elif low > level + departure or high < level - departure:
                armed = True
        assert levels.count_visits(highs, lows, level, tolerance) == expected, level
