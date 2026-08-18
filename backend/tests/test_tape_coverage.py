"""How much of the real tape our feed saw.

The Alpaca IEX feed reports only trades routed through IEX, and that share
swings by orders of magnitude between symbols -- 3.04% of AAPL's tape and
0.24% of IPST's on 2026-08-17. Every volume *level* the app computes is that
same fraction of reality.

Deliberately not used to invalidate VWAP, which is where this started: at
0.24% coverage our IPST VWAP was 7.8122 against 7.8149 from full-tape FMP
bars. A ratio of two quantities from the same sample tolerates a thin sample
much better than the quantities themselves.
"""

from datetime import datetime, timezone

from app.fundamentals.cache import FundamentalsCache
from app.fundamentals.schemas import FundamentalsData


def _cache(**volumes: float | None) -> FundamentalsCache:
    cache = FundamentalsCache.__new__(FundamentalsCache)
    cache._data = {
        symbol: FundamentalsData(
            symbol=symbol, full_tape_volume=volume, updated_at=datetime.now(timezone.utc)
        )
        for symbol, volume in volumes.items()
    }
    return cache


def test_coverage_matches_the_measured_session():
    cache = _cache(IPST=100_157_072, AAPL=32_743_947)
    assert round(cache.tape_coverage_pct("IPST", 274_452), 2) == 0.27
    assert round(cache.tape_coverage_pct("AAPL", 1_160_447), 2) == 3.54


def test_coverage_spans_orders_of_magnitude_between_symbols():
    """The point of the metric: the same feed is a reasonable sample of one
    symbol and a rounding error of another, so a single mental model of "how
    much volume do we see" is wrong."""
    cache = _cache(IPST=100_157_072, AAPL=32_743_947)
    assert cache.tape_coverage_pct("AAPL", 1_160_447) > 10 * cache.tape_coverage_pct("IPST", 274_452)


def test_unknown_coverage_is_none_never_reliable():
    """Missing data must not read as good coverage. Tri-state on purpose:
    "we checked and it is thin" and "we have no idea" are different things to
    put in front of someone about to trade."""
    cache = _cache(KNOWN=1_000_000, NOVOLUME=None)
    assert cache.tape_coverage_pct("NOVOLUME", 5_000) is None
    assert cache.tape_coverage_pct("ABSENT", 5_000) is None
    assert cache.tape_coverage_pct("KNOWN", None) is None


def test_coverage_cannot_exceed_one_hundred_percent():
    """Our own volume and FMP's refresh on different cadences, so the ratio
    can briefly overshoot. Clamped rather than reported as 140% coverage."""
    cache = _cache(THIN=1_000)
    assert cache.tape_coverage_pct("THIN", 1_400) == 100.0


def test_no_observed_volume_is_zero_coverage_not_unknown():
    cache = _cache(SYM=1_000_000)
    assert cache.tape_coverage_pct("SYM", 0) == 0.0
