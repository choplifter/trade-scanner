"""The synthetic chain and the backtest walk -- app.playbooks.synthetic_chain
and app.playbooks.backtest, pure over generated closes. The walk is checked
for the wheel's mechanics: a put is sold once the volatility window is full,
a forced slide assigns it, a call follows on the shares, a forced rally calls
them away, and the summary counts agree with the events."""

import math
from datetime import date, timedelta

import pytest

from app.playbooks import loader
from app.playbooks.backtest import MIN_SESSIONS, walk
from app.playbooks.synthetic_chain import build_chain, realized_vol, strike_step, synthetic_expiries
from app.trading.errors import OrderRejected

TODAY = date(2026, 3, 2)  # a Monday


def test_strike_steps_follow_the_price_level():
    assert strike_step(12.0) == 0.5 and strike_step(80.0) == 1.0 and strike_step(150.0) == 2.5
    assert strike_step(400.0) == 5.0 and strike_step(900.0) == 10.0


def test_synthetic_expiries_are_fridays_weekly_then_monthly():
    expiries = synthetic_expiries(TODAY)
    assert all(e.weekday() == 4 for e in expiries)
    assert expiries[0] == date(2026, 3, 6) and expiries[1] == date(2026, 3, 13)
    assert len([e for e in expiries if e <= TODAY + timedelta(weeks=8)]) >= 8
    # Third Fridays further out.
    assert date(2026, 6, 19) in expiries and date(2026, 7, 17) in expiries


def test_realized_vol_needs_a_full_window_and_is_annualised():
    flat = [100.0] * 30
    assert realized_vol(flat) == 0.0
    assert realized_vol([100.0] * 10) is None
    # Alternating ±1 % daily moves: about 1 % daily sigma, ~16 % annualised.
    closes = [100.0]
    for i in range(30):
        closes.append(closes[-1] * (1.01 if i % 2 == 0 else 1 / 1.01))
    assert realized_vol(closes) == pytest.approx(0.01 * math.sqrt(252), rel=0.1)


def test_the_synthetic_chain_prices_by_black_scholes_with_parity_and_deltas():
    expiry = TODAY + timedelta(days=30)
    chain = build_chain("XYZ", TODAY, 100.0, 0.30, expiry, spread_frac=0.02)
    strikes = [r.strike for r in chain.rows]
    step = strike_step(100.0)
    assert strikes[0] <= 80.0 and strikes[-1] >= 120.0 and all(round(b - a, 6) == step for a, b in zip(strikes, strikes[1:]))
    atm = next(r for r in chain.rows if r.strike == 100.0)
    # Put-call parity at zero rate: C - P = S - K = 0 at the money.
    assert atm.call.mid == pytest.approx(atm.put.mid, abs=0.02)
    assert atm.call.delta == pytest.approx(0.52, abs=0.05) and atm.put.delta == pytest.approx(-0.48, abs=0.05)
    assert atm.call.ask - atm.call.bid == pytest.approx(atm.call.mid * 0.02, abs=0.002)
    assert atm.call.symbol.startswith("XYZ") and atm.call.iv == 0.3


def _sessions(path: list[float], start: date = TODAY) -> list[tuple[date, float]]:
    out = []
    day = start
    for close in path:
        while day.weekday() >= 5:
            day += timedelta(days=1)
        out.append((day, close))
        day += timedelta(days=1)
    return out


def test_the_walk_sells_a_put_gets_assigned_sells_a_call_and_is_called_away():
    wheel = loader.get_playbook("wheel")
    params = wheel.resolve_params({"min_dte": 14, "max_dte": 45, "roll_at_dte": 0, "take_profit_pct": 100, "avoid_earnings": False})
    # Flat for the window, a slide of 20 % over the next month (assignment),
    # then a rally of 40 % over the following two months (called away).
    path = [100.0] * 25
    path += [100.0 * (1 - 0.20 * i / 22) for i in range(1, 23)]
    path += [80.0 * (1 + 0.40 * i / 44) for i in range(1, 45)]
    result = walk("XYZ", wheel, params, _sessions(path), iv_premium=1.2, starting_cash=50_000.0, spread_frac=0.02)

    kinds = [e["kind"] for e in result["events"]]
    assert kinds[0] == "sold_put"
    assert "assigned" in kinds and kinds.index("assigned") > kinds.index("sold_put")
    assert "sold_call" in kinds and kinds.index("sold_call") > kinds.index("assigned")
    assert "called_away" in kinds and kinds.index("called_away") > kinds.index("sold_call")
    s = result["summary"]
    assert s["assignments"] == kinds.count("assigned") and s["called_away"] == kinds.count("called_away")
    assert s["puts_sold"] == kinds.count("sold_put") and s["calls_sold"] == kinds.count("sold_call")
    assert result["synthetic"] is True and len(result["equity"]) == result["sessions"]
    assert 0 < s["days_in_shares_pct"] < 100
    # Premiums were collected and the equity curve is continuous (no day jumps by more than the shares' move).
    assert s["premiums"] > 0
    equities = [p["equity"] for p in result["equity"]]
    assert all(abs(b - a) < 50_000 * 0.15 for a, b in zip(equities, equities[1:]))


def test_the_walk_refuses_too_little_history():
    wheel = loader.get_playbook("wheel")
    with pytest.raises(OrderRejected):
        walk("XYZ", wheel, wheel.resolve_params({}), _sessions([100.0] * (MIN_SESSIONS - 1)), iv_premium=1.0, starting_cash=10_000.0, spread_frac=0.0)


def test_a_flat_stock_collects_premium_and_expires_worthless():
    wheel = loader.get_playbook("wheel")
    params = wheel.resolve_params({"min_dte": 14, "max_dte": 45, "roll_at_dte": 0, "take_profit_pct": 100, "avoid_earnings": False})
    # A gentle wobble keeps the volatility estimate above the floor.
    path = [100.0 + (0.4 if i % 2 else -0.4) for i in range(140)]
    result = walk("XYZ", wheel, params, _sessions(path), iv_premium=1.0, starting_cash=50_000.0, spread_frac=0.0)
    kinds = [e["kind"] for e in result["events"]]
    assert kinds.count("sold_put") >= 2 and "expired" in kinds and "assigned" not in kinds
    assert result["summary"]["final_equity"] > 50_000.0
    assert result["summary"]["shares_at_end"] == 0
