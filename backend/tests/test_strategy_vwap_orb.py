"""VWAP Open Range Break: the ORB whose break candle takes out VWAP too.

The whole ORB definition -- range, veto, window, stop, floor -- is shared
with opening_range_breakout and tested there; what these cases pin is the
one thing this rule adds, where the line may sit relative to the box and
the break close. The boundary cases matter most: a line left behind the
range is the plain ORB's trade, not this one's, and a line the close never
cleared is no VWAP break at all.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pytest

from app.scanners.exit_rules import SIDE_LONG, SIDE_SHORT
from app.services.market_clock import trading_hours_for
from app.strategies import vwap_open_range_break as rule
from app.strategies.context import StrategyContext

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
    """The range: 10.0 to 10.5."""
    return _bar(0, 10.2, 10.5, 10.0, 10.3)


def _break_long(close=11.0):
    return _bar(5, 10.4, close + 0.05, 10.35, close)


def _break_short(close=9.5):
    return _bar(5, 10.1, 10.15, close - 0.05, close)


def _ctx(bars, vwap, levels=(7.0, 14.0)):
    return StrategyContext(
        symbol="AAA",
        bar=bars[-1],
        session_bars=bars,
        session_vwaps=[vwap] * len(bars),
        premarket_vwap=None,
        levels=levels,
    )


# --- the double break -----------------------------------------------------


def test_a_break_with_vwap_inside_the_range_fires_long():
    signal = rule.evaluate(_ctx([_opening_bar(), _break_long()], vwap=10.2))

    assert signal is not None
    assert signal.side == SIDE_LONG
    assert signal.entry_price == pytest.approx(11.0)


def test_the_trade_is_the_orb_trade():
    """Same stop, same target: only the qualification differs, so the two
    rules stay genuinely comparable in a backtest."""
    signal = rule.evaluate(_ctx([_opening_bar(), _break_long()], vwap=10.2))

    assert signal.stop_price == pytest.approx(10.0)
    assert signal.target_price == pytest.approx(14.0)


def test_a_break_with_vwap_inside_the_range_fires_short():
    signal = rule.evaluate(_ctx([_opening_bar(), _break_short()], vwap=10.2))

    assert signal is not None
    assert signal.side == SIDE_SHORT


def test_vwap_between_boundary_and_close_still_counts():
    """The line just past the box, and the break candle crossed both: the
    close owns the boundary and the line together, which is the setup."""
    signal = rule.evaluate(_ctx([_opening_bar(), _break_long()], vwap=10.8))

    assert signal is not None


# --- what stops it firing -------------------------------------------------


def test_vwap_left_behind_the_range_is_the_plain_orbs_trade():
    """Long break with the line already below the box: price owned the VWAP
    side all along, so the range break broke nothing about the line."""
    assert rule.evaluate(_ctx([_opening_bar(), _break_long()], vwap=9.5)) is None


def test_vwap_left_above_the_range_refuses_a_short():
    assert rule.evaluate(_ctx([_opening_bar(), _break_short()], vwap=10.8)) is None


def test_a_close_that_never_cleared_the_line_is_no_vwap_break():
    assert rule.evaluate(_ctx([_opening_bar(), _break_long()], vwap=11.5)) is None


def test_a_close_exactly_on_the_line_is_not_a_break_of_it():
    assert rule.evaluate(_ctx([_opening_bar(), _break_long(close=11.0)], vwap=11.0)) is None


def test_an_unknowable_line_refuses_the_trade():
    """Opposite of the ATR veto's asymmetry, on purpose: the line being
    broken is this rule's whole subject, so without it the setup does not
    exist. The plain ORB still covers the break itself."""
    assert rule.evaluate(_ctx([_opening_bar(), _break_long()], vwap=None)) is None


def test_no_range_break_means_no_signal():
    inside = _bar(5, 10.2, 10.45, 10.05, 10.3)

    assert rule.evaluate(_ctx([_opening_bar(), inside], vwap=10.2)) is None


# --- the contract ---------------------------------------------------------


def test_the_file_satisfies_the_loader_contract():
    from app.strategies.loader import load_strategies

    strategies, errors = load_strategies(only="vwap_open_range_break")

    assert errors == []
    assert [s.name for s in strategies] == [rule.NAME]
