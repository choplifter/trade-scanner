"""The ORB break variant: the breakout rule's setup, the wick stop.

Everything about the setup -- the range, the break, the ATR veto, the
window -- is opening_range_breakout's and is pinned in its own test file;
what this file pins is that the variant really is only the stop, and what
that stop is.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pytest

from app.services.market_clock import trading_hours_for
from app.strategies import breakout, orb_break as rule
from app.strategies import opening_range_breakout as orb
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
    return _bar(0, 10.2, 10.5, 10.0, 10.3)


def _break_long(minutes=5, close=11.0):
    return _bar(minutes, 10.4, close + 0.05, 10.35, close)


def _ctx(bars, levels=(7.0, 14.0), daily_atr=None):
    return StrategyContext(
        symbol="AAA",
        bar=bars[-1],
        session_bars=bars,
        session_vwaps=[10.0] * len(bars),
        premarket_vwap=None,
        levels=levels,
        daily_atr=daily_atr,
    )


def test_the_stop_is_the_break_bars_own_wick():
    """The far-end rule stops this same trade at 10.0, the range low; the
    variant stops it at the print the break rose from."""
    signal = rule.evaluate(_ctx([_opening_bar(), _break_long()]))

    assert signal is not None
    assert signal.stop_price == pytest.approx(10.35)
    assert orb.evaluate(_ctx([_opening_bar(), _break_long()])).stop_price == pytest.approx(10.0)


def test_a_short_stops_at_the_break_bars_high():
    short_break = _bar(5, 10.1, 10.15, 9.4, 9.5)

    signal = rule.evaluate(_ctx([_opening_bar(), short_break]))

    assert signal is not None
    assert signal.stop_price == pytest.approx(10.15)


def test_a_break_bar_closing_on_its_own_wick_is_skipped():
    """close == low leaves nothing to size against -- the case the far-end
    placement cannot produce."""
    on_the_low = _bar(5, 11.2, 11.3, 11.0, 11.0)

    assert rule.evaluate(_ctx([_opening_bar(), on_the_low])) is None


def test_everything_but_the_stop_is_the_breakout_rules():
    """Same entry, same target, same management -- one setup, two stops."""
    variant = rule.evaluate(_ctx([_opening_bar(), _break_long()]))
    original = orb.evaluate(_ctx([_opening_bar(), _break_long()]))

    assert variant.entry_price == original.entry_price
    assert variant.target_price == original.target_price
    assert variant.side == original.side
    assert variant.stop_trigger == original.stop_trigger
    assert variant.scale_out == original.scale_out


def test_the_atr_veto_binds_here_too():
    atr = (10.5 - 10.0) / orb.MAX_RANGE_ATR_FRACTION * 0.8

    assert rule.evaluate(_ctx([_opening_bar(), _break_long()], daily_atr=atr)) is None


def test_the_file_satisfies_the_loader_contract():
    from app.strategies.loader import load_strategies

    strategies, errors = load_strategies(only="orb_break")

    assert errors == []
    assert [s.name for s in strategies] == [rule.NAME]
