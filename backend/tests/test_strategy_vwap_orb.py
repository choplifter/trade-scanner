"""VWAP Open Range Break: the session VWAP itself crosses out of the box.

The cases pin what separates this rule from the whole ORB family: a price
candle breaking the box is NOT a signal here -- only the line crossing the
boundary is, and only on the bar it crosses. The _ctx helper therefore
takes the whole per-bar VWAP series: the rule reads the line's path, not
one value.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pytest

from app.scanners.exit_rules import SIDE_LONG, SIDE_SHORT
from app.services.market_clock import trading_hours_for
from app.strategies import breakout
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


def _drift_bar(minute, close=10.9):
    """Price above the box -- what drags the line up after a break."""
    return _bar(minute, close - 0.05, close + 0.05, close - 0.1, close)


def _ctx(bars, vwaps, levels=(7.0, 14.0), daily_atr=None):
    assert len(vwaps) == len(bars)
    return StrategyContext(
        symbol="AAA",
        bar=bars[-1],
        session_bars=bars,
        session_vwaps=vwaps,
        premarket_vwap=None,
        levels=levels,
        daily_atr=daily_atr,
    )


# Crossing scene: two bars in the box, price leaves at 09:40, the line
# follows and crosses the 10.5 high between the 09:45 and 09:50 bars.
# Above the shared buffer either side of the boundary, so the cases do not
# hinge on its exact value.
def _crossing_long():
    bars = [_opening_bar(), _bar(5, 10.3, 10.45, 10.2, 10.4), _drift_bar(10), _drift_bar(15), _drift_bar(20, close=11.0)]
    vwaps = [10.25, 10.3, 10.38, 10.46, 10.62]
    return bars, vwaps


def test_the_line_crossing_the_high_is_a_long():
    signal = rule.evaluate(_ctx(*_crossing_long()))

    assert signal is not None
    assert signal.side == SIDE_LONG
    assert signal.entry_price == pytest.approx(11.0)
    assert signal.stop_price == pytest.approx(10.0)
    assert "session VWAP crossed above the high" in signal.reason


def test_the_line_crossing_the_low_is_a_short():
    bars = [_opening_bar(), _bar(5, 10.2, 10.25, 9.6, 9.7), _bar(10, 9.7, 9.75, 9.4, 9.5), _bar(15, 9.5, 9.55, 9.2, 9.3)]
    vwaps = [10.25, 10.1, 10.01, 9.8]

    signal = rule.evaluate(_ctx(bars, vwaps, levels=(6.5, 14.0)))

    assert signal is not None
    assert signal.side == SIDE_SHORT
    assert signal.stop_price == pytest.approx(10.5)


def test_a_price_break_alone_is_not_this_signal():
    """The whole point of the correction: candles broke the box, the line
    did not -- that is the ORB's trade, not this rule's."""
    bars = [_opening_bar(), _drift_bar(5, close=11.0)]
    vwaps = [10.25, 10.35]

    assert rule.evaluate(_ctx(bars, vwaps)) is None


def test_only_the_crossing_bar_fires():
    """A line already outside the box is a state, not an event -- firing on
    it would hand the chart a drifting entry."""
    bars, vwaps = _crossing_long()
    bars.append(_drift_bar(25, close=11.1))
    vwaps.append(10.7)

    assert rule.evaluate(_ctx(bars, vwaps)) is None


def test_a_line_hovering_at_the_boundary_is_not_a_crossing():
    bars, vwaps = _crossing_long()
    vwaps[-1] = 10.5 * (1 + breakout.BREAK_BUFFER_PCT / 2)

    assert rule.evaluate(_ctx(bars, vwaps)) is None


def test_a_crossing_after_the_window_is_not_an_opening_trade():
    bars, vwaps = _crossing_long()
    late = 5 + breakout.BREAKOUT_WINDOW_MINUTES + 10
    bars[-1] = _drift_bar(late, close=11.0)

    assert rule.evaluate(_ctx(bars, vwaps)) is None


def test_nothing_fires_before_the_range_is_finished():
    bars = [_opening_bar()]

    assert rule.evaluate(_ctx(bars, [10.6])) is None


def test_the_atr_veto_applies_here_too():
    bars, vwaps = _crossing_long()
    atr = (10.5 - 10.0) / rule.orb.MAX_RANGE_ATR_FRACTION * 0.8

    assert rule.evaluate(_ctx(bars, vwaps, daily_atr=atr)) is None


def test_the_target_is_the_next_level():
    signal = rule.evaluate(_ctx(*_crossing_long(), levels=(7.0, 14.0, 20.0)))

    assert signal.target_price == pytest.approx(14.0)


def test_no_level_ahead_falls_back_to_the_measured_move():
    signal = rule.evaluate(_ctx(*_crossing_long(), levels=(7.0,)))

    assert signal is not None
    assert "measured-move" in signal.reason
    assert signal.target_price == pytest.approx(11.0 + breakout.MEASURED_MOVE_R * 1.0)


def test_a_close_back_beyond_the_far_end_has_nothing_to_size():
    """The crossing bar's close can sit anywhere -- a violent reversal can
    put it beyond the stop, and there is no risk distance left."""
    bars, vwaps = _crossing_long()
    bars[-1] = _bar(20, 10.2, 10.3, 9.7, 9.8)

    assert rule.evaluate(_ctx(bars, vwaps)) is None


# --- the contract ---------------------------------------------------------


def test_the_file_satisfies_the_loader_contract():
    from app.strategies.loader import load_strategies

    strategies, errors = load_strategies(only="vwap_open_range_break")

    assert errors == []
    assert [s.name for s in strategies] == [rule.NAME]
