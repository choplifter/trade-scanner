"""The premarket range breakout.

Deliberately thin, because the rule is thin: it hands a different pair of
prices to strategies.breakout and changes nothing else, and the break test,
the stop, the target and the management are covered against the opening-range
rule already. What is tested here is the part that is genuinely its own --
which range it reads, and that it may fire from the opening bell rather than
having to wait for a window to close.

The last case is the one worth having. Both rules would look correct if this
one waited five minutes too; it would simply be a slightly worse version of
the other, silently.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pytest

from app.scanners.exit_rules import SIDE_LONG, SIDE_SHORT
from app.services.market_clock import trading_hours_for
from app.strategies import breakout
from app.strategies import premarket_range_breakout as rule
from app.strategies.context import StrategyContext

_DAY = date(2026, 8, 19)  # a Wednesday: a full session
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


def _premarket(high=10.5, low=10.0):
    """Two bars before the bell that set the overnight range."""
    return [
        _bar(-120, 10.2, high, low + 0.1, 10.3),
        _bar(-30, 10.3, high - 0.1, low, 10.25),
    ]


def _ctx(bars, levels=(7.0, 14.0)):
    return StrategyContext(
        symbol="AAA",
        bar=bars[-1],
        session_bars=bars,
        # None through the premarket, which is what SessionVwapState produces
        # before the session opens.
        session_vwaps=[None] * len(bars),
        premarket_vwap=None,
        levels=levels,
    )


def _break_long(minutes=0, close=11.0):
    return _bar(minutes, 10.4, close + 0.05, 10.35, close)


def _break_short(minutes=0, close=9.5):
    return _bar(minutes, 10.1, 10.15, close - 0.05, close)


# --- which range it reads -------------------------------------------------


def test_the_range_is_the_premarket_high_and_low():
    signal = rule.evaluate(_ctx([*_premarket(), _break_long()]))

    assert signal is not None
    assert signal.side == SIDE_LONG
    assert signal.stop_price == pytest.approx(10.0)


def test_it_breaks_short_too():
    signal = rule.evaluate(_ctx([*_premarket(), _break_short()]))

    assert signal.side == SIDE_SHORT
    assert signal.stop_price == pytest.approx(10.5)


def test_regular_session_bars_are_not_part_of_the_range():
    """Otherwise the box grows as the session runs and every break is a break
    of something that already moved."""
    bars = [*_premarket(), _bar(0, 10.4, 20.0, 1.0, 10.5), _break_long(minutes=10)]

    signal = rule.evaluate(_ctx(bars))

    assert signal.stop_price == pytest.approx(10.0)


def test_a_symbol_that_did_not_trade_premarket_produces_nothing():
    """No range, no rule. Common enough that it is most of the difference in
    sample size against the opening-range version."""
    assert rule.evaluate(_ctx([_break_long()])) is None


# --- when it may fire -----------------------------------------------------


def test_it_may_fire_on_the_very_first_bar_after_the_bell():
    """The property that separates it from the opening-range rule: the
    premarket is finished when the bell rings, so there is nothing to wait
    for. If this ever needed a wait, it would just be a worse copy of the
    other rule."""
    signal = rule.evaluate(_ctx([*_premarket(), _break_long(minutes=0)]))

    assert signal is not None
    assert rule.READY_AFTER_MINUTES == 0.0


def test_a_break_after_the_window_is_not_taken():
    late = _break_long(minutes=breakout.BREAKOUT_WINDOW_MINUTES + 10)

    assert rule.evaluate(_ctx([*_premarket(), late])) is None


def test_nothing_fires_before_the_bell():
    """A premarket bar cannot break the premarket range -- it is still
    setting it."""
    early = _bar(-10, 10.4, 12.0, 10.35, 11.5)

    assert rule.evaluate(_ctx([*_premarket(), early])) is None


# --- shared behaviour, spot-checked --------------------------------------


def test_the_management_is_the_shared_one():
    signal = rule.evaluate(_ctx([*_premarket(), _break_long()]))

    assert signal.stop_trigger == breakout.STOP_TRIGGER
    assert signal.scale_out == breakout.SCALE_OUT


def test_a_level_too_close_is_still_refused():
    assert rule.evaluate(_ctx([*_premarket(), _break_long()], levels=(11.5,))) is None


def test_the_file_satisfies_the_loader_contract():
    from app.strategies.loader import load_strategies

    strategies, errors = load_strategies(only="premarket_range_breakout")

    assert errors == []
    assert [s.name for s in strategies] == [rule.NAME]
