"""The distribution a chain's smile implies: that it reduces to the
lognormal on a flat smile, moves mass into the tail on a skewed one, agrees
with the call prices it is derived from, and stays a distribution on a
noisy chain."""

import math
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.options.distribution import Distribution, fit_smile, smile_points
from app.options.optimizer import chance_of_profit
from app.options.payoff import PayoffLeg, bs_price

ET = ZoneInfo("America/New_York")
SPOT = 100.0
ATM = 0.30
YEARS = 45 / 365
EXPIRY = date(2026, 11, 20)


def _rows(iv_of, strikes=tuple(70 + 2.5 * i for i in range(25)), *, call_shift=0.0):
    """Dict rows like the Optimizer's: both sides quoted and bid at every
    strike, IV from `iv_of(strike)`; `call_shift` adds a constant to every
    call IV, the way a feed solving against spot instead of the forward does."""
    return [
        {"strike": k, "call": {"iv": iv_of(k) + call_shift, "bid": 1.0}, "put": {"iv": iv_of(k), "bid": 1.0}}
        for k in strikes
    ]


def _lognormal_below(price: float, sigma: float = ATM) -> float:
    w = sigma * math.sqrt(YEARS)
    return 0.5 * (1 + math.erf((math.log(price / SPOT) + 0.5 * w * w) / w / math.sqrt(2)))


def _skew(k: float) -> float:
    # Put skew: +0.4 vol points per strike below the money, flatter above.
    m = math.log(k / SPOT)
    return ATM - 0.40 * m + 0.6 * m * m


def test_a_flat_smile_is_the_lognormal():
    dist = Distribution.from_chain(_rows(lambda k: ATM), SPOT, ATM, YEARS)
    assert dist.skewed
    for price in (80, 90, 95, 100, 105, 110, 120):
        assert dist.cdf(price) == pytest.approx(_lognormal_below(price), abs=2e-3)


def test_the_feeds_call_put_gap_is_taken_out_before_the_fit():
    """LQD on 2026-10-02: calls 7.9 %, puts 11.6 % at the same strike; the
    mids said 9.7 %. A constant gap is the feed's forward assumption, not
    a smile, and must not read as one."""
    rows = _rows(lambda k: 0.116, call_shift=-0.037)
    points = smile_points(rows, SPOT)
    assert all(iv == pytest.approx(0.0975, abs=1e-6) for _, iv in points)


def test_put_skew_moves_probability_into_the_downside_tail():
    dist = Distribution.from_chain(_rows(_skew), SPOT, ATM, YEARS)
    assert dist.skewed
    # More mass far below than the ATM lognormal gives ...
    assert dist.cdf(80) > _lognormal_below(80) + 0.005
    # ... and it is still a distribution: from 0 to 1, never decreasing.
    xs = [40 + i for i in range(121)]
    fs = [dist.cdf(x) for x in xs]
    assert fs == sorted(fs) and fs[0] < 1e-3 and fs[-1] > 1 - 1e-3


def test_the_distribution_is_the_one_the_call_prices_imply():
    """Breeden-Litzenberger, checked numerically: P(S_T > K) is minus the
    slope of the call price in the strike, each call priced at its own
    smile IV. Inside the fitted range the two agree."""
    dist = Distribution.from_chain(_rows(_skew), SPOT, ATM, YEARS)
    smile = dist.smile

    def call(k: float) -> float:
        return bs_price("call", SPOT, k, YEARS, smile.sigma(math.log(k / SPOT)))

    for k in (88.0, 94.0, 100.0, 106.0, 112.0):
        h = 0.01
        above = -(call(k + h) - call(k - h)) / (2 * h)
        assert 1 - dist.cdf(k) == pytest.approx(above, abs=2e-3)


def test_a_chain_too_thin_to_fit_has_no_smile():
    two = [{"strike": 95.0, "call": None, "put": {"iv": 0.32, "bid": 0.5}}, {"strike": 105.0, "call": {"iv": 0.28, "bid": 0.5}, "put": None}]
    assert not Distribution.from_chain(two, SPOT, ATM, YEARS).skewed
    one_side = _rows(_skew, strikes=[80.0, 85.0, 90.0, 95.0])
    assert fit_smile(smile_points(one_side, SPOT), SPOT, ATM, YEARS) is None


def test_unbid_quotes_do_not_shape_the_smile():
    rows = _rows(lambda k: ATM)
    rows[3]["put"] = {"iv": 2.5, "bid": 0.0}  # an IV solved from nothing
    assert all(iv < 1.0 for _, iv in smile_points(rows, SPOT))


def test_a_noisy_chain_still_gives_a_monotone_distribution():
    jitter = [0.0, 0.04, -0.03, 0.05, -0.04, 0.02, -0.05, 0.03] * 4
    rows = _rows(lambda k: _skew(k) + jitter[int((k - 70) / 2.5) % len(jitter)])
    dist = Distribution.from_chain(rows, SPOT, ATM, YEARS)
    fs = [dist.cdf(40 + i * 0.5) for i in range(241)]
    assert fs == sorted(fs) and all(0.0 <= f <= 1.0 for f in fs)


def test_put_skew_cuts_a_far_put_spreads_chance_and_lifts_a_near_ones():
    """What skew does to a put credit spread depends on where its breakeven
    sits. Put skew prices a crash as likelier than the ATM lognormal does
    -- and, to pay for it, a modest dip as less likely (on this smile
    P(S_T < 95) is 28.6 % against 33.2 %). So a spread whose breakeven is
    far out wins less often than the ATM figure said, and one close to the
    money more often. The crossover here is about one standard deviation
    down (88)."""
    horizon = datetime.combine(EXPIRY, time(16, 0), tzinfo=ET)
    dist = Distribution.from_chain(_rows(_skew), SPOT, ATM, YEARS)

    def chances(short: float, long: float, credit: float) -> tuple[float, float]:
        legs = [
            PayoffLeg(kind="put", strike=short, side="sell", expiry=EXPIRY, iv=ATM),
            PayoffLeg(kind="put", strike=long, side="buy", expiry=EXPIRY, iv=ATM),
        ]
        return (
            chance_of_profit(legs, -credit, horizon, SPOT, ATM, YEARS),
            chance_of_profit(legs, -credit, horizon, SPOT, ATM, YEARS, dist=dist),
        )

    flat, skewed = chances(80.0, 75.0, 0.40)  # breakeven 79.6, ~2 sd down
    assert skewed < flat - 0.01
    flat, skewed = chances(97.0, 92.0, 1.80)  # breakeven 95.2, near the money
    assert skewed > flat + 0.01
    # Without a fitted smile the chance is the lognormal's, unchanged.
    legs = [PayoffLeg(kind="put", strike=90.0, side="sell", expiry=EXPIRY, iv=ATM)]
    assert chance_of_profit(legs, -1.0, horizon, SPOT, ATM, YEARS, dist=Distribution(SPOT, ATM, YEARS)) == chance_of_profit(
        legs, -1.0, horizon, SPOT, ATM, YEARS
    )
