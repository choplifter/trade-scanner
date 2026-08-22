"""The bar walk, tested against a stub strategy rather than a real one.

Everything asserted here is a property of the *walk*, not of any trading
rule: how often one event is allowed to count, where a session ends, and
which bars an exit may be scored against. Using a stub keeps it that way --
these tests stay valid when the real rules arrive, and they fail for the
reason they name rather than because a rule changed.

The four properties below are the ones momentum_backtest paid for the hard
way, and each one, if it broke, would produce a plausible-looking number
rather than an error.
"""

from dataclasses import dataclass
from datetime import datetime

import pytest

from app.scanners.exit_rules import EXIT_CLOSE, EXIT_STOP, EXIT_TARGET
from app.scanners.strategy_backtest import (
    _forward_session_bars,
    rule_for,
    simulate_strategy,
)
from app.services.market_clock import ET
from app.strategies.context import Signal


@dataclass
class _Bar:
    timestamp: datetime
    open: float = 10.0
    high: float = 10.2
    low: float = 9.8
    close: float = 10.0
    volume: float = 1000.0


def _at(hour, minute, day=19, **prices):
    return _Bar(timestamp=datetime(2026, 8, day, hour, minute, tzinfo=ET), **prices)


class _FiresOn:
    """Fires on whichever bars the test names, with a fixed 5%/2R shape."""

    name = "stub"

    def __init__(self, predicate):
        self._predicate = predicate

    def evaluate(self, ctx):
        if not self._predicate(ctx):
            return None
        entry = ctx.bar.close
        return Signal(
            strategy=self.name,
            entry_price=entry,
            stop_price=entry * 0.95,
            target_price=entry * 1.10,
            reason="stub",
        )


_ALWAYS = _FiresOn(lambda ctx: True)


# --- rising edge ---------------------------------------------------------


def test_a_run_of_qualifying_bars_is_one_pick():
    """The sample-size trap. A real move keeps a condition true for several
    consecutive bars; counting each one turns a single event into many
    highly correlated samples and inflates n without adding evidence."""
    bars = [_at(10, 0), _at(10, 5), _at(10, 10), _at(10, 15)]

    picks = simulate_strategy({"AAA": bars}, _ALWAYS)

    assert len(picks) == 1
    assert picks[0]["timestamp"] == bars[0].timestamp.isoformat()


def test_the_condition_going_away_and_returning_is_a_second_pick():
    fires = _FiresOn(lambda ctx: ctx.bar.timestamp.minute != 5)
    bars = [_at(10, 0), _at(10, 5), _at(10, 10), _at(10, 15)]

    picks = simulate_strategy({"AAA": bars}, fires)

    assert [p["timestamp"] for p in picks] == [
        bars[0].timestamp.isoformat(),
        bars[2].timestamp.isoformat(),
    ]


def test_each_symbol_keeps_its_own_streak():
    """Shared state across symbols would swallow the first pick of every
    symbol after the first."""
    bars = [_at(10, 0), _at(10, 5)]

    picks = simulate_strategy({"AAA": list(bars), "BBB": list(bars)}, _ALWAYS)

    assert {p["symbol"] for p in picks} == {"AAA", "BBB"}
    assert len(picks) == 2


# --- session boundaries --------------------------------------------------


def test_a_new_session_starts_a_fresh_edge():
    """Without the reset, a streak running into the close swallows the first
    qualifying bar of the next morning.

    The series deliberately holds nothing but regular-session bars: with an
    after-hours bar between the two days the streak would break anyway, and
    the test would pass without exercising the date check at all.
    """
    bars = [_at(10, 0, day=19), _at(10, 5, day=19), _at(10, 0, day=20), _at(10, 5, day=20)]

    picks = simulate_strategy({"AAA": bars}, _ALWAYS)

    assert [p["timestamp"] for p in picks] == [
        bars[0].timestamp.isoformat(),
        bars[2].timestamp.isoformat(),
    ]


def test_premarket_bars_never_trigger():
    bars = [_at(8, 0), _at(9, 0)]

    assert simulate_strategy({"AAA": bars}, _ALWAYS) == []


def test_an_entry_on_the_last_bar_of_the_session_is_not_a_trade():
    """There is nowhere to exit. simulate_exit would score it flat at its
    own entry, which adds a free zero to the sample."""
    picks = simulate_strategy({"AAA": [_at(15, 55)]}, _ALWAYS)

    assert picks == []


def test_an_exit_is_never_scored_against_the_next_day():
    """The artifact this prevents: a late entry resolved against tomorrow's
    open, which is a gap, not a trade the stop could have caught."""
    entry = _at(15, 50, close=10.0)
    next_day = _at(10, 0, day=20, open=20.0, high=20.0, low=20.0, close=20.0)
    bars = [entry, _at(15, 55, close=10.0), next_day]

    picks = simulate_strategy({"AAA": bars}, _FiresOn(lambda c: c.bar is entry))

    assert len(picks) == 1
    assert picks[0]["exit_reason"] == EXIT_CLOSE
    assert picks[0]["exit_price"] == pytest.approx(10.0)


def test_forward_bars_stop_at_the_session_end():
    bars = [_at(15, 50), _at(15, 55), _at(10, 0, day=20), _at(10, 5, day=20)]

    forward = _forward_session_bars(bars, 0, bars[0])

    assert forward == [bars[1]]


def test_forward_bars_skip_after_hours_prints():
    """Same day, outside the session -- a thin print is not a fill."""
    bars = [_at(15, 50), _at(17, 0)]

    assert _forward_session_bars(bars, 0, bars[0]) == []


# --- the exit is the signal's own ---------------------------------------


def test_the_rule_comes_off_the_signal_not_a_constant():
    signal = Signal(
        strategy="s", entry_price=10.0, stop_price=9.5, target_price=11.5, reason="r"
    )

    rule = rule_for(signal)

    assert rule.stop_pct == pytest.approx(0.05)
    assert rule.reward_ratio == pytest.approx(3.0)


def test_a_stop_is_scored_as_minus_one_r():
    entry = _at(10, 0, close=10.0)
    bars = [entry, _at(10, 5, open=10.0, high=10.0, low=9.0, close=9.2)]

    picks = simulate_strategy({"AAA": bars}, _FiresOn(lambda c: c.bar is entry))

    assert picks[0]["exit_reason"] == EXIT_STOP
    assert picks[0]["r_multiple"] == pytest.approx(-1.0)
    assert picks[0]["stop_price"] == pytest.approx(9.5)


def test_a_target_is_scored_at_the_signals_own_ratio():
    entry = _at(10, 0, close=10.0)
    bars = [entry, _at(10, 5, open=10.0, high=11.5, low=10.0, close=11.4)]

    picks = simulate_strategy({"AAA": bars}, _FiresOn(lambda c: c.bar is entry))

    assert picks[0]["exit_reason"] == EXIT_TARGET
    assert picks[0]["r_multiple"] == pytest.approx(2.0)


def test_an_ambiguous_exit_is_flagged_on_the_pick():
    """Carried out to the report rather than absorbed silently. A bar
    spanning both levels is scored a stop by assumption, and how often that
    happened is what says whether the result rests on the tape or on the
    assumption."""
    entry = _at(10, 0, close=10.0)
    bars = [entry, _at(10, 5, open=10.0, high=11.5, low=9.0, close=10.0)]

    picks = simulate_strategy({"AAA": bars}, _FiresOn(lambda c: c.bar is entry))

    assert picks[0]["exit_reason"] == EXIT_STOP
    assert picks[0]["ambiguous_exit"] is True


def test_an_unambiguous_exit_is_not_flagged():
    entry = _at(10, 0, close=10.0)
    bars = [entry, _at(10, 5, open=10.0, high=11.5, low=10.0, close=11.4)]

    picks = simulate_strategy({"AAA": bars}, _FiresOn(lambda c: c.bar is entry))

    assert picks[0]["ambiguous_exit"] is False


# --- the two outcome columns --------------------------------------------


def test_the_percentage_column_is_measured_cost_free():
    """Deliberate: pct_change_since_entry is the field bucket_analysis
    shares with every other backtest here, and those were all measured
    without costs -- so it stays comparable across them. R carries the cost.
    Reading the two as one trade priced twice is the misreading this test
    pins down."""
    entry = _at(10, 0, close=10.0)
    bars = [entry, _at(10, 5, open=10.0, high=11.5, low=10.0, close=11.4)]

    picks = simulate_strategy({"AAA": bars}, _FiresOn(lambda c: c.bar is entry), cost_bps=10)

    assert picks[0]["pct_change_since_entry"] == pytest.approx(10.0)
    # 10bps each way on a 5% stop is 0.2% of entry against 5% risk = 0.04R.
    assert picks[0]["r_multiple"] == pytest.approx(1.96)


def test_no_bars_is_no_picks_rather_than_an_error():
    assert simulate_strategy({}, _ALWAYS) == []
    assert simulate_strategy({"AAA": []}, _ALWAYS) == []
