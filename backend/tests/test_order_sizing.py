"""Position sizing from a stop.

The arithmetic is one division, and the tests are almost entirely about the
ways that division goes wrong. A stop a tenth of a cent from entry sizes
$200 of intended risk into 200,000 shares; nothing downstream would question
it, because the number is arithmetically correct.
"""

import pytest

from app.trading.errors import OrderRejected
from app.trading.sizing import (
    MIN_RISK_PER_SHARE,
    assert_within_limits,
    risk_amount_for,
    shares_for_risk,
)


def test_sizes_to_the_requested_risk():
    result = shares_for_risk(entry=10.0, stop=9.50, side="buy", risk_amount=200.0)
    assert result.qty == 400  # 200 / 0.50
    assert result.risk_per_share == pytest.approx(0.50)
    assert result.risk_amount == pytest.approx(200.0)
    assert result.notional == pytest.approx(4000.0)


def test_sizes_a_short_from_a_stop_above_entry():
    result = shares_for_risk(entry=10.0, stop=10.50, side="sell", risk_amount=200.0)
    assert result.qty == 400


def test_rounds_down_so_realised_risk_never_exceeds_the_request():
    """Rounding up would risk more than asked -- the one direction this must
    never fail in."""
    result = shares_for_risk(entry=10.0, stop=9.70, side="buy", risk_amount=100.0)
    assert result.qty == 333  # 100 / 0.30 = 333.33
    assert result.risk_amount <= 100.0


def test_a_stop_on_the_wrong_side_is_refused_not_flipped():
    """Silently flipping it would place a different trade than the one
    described -- for a buy it would mean the position is already past its
    exit."""
    with pytest.raises(OrderRejected) as exc:
        shares_for_risk(entry=10.0, stop=10.50, side="buy", risk_amount=200.0)
    assert exc.value.field == "stop_price"

    with pytest.raises(OrderRejected):
        shares_for_risk(entry=10.0, stop=9.50, side="sell", risk_amount=200.0)


def test_a_stop_at_the_entry_is_refused():
    """Zero distance is a division by zero wearing a disguise."""
    with pytest.raises(OrderRejected):
        shares_for_risk(entry=10.0, stop=10.0, side="buy", risk_amount=200.0)


def test_a_stop_closer_than_a_penny_is_refused():
    """The dangerous case. 0.001 away turns $200 of risk into 200,000 shares,
    and the arithmetic is perfectly happy about it."""
    with pytest.raises(OrderRejected) as exc:
        shares_for_risk(entry=10.0, stop=9.999, side="buy", risk_amount=200.0)
    assert exc.value.field == "stop_price"
    assert "widen" in exc.value.message.lower()

    # Just inside the boundary still sizes.
    ok = shares_for_risk(entry=10.0, stop=10.0 - MIN_RISK_PER_SHARE, side="buy", risk_amount=200.0)
    assert ok.qty == 20_000


def test_sub_one_share_is_refused_with_the_amount_needed():
    """Flooring to zero must not silently become a no-op order."""
    with pytest.raises(OrderRejected) as exc:
        shares_for_risk(entry=100.0, stop=90.0, side="buy", risk_amount=5.0)
    assert exc.value.field == "risk_amount"
    assert "10.00" in exc.value.message


@pytest.mark.parametrize("bad", [0.0, -1.0])
def test_non_positive_inputs_are_refused(bad):
    with pytest.raises(OrderRejected):
        shares_for_risk(entry=bad, stop=9.0, side="buy", risk_amount=100.0)
    with pytest.raises(OrderRejected):
        shares_for_risk(entry=10.0, stop=bad, side="buy", risk_amount=100.0)
    with pytest.raises(OrderRejected):
        shares_for_risk(entry=10.0, stop=9.0, side="buy", risk_amount=bad)


def test_risk_amount_from_percent_of_equity():
    assert risk_amount_for(100_000.0, 1.0) == pytest.approx(1_000.0)
    with pytest.raises(OrderRejected):
        risk_amount_for(0.0, 1.0)
    with pytest.raises(OrderRejected):
        risk_amount_for(100_000.0, 0.0)


def test_share_and_notional_ceilings_are_independent():
    """Two caps rather than one: a share cap catches an absurd quantity at a
    penny price, a notional cap catches a modest quantity at a high price.
    Neither subsumes the other."""
    with pytest.raises(OrderRejected, match="share ceiling"):
        assert_within_limits(
            qty=50_000, notional=500.0, buying_power=1e9, max_qty=10_000, max_notional=5_000.0
        )
    with pytest.raises(OrderRejected, match="order ceiling"):
        assert_within_limits(
            qty=10, notional=50_000.0, buying_power=1e9, max_qty=10_000, max_notional=5_000.0
        )


def test_insufficient_buying_power_is_refused_not_clamped():
    """A silently reduced size is not the trade whose risk was just
    calculated. The message names what is affordable so the ticket can show
    it."""
    with pytest.raises(OrderRejected) as exc:
        assert_within_limits(
            qty=1_000, notional=10_000.0, buying_power=4_000.0,
            max_qty=10_000, max_notional=50_000.0,
        )
    assert "400 shares affordable" in exc.value.message


def test_limits_pass_when_everything_is_within_range():
    assert_within_limits(
        qty=100, notional=1_000.0, buying_power=5_000.0,
        max_qty=10_000, max_notional=5_000.0,
    )
