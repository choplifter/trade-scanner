"""The VWAP-side gate every signal passes: long only above the line, short
only below.

The rule is the user's, stated once over all strategies rather than written
into each: gate_by_vwap_side runs inside LoadedStrategy.evaluate, the one
object the chart walk, the live scanner and the backtest all evaluate
through. The cases here pin the gate itself; the last one pins *where* it
runs, because a gate applied in some walks and not others would let the
chart show a signal the backtest never scored.
"""

import pathlib
import textwrap

from app.scanners.exit_rules import SIDE_LONG, SIDE_SHORT
from app.strategies import loader as loader_mod
from app.strategies.context import Signal, StrategyContext, gate_by_vwap_side
from app.strategies.loader import load_strategies


class _Bar:
    def __init__(self, close):
        self.close = close


def _ctx(close: float, vwap: float | None) -> StrategyContext:
    bar = _Bar(close)
    return StrategyContext(
        symbol="AAA",
        bar=bar,
        session_bars=[bar],
        session_vwaps=[vwap],
        premarket_vwap=None,
    )


def _signal(side: str, close: float) -> Signal:
    stop = close - 0.5 if side == SIDE_LONG else close + 0.5
    target = close + 1.0 if side == SIDE_LONG else close - 1.0
    return Signal(
        strategy="Test",
        entry_price=close,
        stop_price=stop,
        target_price=target,
        reason="test",
        side=side,
    )


def test_a_long_above_the_line_stands():
    signal = _signal(SIDE_LONG, close=10.5)

    assert gate_by_vwap_side(_ctx(10.5, vwap=10.0), signal) is signal


def test_a_long_below_the_line_is_gated():
    assert gate_by_vwap_side(_ctx(9.5, vwap=10.0), _signal(SIDE_LONG, close=9.5)) is None


def test_a_short_below_the_line_stands():
    signal = _signal(SIDE_SHORT, close=9.5)

    assert gate_by_vwap_side(_ctx(9.5, vwap=10.0), signal) is signal


def test_a_short_above_the_line_is_gated():
    assert gate_by_vwap_side(_ctx(10.5, vwap=10.0), _signal(SIDE_SHORT, close=10.5)) is None


def test_a_close_exactly_on_the_line_is_neither_side():
    ctx = _ctx(10.0, vwap=10.0)

    assert gate_by_vwap_side(ctx, _signal(SIDE_LONG, close=10.0)) is None
    assert gate_by_vwap_side(ctx, _signal(SIDE_SHORT, close=10.0)) is None


def test_no_signal_stays_no_signal():
    assert gate_by_vwap_side(_ctx(10.0, vwap=10.0), None) is None


def test_an_unknowable_line_lets_the_signal_stand():
    """The same asymmetry as the ATR veto: a gate that cannot be computed
    refusing everything would unlist every setup exactly where nobody can
    see why."""
    signal = _signal(SIDE_LONG, close=9.5)

    assert gate_by_vwap_side(_ctx(9.5, vwap=None), signal) is signal


# --- where it runs --------------------------------------------------------


def test_the_gate_runs_inside_every_loaded_strategy(tmp_path, monkeypatch):
    """The choke point, not the strategies: a rule that never heard of VWAP
    still cannot hand a wrong-side signal to any caller."""
    monkeypatch.setattr(loader_mod, "_DIR", tmp_path)
    pathlib.Path(tmp_path / "buyer.py").write_text(
        textwrap.dedent(
            '''
            NAME = "Always Long"

            from app.strategies.context import Signal

            def evaluate(ctx):
                return Signal(
                    strategy=NAME,
                    entry_price=ctx.bar.close,
                    stop_price=ctx.bar.close - 0.5,
                    target_price=ctx.bar.close + 1.0,
                    reason="test",
                )
            '''
        ),
        encoding="utf-8",
    )
    strategy = load_strategies()[0][0]

    assert strategy.evaluate(_ctx(9.5, vwap=10.0)) is None
    assert strategy.evaluate(_ctx(10.5, vwap=10.0)) is not None
