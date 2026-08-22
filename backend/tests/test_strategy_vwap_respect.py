"""The VWAP-respect rule.

Written against hand-built bars, because the point of every case here is a
shape -- a wick that reached the line, a body that closed through it, an
entry that drifted too far from it -- and a shape is far easier to state in
four numbers than to find in real data.

The rule's *numbers* are constructions and will move under calibration, so
these tests are written against the constants rather than against literals.
A test that hardcoded 2 tests or 0.4% would fail on the first calibration run
and say nothing about whether the rule still worked.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest

from app.scanners.exit_rules import SIDE_LONG, SIDE_SHORT, STOP_ON_CLOSE
from app.services.market_clock import ET
from app.strategies import vwap_respect as rule
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


def _touch_long(minute):
    """A bar whose low reached the line and whose body stayed above it."""
    return _bar(minute, 100.3, 100.5, _VWAP, 100.35)


def _touch_short(minute):
    """The mirror: a high that reached the line, a body below it."""
    return _bar(minute, 99.7, _VWAP, 99.5, 99.65)


def _confirm_long(minute=50, close=100.2):
    return _bar(minute, 100.05, close + 0.05, 100.0, close)


def _confirm_short(minute=50, close=99.8):
    return _bar(minute, 99.95, 100.0, close - 0.05, close)


# One level either side, each far enough past a ~0.3 risk unit to clear
# MIN_TARGET_R. Supplied by default so the cases that are not about targets
# do not have to restate them.
_LEVELS = (99.0, 101.0)


def _ctx(bars, vwaps=None, levels=_LEVELS):
    """A context whose VWAP is flat at _VWAP, which is what lets each case
    say one thing: with the line moving, a 'test' would also be a statement
    about the accumulator."""
    if vwaps is None:
        vwaps = [_VWAP] * len(bars)
    return StrategyContext(
        symbol="AAA",
        bar=bars[-1],
        session_bars=bars,
        session_vwaps=vwaps,
        premarket_vwap=None,
        levels=levels,
    )


def _respected_long(n=None):
    return [_touch_long(i) for i in range(n if n is not None else rule.MIN_TESTS)]


# --- the setup fires -----------------------------------------------------


def test_respect_then_confirmation_goes_long():
    signal = rule.evaluate(_ctx([*_respected_long(), _confirm_long()]))

    assert signal is not None
    assert signal.side == SIDE_LONG
    assert signal.entry_price == pytest.approx(100.2)


def test_the_mirror_setup_goes_short():
    """The reason Signal and simulate_exit learned a side at all: this rule
    reads a level for rejection, and a level rejects from both sides."""
    bars = [_touch_short(i) for i in range(rule.MIN_TESTS)] + [_confirm_short()]

    signal = rule.evaluate(_ctx(bars))

    assert signal is not None
    assert signal.side == SIDE_SHORT
    assert signal.stop_price > signal.entry_price
    assert signal.target_price < signal.entry_price


def test_the_stop_sits_just_beyond_the_line():
    signal = rule.evaluate(_ctx([*_respected_long(), _confirm_long()]))

    assert signal.stop_price == pytest.approx(_VWAP * (1 - rule.STOP_BUFFER_PCT))
    assert signal.stop_price < _VWAP


def test_the_stop_is_read_on_the_close_not_on_a_touch():
    """The mechanic the whole measurement hangs on. An entry cents from its
    stop, scored on touch, is stopped out by noise -- the backtest would then
    report a strategy nobody described."""
    signal = rule.evaluate(_ctx([*_respected_long(), _confirm_long()]))

    assert signal.stop_trigger == STOP_ON_CLOSE


def test_half_comes_off_at_the_target():
    signal = rule.evaluate(_ctx([*_respected_long(), _confirm_long()]))

    assert signal.scale_out == rule.SCALE_OUT
    assert 0 < signal.scale_out < 1


def test_the_target_is_the_next_level_not_a_multiple_of_risk():
    """The method, not a setting. An R multiple put the target wherever the
    stop happened to be -- and the stop here is cents from VWAP, so it aimed
    at a price nothing about the stock had chosen."""
    signal = rule.evaluate(_ctx([*_respected_long(), _confirm_long()]))

    assert signal.target_price == 101.0


def test_the_target_is_the_nearest_level_ahead():
    """Not the strongest and not the furthest -- price has to get through the
    near one first, so that is the one the trade is aimed at."""
    bars = [*_respected_long(), _confirm_long()]

    signal = rule.evaluate(_ctx(bars, levels=(99.0, 101.0, 104.0)))

    assert signal.target_price == 101.0


def test_a_short_is_aimed_at_the_nearest_level_below():
    bars = [_touch_short(i) for i in range(rule.MIN_TESTS)] + [_confirm_short()]

    signal = rule.evaluate(_ctx(bars, levels=(96.0, 99.0, 101.0)))

    assert signal.side == SIDE_SHORT
    assert signal.target_price == 99.0


def test_no_level_ahead_means_no_trade():
    """The method has no fallback target, so neither does this. Inventing one
    would measure a rule nobody described."""
    bars = [*_respected_long(), _confirm_long()]

    assert rule.evaluate(_ctx(bars, levels=(99.0,))) is None
    assert rule.evaluate(_ctx(bars, levels=())) is None


def test_a_level_too_close_to_be_worth_the_risk_is_skipped():
    """A real level and still a bad trade: risking one to make a fraction is
    exactly what the small stop exists to avoid."""
    bars = [*_respected_long(), _confirm_long()]

    signal = rule.evaluate(_ctx(bars, levels=(100.25,)))

    assert signal is None


def test_a_level_just_past_the_floor_is_taken():
    """The other side of the same boundary, so the floor is a threshold
    rather than a blanket refusal."""
    bars = [*_respected_long(), _confirm_long()]
    entry, stop = 100.2, _VWAP * (1 - rule.STOP_BUFFER_PCT)
    just_far_enough = entry + (entry - stop) * rule.MIN_TARGET_R + 0.01

    signal = rule.evaluate(_ctx(bars, levels=(just_far_enough,)))

    assert signal is not None
    assert signal.reward_ratio >= rule.MIN_TARGET_R


def test_the_risk_is_small_because_the_entry_hugs_the_line():
    """Aziz's stated reason for entering there at all -- so it is worth
    pinning that the rule actually produces a small stop distance rather than
    merely aiming to."""
    signal = rule.evaluate(_ctx([*_respected_long(), _confirm_long()]))

    assert signal.stop_pct < rule.MAX_ENTRY_DISTANCE_PCT + rule.STOP_BUFFER_PCT


# --- what stops it firing -------------------------------------------------


def test_too_few_tests_is_not_respect():
    bars = [*_respected_long(rule.MIN_TESTS - 1), _confirm_long()]

    assert rule.evaluate(_ctx(bars)) is None


def test_a_body_closing_through_the_line_resets_the_count():
    """The distinction the rule turns on: price that closed on the other side
    tested nothing, it broke the level."""
    broke = _bar(2, 100.2, 100.3, 99.0, 99.2)
    bars = [*_respected_long(), broke, _confirm_long()]

    assert rule.evaluate(_ctx(bars)) is None


def test_respect_rebuilt_after_a_break_counts_again():
    """And the level can re-form -- otherwise one wick would disqualify a
    symbol for the rest of the session."""
    broke = _bar(2, 100.2, 100.3, 99.0, 99.2)
    bars = [*_respected_long(), broke, *[_touch_long(i) for i in range(3, 3 + rule.MIN_TESTS)]]
    bars.append(_confirm_long())

    assert rule.evaluate(_ctx(bars)) is not None


def test_an_entry_too_far_from_the_line_is_skipped():
    """Not taken at worse risk -- the whole setup is the small stop."""
    far = _VWAP * (1 + rule.MAX_ENTRY_DISTANCE_PCT * 2)
    bars = [*_respected_long(), _confirm_long(close=far)]

    assert rule.evaluate(_ctx(bars)) is None


def test_a_confirmation_bar_must_close_in_its_own_direction():
    """A red bar closing above VWAP confirms nothing: the side of the line
    says where price is, the body says where it is going."""
    red_above = _bar(50, 100.3, 100.35, 100.1, 100.15)
    bars = [*_respected_long(), red_above]

    assert rule.evaluate(_ctx(bars)) is None


def test_a_confirmation_on_the_wrong_side_of_the_line_is_not_a_long():
    green_below = _bar(50, 99.5, 99.9, 99.4, 99.8)
    bars = [*_respected_long(), green_below]

    signal = rule.evaluate(_ctx(bars))

    assert signal is None or signal.side == SIDE_SHORT


def test_nothing_fires_before_the_session_has_a_vwap():
    """Premarket bars carry None, and a rule about a line cannot run before
    the line exists."""
    bars = [*_respected_long(), _confirm_long()]

    assert rule.evaluate(_ctx(bars, vwaps=[None] * len(bars))) is None


def test_nothing_fires_once_the_opening_window_has_passed():
    """The method watches the open. A setup at 14:00 is a different trade."""
    late = _confirm_long(minute=rule.OPENING_WINDOW_MINUTES + 5)
    bars = [*_respected_long(), late]

    assert rule.evaluate(_ctx(bars)) is None


def test_the_window_is_measured_from_the_first_session_bar():
    """Not from a literal 09:30, so a late first print or a half day does not
    silently move it. Here the premarket bars carry no VWAP, so the clock
    starts at the first one that does."""
    premarket = [_bar(0, 99.0, 99.1, 98.9, 99.0), _bar(5, 99.0, 99.1, 98.9, 99.0)]
    session = [*_respected_long(), _confirm_long(minute=rule.OPENING_WINDOW_MINUTES - 5)]
    bars = premarket + session
    vwaps = [None] * len(premarket) + [_VWAP] * len(session)

    assert rule.evaluate(_ctx(bars, vwaps)) is not None


# --- the contract ---------------------------------------------------------


def test_the_file_satisfies_the_loader_contract():
    """It is discovered by name from disk, so a rename here is silent until
    something looks for it."""
    from app.strategies.loader import load_strategies

    strategies, errors = load_strategies(only="vwap_respect")

    assert errors == []
    assert [s.name for s in strategies] == [rule.NAME]
