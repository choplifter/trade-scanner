"""The shared context and signal.

Two things here are load-bearing. build_context is the single place both the
live path and the backtest derive a strategy's inputs -- the whole reason it
exists is that sharing only the *decision* (formulas.is_momentum_alert) still
let the two sides derive different inputs, which is how the momentum
backtest's trailing window silently lost its slack. And Signal refuses a stop
that is not below the entry, because that stop is the denominator of R and a
zero or negative one would either crash ExitRule one layer down or, worse,
size a live position off a nonsense risk distance.
"""

from dataclasses import dataclass
from datetime import datetime

import pytest

from app.services.market_clock import ET
from app.strategies.context import Signal, build_context


@dataclass
class _Bar:
    timestamp: datetime
    open: float = 10.0
    high: float = 10.5
    low: float = 9.5
    close: float = 10.0
    volume: float = 1000.0


def _at(hour, minute, day=19):
    """A bar at an ET wall-clock time on a known weekday (2026-08-19 is a
    Wednesday, a regular full trading day)."""
    return _Bar(timestamp=datetime(2026, 8, day, hour, minute, tzinfo=ET))


def _ctx(bars, index, vwaps=None, premarket_vwap=None):
    """build_context with the vwap series defaulted to the right length.

    Alignment is the contract build_context enforces, so the tests that are
    not *about* alignment should not have to restate it.
    """
    if vwaps is None:
        vwaps = [None] * len(bars)
    return build_context("AAA", bars, index, vwaps, premarket_vwap)


def _signal(**overrides):
    kwargs = {
        "strategy": "test",
        "entry_price": 10.0,
        "stop_price": 9.5,
        "target_price": 11.0,
        "reason": "because",
    }
    kwargs.update(overrides)
    return Signal(**kwargs)


# --- the signal's arithmetic ---------------------------------------------


def test_risk_is_the_distance_to_the_stop():
    """The same quantity trading.sizing.shares_for_risk sizes from -- which
    is what makes a backtest R and a live position describe one trade."""
    assert _signal().risk_per_share == pytest.approx(0.5)
    assert _signal().stop_pct == pytest.approx(0.05)


def test_the_reward_ratio_comes_from_the_structural_target():
    """Not a configured constant: a rule aiming at the next level reports
    the ratio that level implies. 1.0 up against 0.5 risked is 2R."""
    assert _signal().reward_ratio == pytest.approx(2.0)


def test_an_asymmetric_target_reports_its_real_ratio():
    assert _signal(target_price=10.25).reward_ratio == pytest.approx(0.5)


# --- the signal's guards -------------------------------------------------


def test_a_stop_at_the_entry_is_refused():
    """Not dropped quietly. ExitRule would refuse it one layer later, where
    the error names the backtest instead of the rule that produced it."""
    with pytest.raises(ValueError, match="not below entry"):
        _signal(stop_price=10.0)


def test_a_stop_above_the_entry_is_refused():
    with pytest.raises(ValueError, match="not below entry"):
        _signal(stop_price=10.5)


def test_a_target_at_or_below_the_entry_is_refused():
    with pytest.raises(ValueError, match="not above entry"):
        _signal(target_price=10.0)


def test_the_error_names_the_strategy():
    """With several rules registered, "stop is not below entry" alone does
    not say which one to go and fix."""
    with pytest.raises(ValueError, match="orb"):
        _signal(strategy="orb", stop_price=11.0)


# --- the context ---------------------------------------------------------


def test_no_context_outside_the_regular_session():
    """Premarket may be a reference price but never a trigger -- see
    momentum.is_regular_session_bar for the after-hours artifact this
    prevents."""
    bars = [_at(8, 0), _at(9, 0)]

    assert _ctx(bars, 0) is None
    assert _ctx(bars, 1) is None


def test_a_regular_session_bar_builds_a_context():
    bars = [_at(10, 0)]

    ctx = _ctx(bars, 0, vwaps=[9.9])

    assert ctx is not None
    assert ctx.symbol == "AAA"
    assert ctx.vwap == 9.9


def test_the_session_slice_ends_at_the_trigger_bar():
    """A strategy must never see the future. The slice is inclusive of its
    own bar and stops there, whatever else the series holds."""
    bars = [_at(10, 0), _at(10, 5), _at(10, 10)]

    ctx = _ctx(bars, 1)

    assert ctx.session_bars == bars[:2]
    assert ctx.session_bars[-1] is bars[1]


def test_the_session_slice_keeps_same_day_premarket():
    """Deliberate, and the reason build_context filters by ET date rather
    than by session membership: excluding premarket blanks out the opening
    range, which is exactly what the first strategies need to see."""
    bars = [_at(8, 0), _at(9, 0), _at(10, 0)]

    ctx = _ctx(bars, 2)

    assert len(ctx.session_bars) == 3


def test_the_session_slice_stops_at_the_previous_day():
    """The boundary guard. A window reaching back across a market close
    compares today's price against yesterday's session."""
    bars = [_at(15, 0, day=18), _at(15, 55, day=18), _at(10, 0, day=19)]

    ctx = _ctx(bars, 2)

    assert ctx.session_bars == [bars[2]]


def test_both_vwap_anchors_are_carried():
    """On a gapper the two disagree about which side of the line price is
    on, so a strategy has to be able to say which one it means."""
    ctx = _ctx([_at(10, 0)], 0, vwaps=[10.2], premarket_vwap=9.8)

    assert (ctx.vwap, ctx.premarket_vwap) == (10.2, 9.8)


def test_an_index_off_the_end_is_none_rather_than_an_error():
    assert _ctx([_at(10, 0)], 5) is None
    assert _ctx([], 0) is None


def test_the_session_scan_does_not_walk_the_whole_history():
    """Guards the performance property the backwards walk exists for: over a
    multi-week series, filtering the full history-so-far at every bar is
    O(n^2). Ten sessions of bars, and the slice for a bar in the last one
    must hold only that session."""
    bars = [
        _at(hour, minute, day=day)
        for day in range(10, 20)
        for hour, minute in ((10, 0), (11, 0), (12, 0))
    ]

    ctx = _ctx(bars, len(bars) - 1)

    assert len(ctx.session_bars) == 3
