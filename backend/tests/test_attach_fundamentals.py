"""Copying cached fundamentals onto the ranked rows.

Three lines of assignment that nothing covered until now, and the whole
sector-attribution page hangs off one of them. The failure mode is quiet: the
field simply stays None, the page shows every row as Unknown, and nothing
raises anywhere.
"""

import asyncio
from types import SimpleNamespace

from app.core.config import Settings
from app.scanners.engine import ScannerEngine
from app.scanners.schemas import ScannerRow


class _Fundamentals:
    """Stands in for FundamentalsCache with exactly the surface
    _attach_fundamentals touches."""

    def __init__(self, data: dict):
        self._data = data
        self.ensured: set[str] = set()

    async def ensure_fresh(self, symbols):
        self.ensured |= set(symbols)

    def float_shares(self, symbol):
        entry = self._data.get(symbol)
        return entry.float_shares if entry else None

    def get(self, symbol):
        return self._data.get(symbol)


def _entry(sector="Healthcare", country="US", name="A Corp", **overrides):
    data = {
        "float_shares": 1_000_000.0,
        "market_cap": 5_000_000.0,
        "short_interest_pct": 3.5,
        "profile": SimpleNamespace(sector=sector, country=country, name=name),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _engine(fundamentals):
    return ScannerEngine(
        clients=None,  # type: ignore[arg-type]
        settings=Settings(alpaca_api_key_id="k", alpaca_api_secret_key="s"),
        universe={},
        manager=None,  # type: ignore[arg-type]
        fundamentals=fundamentals,  # type: ignore[arg-type]
        benchmark_tracker=None,  # type: ignore[arg-type]
        history_store=None,  # type: ignore[arg-type]
        news_cache=None,  # type: ignore[arg-type]
        momentum_cache=None,  # type: ignore[arg-type]
        http_client=None,  # type: ignore[arg-type]
    )


def _row(symbol="AAA"):
    return ScannerRow(
        symbol=symbol,
        exchange="NASDAQ",
        last_price=10.0,
        prev_close=9.0,
        pct_change=11.1,
        volume_today=1_000_000.0,
        avg_vol_20d=500_000.0,
        rvol=2.0,
        dollar_volume_today=10_000_000.0,
        updated_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )


def _attach(rows, data):
    fundamentals = _Fundamentals(data)
    engine = _engine(fundamentals)
    asyncio.run(engine._attach_fundamentals({"gainers": rows}))
    return fundamentals


def test_the_profile_fields_land_on_the_row():
    row = _row()

    _attach([row], {"AAA": _entry()})

    assert row.sector == "Healthcare"
    assert row.country == "US"
    assert row.company_name == "A Corp"
    assert row.market_cap == 5_000_000.0
    assert row.short_interest_pct == 3.5


def test_a_symbol_with_no_cached_fundamentals_is_left_alone():
    """Not an error -- the fetch may simply not have landed yet."""
    row = _row()

    _attach([row], {})

    assert row.sector is None
    assert row.company_name is None


def test_a_cache_entry_without_a_profile_leaves_the_profile_fields_none():
    """The 429 case: float and short interest come from bulk files and can be
    present while the per-symbol profile call is still backed off."""
    row = _row()

    _attach([row], {"AAA": _entry(profile=None)})

    assert row.sector is None
    assert row.country is None
    assert row.float_shares == 1_000_000.0


def test_a_profile_with_no_sector_leaves_it_none_rather_than_blank():
    """Observed live: some symbols come back from FMP with sector null. It
    must stay None so the attribution reports Unknown instead of attributing
    the move to a sector that was never identified."""
    row = _row()

    _attach([row], {"AAA": _entry(sector=None)})

    assert row.sector is None


def test_every_ranked_symbol_is_refreshed_once():
    rows = [_row("AAA"), _row("BBB")]

    fundamentals = _attach(rows, {"AAA": _entry(), "BBB": _entry(sector="Technology")})

    assert fundamentals.ensured == {"AAA", "BBB"}
    assert [r.sector for r in rows] == ["Healthcare", "Technology"]


def test_no_rows_means_no_fetch():
    """Guards the early return -- ensure_fresh on an empty set would be a
    pointless round trip on every closed-market tick."""
    fundamentals = _attach([], {})

    assert fundamentals.ensured == set()
