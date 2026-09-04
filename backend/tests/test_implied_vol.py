"""The implied-volatility solver and the analytic greeks that stand in for
Alpaca's snapshot greeks on a replayed chain (app.options.payoff)."""

import pytest

from app.options.payoff import MIN_YEARS, bs_greeks, bs_price, implied_vol, intrinsic


@pytest.mark.parametrize(
    "kind,spot,strike,years,sigma",
    [
        ("call", 100.0, 100.0, 30 / 365, 0.25),
        ("put", 100.0, 95.0, 5 / 365, 0.40),
        ("call", 640.0, 650.0, 1 / 365, 0.18),
        ("put", 50.0, 60.0, 60 / 365, 0.60),
        ("call", 640.0, 600.0, 10 / 365, 0.22),
    ],
)
def test_solver_round_trips_black_scholes(kind, spot, strike, years, sigma):
    price = bs_price(kind, spot, strike, years, sigma)
    solved = implied_vol(kind, price, spot, strike, years)
    assert solved is not None
    assert abs(solved - sigma) < 1e-4


def test_no_iv_at_or_below_intrinsic():
    assert implied_vol("call", intrinsic("call", 110.0, 100.0), 110.0, 100.0, 10 / 365) is None
    assert implied_vol("put", 0.5, 110.0, 100.0, 10 / 365) is not None
    assert implied_vol("call", 0.0, 100.0, 100.0, 10 / 365) is None
    assert implied_vol("call", None, 100.0, 100.0, 10 / 365) is None


def test_no_iv_without_time_left():
    assert implied_vol("call", 1.0, 100.0, 100.0, MIN_YEARS / 2) is None
    assert implied_vol("call", 1.0, 100.0, 100.0, 0.0) is None


def test_no_iv_when_no_volatility_reaches_the_price():
    # A call cannot be worth more than the spot: a stale print against a
    # moved underlying has no solution.
    assert implied_vol("call", 150.0, 100.0, 100.0, 10 / 365) is None


def test_greeks_have_the_expected_signs():
    delta, gamma, theta = bs_greeks("call", 100.0, 100.0, 30 / 365, 0.25)
    assert 0 < delta < 1
    assert gamma > 0
    assert theta < 0
    delta_p, gamma_p, theta_p = bs_greeks("put", 100.0, 100.0, 30 / 365, 0.25)
    assert -1 < delta_p < 0
    assert abs(delta - delta_p - 1.0) < 1e-6  # put-call parity of deltas at r = 0
    assert gamma_p == gamma
    assert theta_p < 0


def test_greeks_collapse_to_intrinsic_once_expired():
    assert bs_greeks("call", 110.0, 100.0, 0.0, 0.25) == (1.0, 0.0, 0.0)
    assert bs_greeks("call", 90.0, 100.0, 0.0, 0.25) == (0.0, 0.0, 0.0)
    assert bs_greeks("put", 90.0, 100.0, 0.0, 0.25) == (-1.0, 0.0, 0.0)
