"""The risk chart's arithmetic: Black-Scholes against known values, the
at-expiry curve's kinks and breakevens, the today curve, stock legs."""

from datetime import date, datetime, timezone

import pytest

from app.options.payoff import PayoffLeg, bs_price, payoff_curve, years_between

NOW = datetime(2026, 9, 3, 14, 30, tzinfo=timezone.utc)
EXPIRY = date(2026, 9, 18)
LATER = date(2026, 10, 16)


def _curve_at(curve: dict, price: float) -> float:
    i = min(range(len(curve["prices"])), key=lambda k: abs(curve["prices"][k] - price))
    return curve["at_expiry"][i]


def test_black_scholes_matches_a_textbook_value():
    # S=100, K=100, T=1y, sigma=20%, r=0: call = put = 7.9656
    assert bs_price("call", 100, 100, 1.0, 0.20) == pytest.approx(7.9656, abs=1e-3)
    assert bs_price("put", 100, 100, 1.0, 0.20) == pytest.approx(7.9656, abs=1e-3)
    # No time left: intrinsic.
    assert bs_price("call", 110, 100, 0.0, 0.20) == 10.0
    assert bs_price("put", 90, 100, 0.0, 0.20) == 10.0


def test_years_between_counts_to_the_close():
    years = years_between(NOW, EXPIRY)
    assert 0.03 < years < 0.05  # ~15 days


def test_bull_put_at_expiry_has_the_defined_risk_shape():
    legs = [
        PayoffLeg("put", 740, "buy", expiry=EXPIRY, iv=0.2),
        PayoffLeg("put", 745, "sell", expiry=EXPIRY, iv=0.2),
    ]
    curve = payoff_curve(legs, qty=2, net_price=-1.0, spot=748.0, now=NOW)
    # Credit 1.00 x 100 x 2 above the short strike, -(5 - 1) x 200 below the long.
    assert _curve_at(curve, 760) == pytest.approx(200.0)
    assert _curve_at(curve, 700) == pytest.approx(-800.0)
    assert curve["breakevens"] == [744.0]
    assert curve["max_profit"] == 200.0 and curve["max_loss"] == -800.0
    assert curve["today"] is not None and len(curve["today"]) == len(curve["prices"])
    assert curve["multiplier"] == 200 and curve["expiry"] == EXPIRY


def test_long_call_profit_is_unbounded_and_today_needs_iv():
    legs = [PayoffLeg("call", 750, "buy", expiry=EXPIRY, iv=None)]
    curve = payoff_curve(legs, qty=1, net_price=3.0, spot=748.0, now=NOW)
    assert curve["max_profit"] is None
    assert curve["max_loss"] == -300.0
    assert curve["breakevens"] == [753.0]
    assert curve["today"] is None


def test_straddle_has_two_breakevens_and_butterfly_peaks_at_the_body():
    straddle = [
        PayoffLeg("put", 750, "buy", expiry=EXPIRY, iv=0.2),
        PayoffLeg("call", 750, "buy", expiry=EXPIRY, iv=0.2),
    ]
    curve = payoff_curve(straddle, qty=1, net_price=8.0, spot=750.0, now=NOW)
    assert curve["breakevens"] == [742.0, 758.0]
    assert curve["max_profit"] is None and curve["max_loss"] == -800.0

    fly = [
        PayoffLeg("call", 745, "buy", expiry=EXPIRY, iv=0.2),
        PayoffLeg("call", 750, "sell", ratio=2, expiry=EXPIRY, iv=0.2),
        PayoffLeg("call", 755, "buy", expiry=EXPIRY, iv=0.2),
    ]
    curve = payoff_curve(fly, qty=1, net_price=1.0, spot=750.0, now=NOW, points=201)
    assert curve["max_profit"] == pytest.approx(400.0, abs=5)
    assert curve["max_loss"] == -100.0
    assert curve["breakevens"] == [746.0, 754.0]


def test_calendar_is_drawn_at_the_short_expiry_with_the_long_leg_still_alive():
    legs = [
        PayoffLeg("call", 750, "sell", expiry=EXPIRY, iv=0.2),
        PayoffLeg("call", 750, "buy", expiry=LATER, iv=0.2),
    ]
    curve = payoff_curve(legs, qty=1, net_price=2.0, spot=750.0, now=NOW)
    assert curve["expiry"] == EXPIRY
    # At the body the short leg is worthless and the long leg keeps time
    # value: the best outcome. Far away both legs are near-worthless or
    # both intrinsic: the debit is (nearly) the loss.
    assert _curve_at(curve, 750) > 0
    assert _curve_at(curve, curve["prices"][0]) == pytest.approx(-200.0, abs=40)
    assert curve["max_loss"] is not None and curve["max_loss"] <= 0


def test_covered_call_uses_a_stock_leg():
    legs = [
        PayoffLeg("stock", 100.0, "buy"),
        PayoffLeg("call", 105, "sell", expiry=EXPIRY, iv=0.3),
    ]
    curve = payoff_curve(legs, qty=1, net_price=-2.0, spot=100.0, now=NOW)
    # Above the strike the shares are called away: (105 - 100 + 2) x 100.
    assert _curve_at(curve, 110) == pytest.approx(700.0)
    assert curve["max_profit"] == pytest.approx(700.0)
    assert curve["breakevens"] == [98.0]
    assert curve["max_loss"] is not None  # bounded by the shares going to zero, on this grid


def test_payoff_needs_an_option_leg():
    with pytest.raises(ValueError):
        payoff_curve([PayoffLeg("stock", 100.0, "buy")], qty=1, net_price=0.0, spot=100.0, now=NOW)


def test_the_curve_carries_the_legs_and_moment_for_client_side_repricing():
    now = datetime(2026, 9, 4, 14, 0, tzinfo=timezone.utc)
    legs = [
        PayoffLeg(kind="put", strike=95.0, side="buy", expiry=date(2026, 9, 18), iv=0.25),
        PayoffLeg(kind="put", strike=100.0, side="sell", expiry=date(2026, 9, 18), iv=0.22),
    ]
    curve = payoff_curve(legs, 2, -1.10, 100.0, now)

    assert curve["as_of"] == now
    assert curve["net_price"] == -1.10
    assert [(l["side"], l["kind"], l["strike"], l["iv"]) for l in curve["legs"]] == [
        ("buy", "put", 95.0, 0.25),
        ("sell", "put", 100.0, 0.22),
    ]
    assert curve["multiplier"] == 200
