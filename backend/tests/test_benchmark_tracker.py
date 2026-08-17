from datetime import datetime, timezone

from app.scanners.benchmark_tracker import ScannerBenchmarkTracker, compute_performance


def test_record_if_new_ignores_a_repeat_in_the_same_view():
    """The case this dedup exists for: a stock sits in the top 50 for hours
    and gets re-offered on every poll tick. That is one signal, not hundreds,
    and the entry must keep its original price rather than drifting to the
    latest one."""
    tracker = ScannerBenchmarkTracker()
    tracker.record_if_new("AAPL", "gainers", 10.0, 5.0, 2.0, 400.0)
    tracker.record_if_new("AAPL", "gainers", 12.0, 20.0, 4.0, 410.0)

    entries = tracker.all()
    assert len(entries) == 1
    assert entries[0].entry_price == 10.0


def test_record_if_new_tracks_each_view_separately():
    """Keyed per (symbol, view), matching ScannerHistoryStore.

    Per-symbol keying made an overlapping view invisible here: moderate_movers
    is a subset of gainers and gainers is recorded first, so its symbols were
    always already present and it would have shown nothing at all -- while
    appearing normally in the persisted history, leaving the two disagreeing.
    """
    tracker = ScannerBenchmarkTracker()
    tracker.record_if_new("AAPL", "gainers", 10.0, 5.0, 2.0, 400.0)
    tracker.record_if_new("AAPL", "moderate_movers", 10.0, 5.0, 2.0, 400.0)

    assert {e.view for e in tracker.all()} == {"gainers", "moderate_movers"}


def test_compute_performance_pct_change_and_alpha():
    tracker = ScannerBenchmarkTracker()
    tracker.record_if_new(
        symbol="AAPL",
        view="gainers",
        entry_price=100.0,
        entry_pct_change=8.0,
        entry_rvol=3.0,
        benchmark_entry_price=400.0,
    )

    current_prices = {"AAPL": 110.0}
    picks = compute_performance(
        tracker.all(),
        lambda symbol: current_prices.get(symbol),
        current_benchmark_price=404.0,  # benchmark up 1% while AAPL up 10%
    )

    assert len(picks) == 1
    pick = picks[0]
    assert pick["symbol"] == "AAPL"
    assert pick["pct_change_since_entry"] == 10.0
    assert pick["benchmark_pct_change_since_entry"] == 1.0
    assert pick["alpha_vs_benchmark"] == 9.0


def test_compute_performance_handles_missing_prices_gracefully():
    tracker = ScannerBenchmarkTracker()
    tracker.record_if_new("AAPL", "gainers", 100.0, 8.0, 3.0, benchmark_entry_price=None)

    # No current price available (symbol dropped out of self.rows) and no
    # entry-time benchmark price either -- should degrade to None fields,
    # not raise (e.g. a ZeroDivisionError or TypeError from None arithmetic).
    picks = compute_performance(
        tracker.all(),
        lambda symbol: None,
        current_benchmark_price=404.0,
    )

    assert len(picks) == 1
    pick = picks[0]
    assert pick["current_price"] is None
    assert pick["pct_change_since_entry"] is None
    assert pick["benchmark_pct_change_since_entry"] is None
    assert pick["alpha_vs_benchmark"] is None


def test_compute_performance_sorts_most_recent_first():
    tracker = ScannerBenchmarkTracker()
    tracker.record_if_new("OLD", "gainers", 10.0, 5.0, 2.0, 400.0)
    tracker._entries[("OLD", "gainers")].first_seen_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    tracker.record_if_new("NEW", "gainers", 10.0, 5.0, 2.0, 400.0)
    tracker._entries[("NEW", "gainers")].first_seen_at = datetime(2026, 1, 2, tzinfo=timezone.utc)

    picks = compute_performance(tracker.all(), lambda symbol: None, None)
    assert [p["symbol"] for p in picks] == ["NEW", "OLD"]
