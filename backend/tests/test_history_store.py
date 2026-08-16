import asyncio
from datetime import datetime, timezone

from app.scanners.history_store import NewAppearance, ScannerHistoryStore

# A real NYSE trading day (Wednesday) and a real non-trading day (the Saturday
# after it). Fixed rather than derived from "today" so these tests behave the
# same whichever day they run on -- _is_trading_day consults the actual NYSE
# calendar, so seeding on a weekend would otherwise drop every row.
TRADING_DAY = datetime(2026, 8, 12, 15, 0, tzinfo=timezone.utc)
NON_TRADING_DAY = datetime(2026, 8, 15, 15, 0, tzinfo=timezone.utc)


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
    view: str = "gainers",
    now: datetime = TRADING_DAY,
) -> None:
    asyncio.run(
        store.record_appearances(
            [
                NewAppearance(
                    symbol=symbol,
                    view=view,
                    entry_price=entry_price,
                    entry_pct_change=10.0,
                    entry_rvol=entry_rvol,
                    benchmark_entry_price=None,
                    entry_headline=headline,
                )
            ],
            now=now,
        )
    )
    # Wide lookback so the snapshot still attaches to a back-dated appearance.
    asyncio.run(store.write_snapshots({symbol: latest_price}, None, lookback_days=3650))


def test_compute_ranking_drift_no_data_since_cutoff(tmp_path):
    store = _store(tmp_path)
    report = asyncio.run(store.compute_ranking_drift(since_date="2099-01-01"))
    assert report["sample_size"] == 0
    assert report["views"] == {}
    assert report["excluded_non_trading_day"] == 0


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
    gainers = report["views"]["gainers"]
    assert gainers["sample_size"] == 5

    catalyst = gainers["catalyst"]
    assert catalyst["with_headline"]["sample_size"] == 2
    assert catalyst["with_headline"]["win_rate"] == 100.0
    assert catalyst["without_headline"]["sample_size"] == 3
    assert catalyst["win_rate_delta_pp"] == 100.0 - catalyst["without_headline"]["win_rate"]
    assert catalyst["sufficient_sample"] is False  # well under the n=30 floor

    fade_risk = gainers["fade_risk"]
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

    catalyst = report["views"]["gainers"]["catalyst"]
    # BBB's roundup mention doesn't count as a catalyst -- only AAA does.
    assert catalyst["with_headline"]["sample_size"] == 1
    assert catalyst["without_headline"]["sample_size"] == 1


def test_compute_ranking_drift_splits_views_instead_of_pooling(tmp_path):
    store = _store(tmp_path)
    # Same price move in both views. For the gainer that's a win; for the loser
    # the same rise means the flagged drop reversed. Pooling would average the
    # two into a meaningless 50%.
    _seed(store, "UP1", entry_rvol=3.0, headline=None, entry_price=10.0, latest_price=11.0)
    _seed(
        store, "DOWN1", entry_rvol=3.0, headline=None, entry_price=10.0, latest_price=11.0, view="losers"
    )

    report = asyncio.run(store.compute_ranking_drift(since_date="2000-01-01"))

    assert set(report["views"]) == {"gainers", "losers"}
    assert report["views"]["gainers"]["sample_size"] == 1
    assert report["views"]["losers"]["sample_size"] == 1
    # No pooled aggregate is emitted at all -- that was the bug.
    assert "catalyst" not in report
    assert "fade_risk" not in report


def test_compute_ranking_drift_drops_non_trading_day_appearances(tmp_path):
    store = _store(tmp_path)
    _seed(store, "AAA", entry_rvol=3.0, headline=None, entry_price=10.0, latest_price=11.0)
    # Recorded on a Saturday from closed-market fallback data. Its snapshot is
    # taken against a frozen tape, so it would score as an automatic 0% loss.
    _seed(
        store,
        "SATURDAY",
        entry_rvol=3.0,
        headline=None,
        entry_price=10.0,
        latest_price=10.0,
        now=NON_TRADING_DAY,
    )

    report = asyncio.run(store.compute_ranking_drift(since_date="2000-01-01"))

    assert report["sample_size"] == 1, "only the real trading day's pick should count"
    assert report["excluded_non_trading_day"] == 1
    assert report["views"]["gainers"]["catalyst"]["without_headline"]["win_rate"] == 100.0


def test_compute_performance_drops_non_trading_day_appearances(tmp_path):
    store = _store(tmp_path)
    _seed(store, "AAA", entry_rvol=3.0, headline=None, entry_price=10.0, latest_price=11.0)
    # Saturday fallback row: flat price, so it would score as a 0% loss and
    # halve the reported win rate.
    _seed(
        store,
        "SATURDAY",
        entry_rvol=3.0,
        headline=None,
        entry_price=10.0,
        latest_price=10.0,
        now=NON_TRADING_DAY,
    )

    # Wide enough window to reach both seeded dates.
    report = asyncio.run(store.compute_performance(days=3650, view="gainers"))

    latest = [row for row in report["summary"] if row["horizon"] == "latest"]
    assert len(latest) == 1
    assert latest[0]["sample_size"] == 1, "the Saturday row should not be counted"
    assert latest[0]["win_rate"] == 100.0
    # The RVOL/gap buckets draw from the same filtered picks. (Leaderboards
    # aren't checked here -- they require alpha_vs_benchmark, and these rows are
    # seeded without a benchmark price, so they'd be empty either way.)
    assert sum(b["sample_size"] for b in report["rvol_buckets"]) == 1
    assert sum(b["sample_size"] for b in report["gap_buckets"]) == 1


def test_compute_ranking_drift_counts_a_holiday_as_a_non_trading_day(tmp_path):
    store = _store(tmp_path)
    # 2026-01-01 is a New Year's Day holiday -- a weekday the NYSE is shut, so a
    # plain weekday check would wrongly keep it.
    _seed(
        store,
        "HOLIDAY",
        entry_rvol=3.0,
        headline=None,
        entry_price=10.0,
        latest_price=10.0,
        now=datetime(2026, 1, 1, 15, 0, tzinfo=timezone.utc),
    )

    report = asyncio.run(store.compute_ranking_drift(since_date="2000-01-01"))

    assert report["sample_size"] == 0
    assert report["excluded_non_trading_day"] == 1
