"""The ORB retest rule: the opening-range boundary, broken, held, returned to.

The retest grammar itself (holds, invalidation, the wick stop) is pinned in
test_strategy_vwap_retest against the shared implementation; what is this
file's own is the line -- that the boundary is the completed opening range's,
that the break is the breakout rule's own break, and that the window closes.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pytest

from app.scanners.exit_rules import SIDE_LONG, SIDE_SHORT
from app.services.market_clock import trading_hours_for
from app.strategies import breakout, orb_retest as rule
from app.strategies.context import StrategyContext

# 2026-08-19 is a Wednesday: a full session, bell at 09:30, so the range is
# the 09:30-09:35 bar. Range here: 10.5 / 10.0.
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


def _opening_bar():
    return _bar(0, 10.2, 10.5, 10.0, 10.3)


def _break_long(minute=5):
    """Closed clearly above the 10.5 boundary -- past the shared buffer."""
    return _bar(minute, 10.4, 10.65, 10.35, 10.6)


def _hold_long(minute):
    return _bar(minute, 10.6, 10.75, 10.55, 10.7)


def _retest_long(minute=None):
    """The wick came back to the broken high, the body stayed above it."""
    minute = 10 + 5 * rule.MIN_HOLD_BARS if minute is None else minute
    return _bar(minute, 10.65, 10.7, 10.5, 10.62)


def _setup_long(holds=None):
    holds = rule.MIN_HOLD_BARS if holds is None else holds
    bars = [_opening_bar(), _break_long()]
    bars += [_hold_long(10 + 5 * i) for i in range(holds)]
    bars.append(_retest_long(10 + 5 * holds))
    return bars


_LEVELS = (9.0, 11.0)


def _ctx(bars, levels=_LEVELS):
    return StrategyContext(
        symbol="AAA",
        bar=bars[-1],
        session_bars=bars,
        session_vwaps=[10.0] * len(bars),
        premarket_vwap=None,
        levels=levels,
    )


# --- the setup ------------------------------------------------------------


def test_break_hold_and_retest_of_the_high_is_a_long():
    signal = rule.evaluate(_ctx(_setup_long()))

    assert signal is not None
    assert signal.side == SIDE_LONG
    assert signal.entry_price == pytest.approx(10.62)
    assert signal.stop_price == pytest.approx(10.5)  # the retest bar's wick
    assert signal.target_price == 11.0


def test_the_mirror_below_the_range_low_is_a_short():
    bars = [
        _opening_bar(),
        _bar(5, 10.1, 10.15, 9.85, 9.9),  # broke the 10.0 low, clearly
        *[_bar(10 + 5 * i, 9.9, 9.92, 9.75, 9.8) for i in range(rule.MIN_HOLD_BARS)],
        _bar(10 + 5 * rule.MIN_HOLD_BARS, 9.85, 10.0, 9.8, 9.88),  # retest from below
    ]

    signal = rule.evaluate(_ctx(bars))

    assert signal is not None
    assert signal.side == SIDE_SHORT
    assert signal.stop_price == pytest.approx(10.0)


def test_the_break_behind_the_retest_is_the_breakout_rules_own_break():
    """One break, two entries: a close the Opening Range Breakout would not
    trade (inside its buffer) must not become a break here either."""
    barely = 10.5 * (1 + breakout.BREAK_BUFFER_PCT / 2)
    bars = _setup_long()
    bars[1] = _bar(5, 10.4, barely + 0.05, 10.35, barely)

    assert rule.evaluate(_ctx(bars)) is None


# --- what stops it firing -------------------------------------------------


def test_too_few_holding_bars_is_no_setup():
    assert rule.evaluate(_ctx(_setup_long(holds=rule.MIN_HOLD_BARS - 1))) is None


def test_a_close_back_inside_the_range_invalidates_the_break():
    bars = _setup_long()
    bars[3] = _bar(15, 10.55, 10.6, 10.3, 10.4)  # gave the boundary back

    assert rule.evaluate(_ctx(bars)) is None


def test_an_unbroken_boundary_has_nothing_to_retest():
    """Price wicked the high from inside all morning: no break, no setup."""
    bars = [
        _opening_bar(),
        *[_bar(5 + 5 * i, 10.3, 10.5, 10.25, 10.4) for i in range(rule.MIN_HOLD_BARS + 1)],
        _retest_long(30),
    ]

    assert rule.evaluate(_ctx(bars)) is None


def test_a_wick_that_stays_away_from_the_boundary_is_not_a_retest():
    far = 10.5 * (1 + rule.TEST_BAND_PCT) + 0.05
    bars = _setup_long()
    bars[-1] = _bar(10 + 5 * rule.MIN_HOLD_BARS, 10.7, 10.75, far, 10.72)

    assert rule.evaluate(_ctx(bars)) is None


def test_the_window_closes_on_the_retest():
    late = rule.OPENING_MINUTES + breakout.BREAKOUT_WINDOW_MINUTES + 10
    bars = _setup_long()
    bars[-1] = _retest_long(late)

    assert rule.evaluate(_ctx(bars)) is None


def test_a_flat_range_measures_nothing():
    bars = _setup_long()
    bars[0] = _bar(0, 10.0, 10.0, 10.0, 10.0)

    assert rule.evaluate(_ctx(bars)) is None


# --- the contract ---------------------------------------------------------


def test_the_file_satisfies_the_loader_contract():
    from app.strategies.loader import load_strategies

    strategies, errors = load_strategies(only="orb_retest")

    assert errors == []
    assert [s.name for s in strategies] == [rule.NAME]
