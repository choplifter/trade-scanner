"""compute_gex_plan's regime classification, near_flip proximity, and
playbook content -- pure, built directly on GexReading/StrikeGex fixtures."""

from datetime import datetime, timezone

from app.market_data.gamma_exposure import GexReading, StrikeGex
from app.market_data.gex_plan import compute_gex_plan

_AS_OF = datetime(2026, 8, 31, 20, 0, tzinfo=timezone.utc)


def _reading(spot_price: float, by_strike: list[StrikeGex]) -> GexReading:
    net = sum(row.net_gex for row in by_strike)
    call = sum(row.call_gex for row in by_strike)
    put = sum(row.put_gex for row in by_strike)
    return GexReading(
        symbol="SPY",
        spot_price=spot_price,
        net_gex=net,
        call_gex=call,
        put_gex=put,
        contracts_used=len(by_strike),
        as_of=_AS_OF,
        by_strike=by_strike,
    )


def test_regime_positive_when_net_gex_nonnegative():
    reading = _reading(100.0, [StrikeGex(strike=100.0, net_gex=1000.0, call_gex=1000.0, put_gex=0.0)])
    plan = compute_gex_plan(reading)
    assert plan.regime == "positive"
    assert any("long gamma" in line for line in plan.playbook)


def test_regime_negative_when_net_gex_negative():
    reading = _reading(100.0, [StrikeGex(strike=100.0, net_gex=-1000.0, call_gex=0.0, put_gex=-1000.0)])
    plan = compute_gex_plan(reading)
    assert plan.regime == "negative"
    assert any("short gamma" in line for line in plan.playbook)


def test_near_flip_true_within_threshold():
    # Cumulative crosses zero between 90 (-1000) and 100 (-1000+3000=2000),
    # 1/3 of the way across -> flip at strike 93.33.
    rows = [
        StrikeGex(strike=90.0, net_gex=-1000.0, call_gex=0.0, put_gex=-1000.0),
        StrikeGex(strike=100.0, net_gex=3000.0, call_gex=3000.0, put_gex=0.0),
    ]
    flip = 90.0 + (1000.0 / 3000.0) * 10.0
    plan = compute_gex_plan(_reading(flip + 0.1, rows))  # within 0.5% of flip
    assert plan.gamma_flip_strike == flip
    assert plan.near_flip is True
    assert any("gamma-flip" in line for line in plan.playbook)


def test_near_flip_false_outside_threshold():
    rows = [
        StrikeGex(strike=90.0, net_gex=-1000.0, call_gex=0.0, put_gex=-1000.0),
        StrikeGex(strike=100.0, net_gex=3000.0, call_gex=3000.0, put_gex=0.0),
    ]
    plan = compute_gex_plan(_reading(150.0, rows))
    assert plan.near_flip is False


def test_playbook_includes_wall_lines_when_available():
    rows = [
        StrikeGex(strike=95.0, net_gex=-2000.0, call_gex=0.0, put_gex=-2000.0),
        StrikeGex(strike=105.0, net_gex=1500.0, call_gex=1500.0, put_gex=0.0),
    ]
    plan = compute_gex_plan(_reading(100.0, rows))
    assert plan.call_wall.strike == 105.0
    assert plan.put_wall.strike == 95.0
    assert any("Call wall" in line for line in plan.playbook)
    assert any("Put wall" in line for line in plan.playbook)
