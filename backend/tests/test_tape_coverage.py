"""How much of the real tape our feed saw, and what that invalidates.

The Alpaca IEX feed reports only trades routed through IEX. That share varies
by orders of magnitude between symbols, and anything volume-weighted inherits
it -- VWAP most of all, since it *is* a volume weighting. Measured on
2026-08-17: AAPL 3.04% coverage gave a VWAP 0.10% off the true volume-weighted
price, while IPST at 0.24% was 5.83% off (7.81 against 7.38), which showed
price below VWAP for an afternoon it spent above.
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


def test_the_two_measured_symbols_land_on_opposite_sides():
    """The threshold exists to separate exactly this pair -- a VWAP that was
    0.10% off from one that was 5.83% off and on the wrong side."""
    cache = _cache(IPST=100_157_072, AAPL=32_743_947)
    assert cache.is_vwap_reliable("IPST", 274_452) is False
    assert cache.is_vwap_reliable("AAPL", 1_160_447) is True


def test_unknown_coverage_is_none_never_reliable():
    """Missing data must not read as good coverage. Tri-state on purpose:
    "we checked and it is thin" and "we have no idea" are different things to
    put in front of someone about to trade."""
    cache = _cache(KNOWN=1_000_000, NOVOLUME=None)
    assert cache.tape_coverage_pct("NOVOLUME", 5_000) is None
    assert cache.is_vwap_reliable("NOVOLUME", 5_000) is None
    assert cache.tape_coverage_pct("ABSENT", 5_000) is None
    assert cache.is_vwap_reliable("ABSENT", 5_000) is None
    assert cache.tape_coverage_pct("KNOWN", None) is None


def test_coverage_cannot_exceed_one_hundred_percent():
    """Our own volume and FMP's refresh on different cadences, so the ratio
    can briefly overshoot. Clamped rather than reported as 140% coverage."""
    cache = _cache(THIN=1_000)
    assert cache.tape_coverage_pct("THIN", 1_400) == 100.0
    assert cache.is_vwap_reliable("THIN", 1_400) is True


def test_no_observed_volume_is_zero_coverage_not_unknown():
    cache = _cache(SYM=1_000_000)
    assert cache.tape_coverage_pct("SYM", 0) == 0.0
    assert cache.is_vwap_reliable("SYM", 0) is False
