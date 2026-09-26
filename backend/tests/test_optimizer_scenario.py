"""The scenario grid on an optimizer card (optimizer.scenario_matrix):
price against implied volatility at the horizon."""

from datetime import datetime, timezone

import pytest

from app.options.optimizer import SCENARIO_IV_SHIFTS, SCENARIO_MOVES, position_pnl, scenario_matrix
from app.options.payoff import PayoffLeg

SPOT = 773.30
EXPIRY = datetime(2026, 10, 23, 20, 0, tzinfo=timezone.utc)
HORIZON = datetime(2026, 10, 5, 20, 0, tzinfo=timezone.utc)
MOVE = 15.0  # one implied move to the horizon


def _bull_put():
    """Real SPY quotes from 21 Sep 2026: sell the 750 put, buy the 745."""
    legs = [
        PayoffLeg(kind="put", strike=750, side="sell", ratio=1, expiry=EXPIRY, iv=0.1463),
        PayoffLeg(kind="put", strike=745, side="buy", ratio=1, expiry=EXPIRY, iv=0.1525),
    ]
    return legs, -0.63  # credit received, signed like the ticket


def _long_put():
    return [PayoffLeg(kind="put", strike=750, side="buy", ratio=1, expiry=EXPIRY, iv=0.1463)], 4.05


def test_the_grid_spans_the_implied_move_around_the_spot():
    legs, net = _bull_put()
    grid = scenario_matrix(legs, net, SPOT, HORIZON, MOVE)
    assert grid["prices"] == [round(SPOT + m * MOVE, 2) for m in SCENARIO_MOVES]
    assert [row["iv_shift"] for row in grid["rows"]] == list(SCENARIO_IV_SHIFTS)
    assert grid["implied_move"] == MOVE


def test_the_middle_row_is_the_cards_own_number():
    """The card prices at IV unchanged, so the grid must agree with it
    exactly -- otherwise the reader is shown two different numbers for the
    same scenario."""
    legs, net = _bull_put()
    grid = scenario_matrix(legs, net, SPOT, HORIZON, MOVE, qty=2)
    middle = next(row for row in grid["rows"] if row["iv_shift"] == 0.0)
    for price, cell in zip(grid["prices"], middle["pnl"]):
        assert cell == pytest.approx(position_pnl(legs, net, price, HORIZON, 2), abs=0.01)


def test_a_position_expiring_at_the_horizon_reads_the_same_at_every_volatility():
    """Then the P/L is intrinsic, and the grid says so: every IV row equal,
    span zero -- the honest signal that the assumption does no work here."""
    legs, net = _bull_put()
    at_expiry = scenario_matrix(legs, net, SPOT, EXPIRY, MOVE)
    rows = [row["pnl"] for row in at_expiry["rows"]]
    assert rows[0] == rows[1] == rows[2]
    assert at_expiry["iv_span"] == 0.0


def test_a_single_long_leg_is_far_more_volatility_dependent_than_a_spread():
    """Measured on the same quotes: the spread's legs nearly cancel, the
    long put's do not. This is the difference the grid exists to show."""
    spread_grid = scenario_matrix(*_bull_put(), SPOT, HORIZON, MOVE)
    long_grid = scenario_matrix(*_long_put(), SPOT, HORIZON, MOVE)
    assert long_grid["iv_span"] > 5 * spread_grid["iv_span"]


def test_higher_volatility_helps_a_long_option_and_hurts_a_short_one():
    at_spot = SCENARIO_MOVES.index(0.0)
    long_rows = scenario_matrix(*_long_put(), SPOT, HORIZON, MOVE)["rows"]
    spread_rows = scenario_matrix(*_bull_put(), SPOT, HORIZON, MOVE)["rows"]
    assert long_rows[0]["pnl"][at_spot] < long_rows[1]["pnl"][at_spot] < long_rows[2]["pnl"][at_spot]
    # The credit spread is short volatility: the +25 % row is the worst.
    assert spread_rows[2]["pnl"][at_spot] < spread_rows[1]["pnl"][at_spot] < spread_rows[0]["pnl"][at_spot]


def test_no_grid_without_an_implied_move_or_without_an_iv():
    legs, net = _bull_put()
    assert scenario_matrix(legs, net, SPOT, HORIZON, 0.0) is None
    blind = [PayoffLeg(kind="put", strike=750, side="buy", ratio=1, expiry=EXPIRY, iv=None)]
    assert scenario_matrix(blind, 4.05, SPOT, HORIZON, MOVE) is None
