"""The opening range, and the breakout rule built on it.

Split in two because the two carry different risks. The range itself has one
failure mode that a backtest cannot show you -- measuring it from bars that
had not printed yet, which turns a peek at the future into what looks like a
signal -- so is_complete gets its own cases. The rule is mostly ordinary
threshold logic and is tested against the constants rather than against
literals, so calibration does not break the tests that say whether it works.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pytest

from app.market_data import opening_range as orange
from app.scanners.exit_rules import SIDE_LONG, SIDE_SHORT, STOP_ON_CLOSE
from app.services.market_clock import trading_hours_for
from app.strategies import opening_range_breakout as rule
from app.strategies.context import StrategyContext

# 2026-08-19 is a Wednesday: a full session, so the bell is 09:30 and the
# range is the 09:30-09:35 bar.
_DAY = date(2026, 8, 19)
_OPEN = trading_hours_for(_DAY)[0]


@dataclass
class _Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 1000.0


def _bar(minutes_after_open, open_, high, low, close):
    return _Bar(_OPEN + timedelta(minutes=minutes_after_open), open_, high, low, close)


def _opening_bar(high=10.5, low=10.0):
    """The range itself: the first five-minute bar of the session."""
    return _bar(0, 10.2, high, low, 10.3)


# Far enough either side to clear MIN_TARGET_R against a stop at the opposite
# end of the range: risking the full 0.50 range from an 11.00 entry needs a
# level at 13.00 or better. Sized deliberately, because a level that is merely
# *ahead* is not enough -- which is the trade-off this rule exists to test.
def _ctx(bars, levels=(7.0, 14.0)):
    return StrategyContext(
        symbol="AAA",
        bar=bars[-1],
        session_bars=bars,
        session_vwaps=[10.0] * len(bars),
        premarket_vwap=None,
        levels=levels,
    )


# --- the range ------------------------------------------------------------


def test_the_range_is_the_first_five_minutes():
    bars = [_opening_bar(), _bar(5, 10.3, 11.0, 10.25, 10.9)]

    assert orange.opening_range(bars, _DAY) == (10.5, 10.0)


def test_premarket_is_not_part_of_the_range():
    """It is about what the auction did. Premarket has its own range in
    session_marks."""
    bars = [_bar(-60, 9.0, 20.0, 8.0, 9.5), _opening_bar()]

    assert orange.opening_range(bars, _DAY) == (10.5, 10.0)


def test_bars_after_the_window_are_not_part_of_the_range():
    bars = [_opening_bar(), _bar(5, 10.3, 99.0, 1.0, 10.9)]

    assert orange.opening_range(bars, _DAY) == (10.5, 10.0)


def test_another_session_does_not_leak_in():
    other = _Bar(_OPEN + timedelta(days=1), 50.0, 60.0, 40.0, 55.0)
    bars = [_opening_bar(), other]

    assert orange.opening_range(bars, _DAY) == (10.5, 10.0)


def test_a_session_that_has_not_opened_has_no_range():
    assert orange.opening_range([_bar(-60, 9.0, 9.5, 8.5, 9.0)], _DAY) == (None, None)


def test_a_non_trading_day_has_no_range():
    saturday = date(2026, 8, 22)

    assert trading_hours_for(saturday) is None
    assert orange.opening_range([_opening_bar()], saturday) == (None, None)


def test_nothing_traded_reports_none_not_zero():
    """A zero would draw a level at the bottom of the chart and drag a target
    down to it."""
    assert orange.opening_range([], _DAY) == (None, None)


# --- the look-ahead guard -------------------------------------------------


def test_a_bar_inside_the_window_has_no_finished_range():
    """The failure a backtest cannot show you. At 09:33 the five-minute high
    so far is not the five-minute high, and a rule comparing against it is
    reading bars that had not printed."""
    assert orange.is_complete(_bar(3, 10.2, 10.4, 10.1, 10.3), _DAY) is False


def test_the_range_is_finished_at_the_moment_the_window_ends():
    assert orange.is_complete(_bar(5, 10.3, 11.0, 10.25, 10.9), _DAY) is True


def test_the_opening_bar_itself_does_not_qualify():
    """Otherwise every session's first bar trivially breaks its own high."""
    assert orange.is_complete(_opening_bar(), _DAY) is False


# --- the breakout ---------------------------------------------------------


def _break_long(minutes=5, close=11.0):
    return _bar(minutes, 10.4, close + 0.05, 10.35, close)


def _break_short(minutes=5, close=9.5):
    return _bar(minutes, 10.1, 10.15, close - 0.05, close)


def test_a_close_above_the_range_is_a_long_break():
    signal = rule.evaluate(_ctx([_opening_bar(), _break_long()]))

    assert signal is not None
    assert signal.side == SIDE_LONG
    assert signal.entry_price == pytest.approx(11.0)


def test_a_close_below_the_range_is_a_short_break():
    signal = rule.evaluate(_ctx([_opening_bar(), _break_short()]))

    assert signal is not None
    assert signal.side == SIDE_SHORT
    assert signal.stop_price > signal.entry_price


def test_the_stop_is_the_boundary_that_was_broken():
    """Not the far end of the range. With a close trigger this reads exactly:
    price closed back inside the opening range, so the break failed. The far
    end was measured first and risked the whole range -- 1.51% of price
    against a median 0.39R to the next level, which left 3 of 401 breaks
    tradeable."""
    signal = rule.evaluate(_ctx([_opening_bar(), _break_long()]))

    assert signal.stop_price == pytest.approx(10.5)


def test_a_short_stops_at_the_range_low_it_broke():
    signal = rule.evaluate(_ctx([_opening_bar(), _break_short()]))

    assert signal.stop_price == pytest.approx(10.0)


def test_the_target_is_the_next_level():
    signal = rule.evaluate(_ctx([_opening_bar(), _break_long()], levels=(7.0, 14.0, 20.0)))

    assert signal.target_price == 14.0


def test_the_break_is_read_on_the_close_not_the_wick():
    """The failure the opening range is most famous for: the poke through the
    high that closes back inside."""
    poke = _bar(5, 10.4, 12.0, 10.3, 10.4)

    assert rule.evaluate(_ctx([_opening_bar(), poke])) is None


def test_a_close_barely_past_the_level_is_not_a_break():
    just_over = _bar(5, 10.4, 10.55, 10.35, 10.5 + 10.5 * rule.BREAK_BUFFER_PCT / 2)

    assert rule.evaluate(_ctx([_opening_bar(), just_over])) is None


def test_the_setup_is_managed_the_way_the_method_says():
    signal = rule.evaluate(_ctx([_opening_bar(), _break_long()]))

    assert signal.stop_trigger == STOP_ON_CLOSE
    assert signal.scale_out == rule.SCALE_OUT


# --- what stops it firing -------------------------------------------------


def test_no_break_inside_the_range_is_no_signal():
    inside = _bar(5, 10.2, 10.45, 10.05, 10.3)

    assert rule.evaluate(_ctx([_opening_bar(), inside])) is None


def test_nothing_fires_before_the_range_is_finished():
    early = _bar(3, 10.4, 12.0, 10.3, 11.5)

    assert rule.evaluate(_ctx([_opening_bar(), early])) is None


def test_a_break_long_after_the_open_is_not_an_opening_range_break():
    late = _break_long(minutes=rule.OPENING_MINUTES + rule.BREAKOUT_WINDOW_MINUTES + 10)

    assert rule.evaluate(_ctx([_opening_bar(), late])) is None


def test_a_level_too_close_to_be_worth_the_range_is_skipped():
    """The trade-off this rule exists to test. Risking the whole opening
    range means the next level often is not far enough away."""
    signal = rule.evaluate(_ctx([_opening_bar(), _break_long()], levels=(11.5,)))

    assert signal is None


def test_no_level_ahead_means_no_trade():
    assert rule.evaluate(_ctx([_opening_bar(), _break_long()], levels=(7.0,))) is None


def test_a_flat_opening_bar_is_not_a_range():
    """Every close breaks a zero-height range, which measures nothing."""
    flat = _bar(0, 10.0, 10.0, 10.0, 10.0)

    assert rule.evaluate(_ctx([flat, _break_long()])) is None


def test_a_bar_that_covers_the_whole_range_is_read_by_its_close():
    """It traded through both ends, so only the close says which way it
    broke. The stop then sits a full range away, which is the cost of the
    placement rather than a defect."""
    engulfing = _bar(5, 10.4, 12.0, 9.0, 9.5)

    signal = rule.evaluate(_ctx([_opening_bar(), engulfing]))

    assert signal.side == SIDE_SHORT
    assert signal.entry_price == pytest.approx(9.5)
    assert signal.stop_price == pytest.approx(10.0)


# --- the contract ---------------------------------------------------------


def test_the_file_satisfies_the_loader_contract():
    from app.strategies.loader import load_strategies

    strategies, errors = load_strategies(only="opening_range_breakout")

    assert errors == []
    assert [s.name for s in strategies] == [rule.NAME]
