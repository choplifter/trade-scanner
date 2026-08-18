"""Both VWAP anchors, tracked off one state object.

The 09:30 anchor is the day-trading convention and stays the default. The
premarket anchor is what TradingView draws, and on a gapper the two differ
enough to put price on opposite sides -- IPST 2026-08-17 closed at 7.39
against 7.81 session-anchored and ~7.18 premarket-anchored.
"""

from datetime import datetime, timedelta

from app.market_data.vwap import SessionVwapState
from app.services.market_clock import ET


def _feed(state: SessionVwapState, day: str, hour: int, minute: int, price: float, volume: float):
    ts = datetime.fromisoformat(day).replace(hour=hour, minute=minute, tzinfo=ET)
    return state.update(
        timestamp=ts, high=price, low=price, close=price, volume=volume, bar_vwap=price
    )


def test_regular_session_anchor_ignores_premarket():
    state = SessionVwapState(symbol="TEST")
    assert _feed(state, "2026-08-17", 7, 0, 5.0, 1_000) is None
    assert _feed(state, "2026-08-17", 9, 0, 6.0, 1_000) is None
    assert _feed(state, "2026-08-17", 10, 0, 10.0, 1_000) == 10.0


def test_premarket_anchor_counts_every_print():
    """Same bars, other anchor: the cheap premarket volume is included, so
    the line sits far below the session-anchored one."""
    state = SessionVwapState(symbol="TEST")
    _feed(state, "2026-08-17", 7, 0, 5.0, 1_000)
    _feed(state, "2026-08-17", 9, 0, 6.0, 1_000)
    assert state.premarket_anchored_vwap == 5.5

    _feed(state, "2026-08-17", 10, 0, 10.0, 2_000)
    # (5 + 6 + 10 + 10) / 4 across 4k shares
    assert state.premarket_anchored_vwap == 7.75
    assert state.cum_pv / state.cum_vol == 10.0


def test_the_two_anchors_can_put_price_on_opposite_sides():
    """The reason this exists at all, in miniature: heavy cheap premarket
    then a higher session. Price closes above one line and below the other,
    and both are correct."""
    state = SessionVwapState(symbol="TEST")
    _feed(state, "2026-08-17", 8, 0, 6.0, 50_000)
    session_vwap = _feed(state, "2026-08-17", 10, 0, 8.0, 10_000)
    close = 7.4
    assert close < session_vwap
    assert close > state.premarket_anchored_vwap


def test_both_anchors_reset_together_on_a_new_day():
    state = SessionVwapState(symbol="TEST")
    _feed(state, "2026-08-17", 8, 0, 6.0, 50_000)
    _feed(state, "2026-08-17", 10, 0, 8.0, 10_000)
    assert _feed(state, "2026-08-18", 10, 0, 3.0, 1_000) == 3.0
    assert state.premarket_anchored_vwap == 3.0


def test_premarket_anchor_is_none_before_anything_trades():
    assert SessionVwapState(symbol="TEST").premarket_anchored_vwap is None
