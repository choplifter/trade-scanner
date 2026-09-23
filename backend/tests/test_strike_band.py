"""The chain's strike window: a percentage of spot with a dollar floor, so
a cheap name comes back as more than the few strikes around the money.
Ford at ~12 used to fetch 10.80-13.20 -- five rows on $0.50 strikes."""

import pytest

from app.options.chain_fetch import (
    FAR_STRIKE_ABOVE,
    FAR_STRIKE_BELOW,
    STRIKE_MIN_RANGE,
    STRIKE_PCT_RANGE,
    strike_band,
)


def test_a_cheap_name_gets_the_dollar_floor():
    low, high = strike_band(12.0)
    assert (low, high) == (7.0, 17.0)


@pytest.mark.parametrize("spot", [60.0, 230.0, 768.0])
def test_above_the_floor_the_percentage_still_decides(spot):
    assert strike_band(spot) == (
        round(spot * (1 - STRIKE_PCT_RANGE), 2),
        round(spot * (1 + STRIKE_PCT_RANGE), 2),
    )


def test_fifty_dollars_is_where_the_two_rules_meet():
    # 10% of 50 is exactly the floor: neither rule widens the band.
    assert strike_band(50.0) == (45.0, 55.0) == (50.0 - STRIKE_MIN_RANGE, 50.0 + STRIKE_MIN_RANGE)


def test_the_low_end_stays_positive():
    # A band wider than the spot would ask Alpaca for strikes at or below 0.
    low, high = strike_band(3.0)
    assert low == 0.01 and high == 8.0


def test_the_far_strip_keeps_its_lopsided_band_and_gains_the_floor():
    # Deep ITM calls a year out: 35% below, 5% above -- and on a cheap name
    # both sides widen to the floor.
    assert strike_band(200.0, FAR_STRIKE_BELOW, FAR_STRIKE_ABOVE) == (130.0, 210.0)
    assert strike_band(12.0, FAR_STRIKE_BELOW, FAR_STRIKE_ABOVE) == (7.0, 17.0)
