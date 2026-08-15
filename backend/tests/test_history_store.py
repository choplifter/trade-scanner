import asyncio
from datetime import datetime, timezone

from app.scanners.history_store import NewAppearance, ScannerHistoryStore


def _store(tmp_path) -> ScannerHistoryStore:
    store = ScannerHistoryStore(str(tmp_path / "history.sqlite3"))
    asyncio.run(store.init_schema())
    return store


def _seed(
    store: ScannerHistoryStore,
    symbol: str,
    entry_rvol: float,
    headline: str | None,
    entry_price: float,
    latest_price: float,
) -> None:
    now = datetime.now(timezone.utc)
    asyncio.run(
        store.record_appearances(
            [
                NewAppearance(
                    symbol=symbol,
                    view="gainers",
                    entry_price=entry_price,
                    entry_pct_change=10.0,
                    entry_rvol=entry_rvol,
                    benchmark_entry_price=None,
                    entry_headline=headline,
                )
            ]
        )
    )
    asyncio.run(store.write_snapshots({symbol: latest_price}, None))


def test_compute_ranking_drift_no_data_since_cutoff(tmp_path):
    store = _store(tmp_path)
    report = asyncio.run(store.compute_ranking_drift(since_date="2099-01-01"))
    assert report["sample_size"] == 0
    assert report["catalyst"] is None
    assert report["fade_risk"] is None


def test_compute_ranking_drift_catalyst_and_fade_risk_breakdown(tmp_path):
    store = _store(tmp_path)
    # Two winners with a headline, one loser without one.
    _seed(store, "AAA", entry_rvol=3.0, headline="Beats earnings", entry_price=10.0, latest_price=11.0)
    _seed(store, "BBB", entry_rvol=3.0, headline="Guidance raise", entry_price=10.0, latest_price=12.0)
    _seed(store, "CCC", entry_rvol=3.0, headline=None, entry_price=10.0, latest_price=9.0)
    # One winner and one loser above the fade-risk RVOL threshold.
    _seed(store, "DDD", entry_rvol=20.0, headline=None, entry_price=10.0, latest_price=10.5)
    _seed(store, "EEE", entry_rvol=20.0, headline=None, entry_price=10.0, latest_price=8.0)

    report = asyncio.run(store.compute_ranking_drift(since_date="2000-01-01"))

    assert report["sample_size"] == 5

    catalyst = report["catalyst"]
    assert catalyst["with_headline"]["sample_size"] == 2
    assert catalyst["with_headline"]["win_rate"] == 100.0
    assert catalyst["without_headline"]["sample_size"] == 3
    assert catalyst["win_rate_delta_pp"] == 100.0 - catalyst["without_headline"]["win_rate"]
    assert catalyst["sufficient_sample"] is False  # well under the n=30 floor

    fade_risk = report["fade_risk"]
    assert fade_risk["threshold"] == 15.0
    assert fade_risk["rvol_above_threshold"]["sample_size"] == 2
    assert fade_risk["rvol_above_threshold"]["win_rate"] == 50.0
    assert fade_risk["rvol_at_or_below_threshold"]["sample_size"] == 3
    assert fade_risk["sufficient_sample"] is False


def test_compute_ranking_drift_excludes_roundup_mentions_from_catalyst(tmp_path):
    store = _store(tmp_path)
    _seed(store, "AAA", entry_rvol=3.0, headline="Beats earnings", entry_price=10.0, latest_price=11.0)
    _seed(
        store,
        "BBB",
        entry_rvol=3.0,
        headline="12 Health Care Stocks Moving In Wednesday's Intraday Session",
        entry_price=10.0,
        latest_price=12.0,
    )

    report = asyncio.run(store.compute_ranking_drift(since_date="2000-01-01"))

    catalyst = report["catalyst"]
    # BBB's roundup mention doesn't count as a catalyst -- only AAA does.
    assert catalyst["with_headline"]["sample_size"] == 1
    assert catalyst["without_headline"]["sample_size"] == 1
