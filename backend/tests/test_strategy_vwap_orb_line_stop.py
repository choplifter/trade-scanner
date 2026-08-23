"""The line-stop variant of the VWAP crossing rule.

The crossing grammar itself is pinned in test_strategy_vwap_orb against
the shared implementation; what is this file's own is the one choice it
makes -- the stop at the line instead of the far end -- and the skip that
choice creates when the crossing bar closes on the line itself.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pytest

from app.scanners.exit_rules import SIDE_LONG
from app.services.market_clock import trading_hours_for
from app.strategies import vwap_orb_line_stop as rule
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


def _crossing_long():
    """The scene from test_strategy_vwap_orb: box 10.0-10.5, the line
    crosses the high between the last two bars, entry close 11.0."""
    bars = [
        _bar(0, 10.2, 10.5, 10.0, 10.3),
        _bar(5, 10.3, 10.45, 10.2, 10.4),
        _bar(10, 10.85, 10.95, 10.8, 10.9),
        _bar(15, 10.85, 10.95, 10.8, 10.9),
        _bar(20, 10.9, 11.05, 10.85, 11.0),
    ]
    vwaps = [10.25, 10.3, 10.38, 10.46, 10.62]
    return bars, vwaps


def _ctx(bars, vwaps, levels=(7.0, 14.0)):
    return StrategyContext(
        symbol="AAA",
        bar=bars[-1],
        session_bars=bars,
        session_vwaps=vwaps,
        premarket_vwap=None,
        levels=levels,
    )


def test_the_stop_is_where_the_line_stood_at_entry():
    signal = rule.evaluate(_ctx(*_crossing_long()))

    assert signal is not None
    assert signal.side == SIDE_LONG
    assert signal.entry_price == pytest.approx(11.0)
    assert signal.stop_price == pytest.approx(10.62)


def test_risk_is_the_distance_to_the_line_not_the_box():
    signal = rule.evaluate(_ctx(*_crossing_long()))

    assert signal.risk_per_share == pytest.approx(11.0 - 10.62)


def test_a_close_on_the_line_has_nothing_to_size():
    """The doji-at-the-line case the far-end sibling never sees."""
    bars, vwaps = _crossing_long()
    bars[-1] = _bar(20, 10.65, 10.7, 10.55, vwaps[-1])

    assert rule.evaluate(_ctx(bars, vwaps)) is None


def test_the_setup_is_the_shared_crossing():
    """A price break without the line leaving the box is no signal here
    either -- the setup lives once, in vwap_open_range_break."""
    bars, vwaps = _crossing_long()
    vwaps[-1] = 10.45

    assert rule.evaluate(_ctx(bars, vwaps)) is None


def test_the_file_satisfies_the_loader_contract():
    from app.strategies.loader import load_strategies

    strategies, errors = load_strategies(only="vwap_orb_line_stop")

    assert errors == []
    assert [s.name for s in strategies] == [rule.NAME]
