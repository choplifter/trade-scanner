"""The VWAP break-and-retest rule.

Hand-built bars against a flat line, same harness as the respect rule and
for the same reason: every case here is a shape -- a break that was not
clear, a hold that was too short, a close back through the line -- and a
shape is easier to state in four numbers than to find in data.

Written against the constants, not literals, so calibration does not break
the tests that say whether the rule still works.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest

from app.scanners.exit_rules import SIDE_LONG, SIDE_SHORT, STOP_ON_CLOSE
from app.services.market_clock import ET
from app.strategies import vwap_retest as rule
from app.strategies.context import StrategyContext

_VWAP = 100.0
_OPEN = datetime(2026, 8, 19, 10, 0, tzinfo=ET)


@dataclass
class _Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 1000.0


def _bar(minute, open_, high, low, close):
    return _Bar(_OPEN + timedelta(minutes=minute), open_, high, low, close)


# The long-side shapes, stated once. Shorts are the mirror around the line.


def _below(minute):
    """A bar on the pre-break side of the line."""
    return _bar(minute, 99.7, 99.9, 99.5, 99.6)


def _break_long(minute, close=100.25):
    """Crossed the line and closed clearly beyond it -- the default close
    sits past BREAK_BUFFER_PCT of the flat 100 line."""
    return _bar(minute, 99.9, close + 0.05, 99.85, close)


def _hold_long(minute):
    """Held the new side, away from the line."""
    return _bar(minute, 100.3, 100.6, 100.25, 100.5)


def _retest_long(minute, close=100.2):
    """The wick came back to the line, the body stayed above it."""
    return _bar(minute, 100.35, close + 0.05, _VWAP, close)


def _setup_long(holds=None):
    holds = rule.MIN_HOLD_BARS if holds is None else holds
    bars = [_below(0), _break_long(5)]
    bars += [_hold_long(10 + 5 * i) for i in range(holds)]
    bars.append(_retest_long(10 + 5 * holds))
    return bars


# One level either side, far enough past a ~0.3 risk unit to clear
# MIN_TARGET_R, so cases not about targets need not restate them.
_LEVELS = (99.0, 101.0)


def _ctx(bars, levels=_LEVELS):
    return StrategyContext(
        symbol="AAA",
        bar=bars[-1],
        session_bars=bars,
        session_vwaps=[_VWAP] * len(bars),
        premarket_vwap=None,
        levels=levels,
    )


# --- the setup ------------------------------------------------------------


def test_break_hold_and_retest_is_a_long():
    signal = rule.evaluate(_ctx(_setup_long()))

    assert signal is not None
    assert signal.side == SIDE_LONG
    assert signal.entry_price == pytest.approx(100.2)
    assert signal.target_price == 101.0


def test_the_stop_is_the_retest_bars_own_wick():
    """Calibrated there by the user after the first measurement: a stop
    measured off the line sat cents from the entry and cost ate the trade.
    The wick is the print the market just defended."""
    signal = rule.evaluate(_ctx(_setup_long()))

    assert signal.stop_price == pytest.approx(_setup_long()[-1].low)


def test_the_mirror_is_a_short():
    bars = [
        _bar(0, 100.3, 100.5, 100.1, 100.4),
        _bar(5, 100.1, 100.15, 99.7, 99.75),  # broke down, clearly
        *[_bar(10 + 5 * i, 99.7, 99.75, 99.4, 99.5) for i in range(rule.MIN_HOLD_BARS)],
        _bar(10 + 5 * rule.MIN_HOLD_BARS, 99.65, _VWAP, 99.75, 99.8),  # retest from below
    ]

    signal = rule.evaluate(_ctx(bars))

    assert signal is not None
    assert signal.side == SIDE_SHORT
    assert signal.stop_price == pytest.approx(bars[-1].high)


def test_a_bar_closing_on_its_own_wick_has_nothing_to_size_against():
    """close == low: the stop and the entry are the same print. Above the
    line, inside the band -- everything else about the retest holds."""
    bars = _setup_long()
    bars[-1] = _bar(30, 100.1, 100.15, 100.05, 100.05)

    assert rule.evaluate(_ctx(bars)) is None


def test_the_setup_is_managed_the_way_the_method_says():
    signal = rule.evaluate(_ctx(_setup_long()))

    assert signal.stop_trigger == STOP_ON_CLOSE
    assert signal.scale_out == rule.SCALE_OUT


# --- what stops it firing -------------------------------------------------


def test_too_few_holding_bars_is_no_setup():
    """The user's "at least 2-3 candles on the other side" -- one is not
    a hold, it is a wobble."""
    assert rule.evaluate(_ctx(_setup_long(holds=rule.MIN_HOLD_BARS - 1))) is None


def test_a_side_held_all_session_was_never_broken():
    """A stock above VWAP from the first print has nothing to retest --
    the setup needs the break, not just the side."""
    bars = [_hold_long(5 * i) for i in range(4)] + [_retest_long(25)]

    assert rule.evaluate(_ctx(bars)) is None


def test_a_close_back_through_the_line_invalidates_the_break():
    """Once a body has closed back on the old side, the line was not
    defended -- whatever is retested afterwards is not this setup."""
    bars = [
        _below(0),
        _break_long(5),
        _hold_long(10),
        _below(15),  # the give-back
        _break_long(20),
        _hold_long(25),  # only one hold since the *new* break
        _retest_long(30),
    ]

    assert rule.evaluate(_ctx(bars)) is None


def test_an_unclear_break_close_is_not_a_break():
    """"Deutlich": a close a whisker past the line is the minimum cross
    the buffer exists to exclude."""
    barely = _VWAP * (1 + rule.BREAK_BUFFER_PCT / 2)
    bars = _setup_long()
    bars[1] = _break_long(5, close=barely)

    assert rule.evaluate(_ctx(bars)) is None


def test_a_wick_that_stays_away_from_the_line_is_not_a_retest():
    far = _VWAP * (1 + rule.TEST_BAND_PCT) + 0.05
    bars = _setup_long()
    bars[-1] = _bar(30, 100.4, 100.5, far, 100.45)

    assert rule.evaluate(_ctx(bars)) is None


def test_a_retest_that_closes_through_the_line_is_the_break_failing():
    bars = _setup_long()
    bars[-1] = _bar(30, 100.1, 100.2, 99.6, 99.7)

    assert rule.evaluate(_ctx(bars)) is None


def test_an_entry_too_far_from_the_line_is_skipped():
    """The wick came back but the body did not: the risk is no longer the
    small one the setup describes."""
    far = _VWAP * (1 + rule.MAX_ENTRY_DISTANCE_PCT) + 0.05
    bars = _setup_long()
    bars[-1] = _bar(30, far - 0.1, far + 0.1, _VWAP, far)

    assert rule.evaluate(_ctx(bars)) is None


def test_a_level_nearer_than_the_floor_disqualifies():
    assert rule.evaluate(_ctx(_setup_long(), levels=(100.5,))) is None


def test_no_level_ahead_means_no_trade():
    assert rule.evaluate(_ctx(_setup_long(), levels=(99.0,))) is None


# --- the contract ---------------------------------------------------------


def test_the_file_satisfies_the_loader_contract():
    from app.strategies.loader import load_strategies

    strategies, errors = load_strategies(only="vwap_retest")

    assert errors == []
    assert [s.name for s in strategies] == [rule.NAME]
