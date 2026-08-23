"""The measured-move fallback: what the break rules aim at when nothing is
mapped above the entry.

Born from a real miss: UEC broke its opening range cleanly on a new-high
day, VWAP inside the box, and produced nothing -- every known level sat
below the entry. The strongest breakouts have, by definition, no structure
above them, so "no level ahead means no trade" unlisted exactly the days
the rules exist for.

The boundary these cases guard hardest: the fallback covers *no* level
ahead only. A level ahead but too near is a wall in the trade's path, and a
constructed target beyond it would pretend the wall is not there -- that
refusal must survive the fallback being on.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pytest

from app.services.market_clock import trading_hours_for
from app.strategies import breakout, switches
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
    """The range: 10.0 to 10.5."""
    return _bar(0, 10.2, 10.5, 10.0, 10.3)


def _break_long(close=11.0):
    return _bar(5, 10.4, close + 0.05, 10.35, close)


def _ctx(bars, levels=()):
    return StrategyContext(
        symbol="AAA",
        bar=bars[-1],
        session_bars=bars,
        session_vwaps=[10.2] * len(bars),
        premarket_vwap=None,
        levels=levels,
    )


def test_no_level_ahead_gets_a_measured_move_target():
    """Entry 11.0, stop 10.0, risk 1.0 -> target at entry + 2R = 13.0."""
    signal = orb.evaluate(_ctx([_opening_bar(), _break_long()], levels=()))

    assert signal is not None
    assert signal.target_price == pytest.approx(11.0 + breakout.MEASURED_MOVE_R * 1.0)
    assert signal.reward_ratio == pytest.approx(breakout.MEASURED_MOVE_R)
    assert "measured-move" in signal.reason


def test_levels_only_behind_the_entry_count_as_none_ahead():
    """The UEC shape: levels exist, all below a long entry."""
    signal = orb.evaluate(_ctx([_opening_bar(), _break_long()], levels=(9.0, 10.4)))

    assert signal is not None
    assert "measured-move" in signal.reason


def test_a_real_level_ahead_still_wins_over_the_construction():
    signal = orb.evaluate(_ctx([_opening_bar(), _break_long()], levels=(14.0,)))

    assert signal.target_price == pytest.approx(14.0)
    assert "target level" in signal.reason


def test_a_too_near_level_still_refuses_even_with_the_fallback_on():
    """The boundary that matters: a known level in the path is a wall, and a
    constructed target beyond it would pretend the wall is not there."""
    assert orb.evaluate(_ctx([_opening_bar(), _break_long()], levels=(11.5,))) is None


def test_switched_off_restores_no_level_no_trade():
    switches.set_measured_move_target(False)

    assert orb.evaluate(_ctx([_opening_bar(), _break_long()], levels=())) is None


def test_the_switch_defaults_to_on():
    assert switches.measured_move_target_enabled() is True


def test_the_setting_survives_strategy_switching():
    """One JSON file carries both; flipping a strategy must not reset the
    setting, and the setting key must never read as a strategy stem."""
    switches.set_measured_move_target(False)
    switches.set_switched("orb_break", False)
    switches.set_switched("orb_break", True)

    assert switches.measured_move_target_enabled() is False
    assert switches.switched_off() == set()


def test_the_setting_key_is_not_a_strategy_switch():
    switches.set_measured_move_target(False)

    assert switches.switched_off() == set()
