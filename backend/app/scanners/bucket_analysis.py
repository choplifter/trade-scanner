"""Shared win-rate/avg-return bucketing -- the same methodology used to
validate the catalyst-boost/fade-risk ranking multipliers against live
data (see history_store.py's compute_ranking_drift) is reused as-is by
the historical backtest (see backtest.py) so both measure "does this
predict outcome" the identical way, just over different sample sources.
"""

# Fade-risk buckets: does a bigger entry gap / higher RVOL predict worse
# subsequent performance ("gap and crap") rather than better? Bounds are
# upper-exclusive except the last, open-ended bucket. Bucketed on |gap%|
# (direction-agnostic -- losers' gaps are negative) and on entry_rvol
# directly (already direction-agnostic).
GAP_BUCKETS = [("<15%", 0.0, 15.0), ("15-30%", 15.0, 30.0), ("30-60%", 30.0, 60.0), (">60%", 60.0, None)]
RVOL_BUCKETS = [("<2x", 0.0, 2.0), ("2-5x", 2.0, 5.0), ("5-15x", 5.0, 15.0), (">15x", 15.0, None)]

# Below this many picks in a bucket, win rate/avg return are too noisy to
# call a trend one way or the other.
MIN_SAMPLE_SIZE = 30


def bucket_stats(picks: list[dict]) -> dict:
    wins = sum(1 for p in picks if p["pct_change_since_entry"] > 0)
    return {
        "sample_size": len(picks),
        "win_rate": round(wins / len(picks) * 100, 1) if picks else None,
        "avg_return": (
            round(sum(p["pct_change_since_entry"] for p in picks) / len(picks), 2) if picks else None
        ),
    }


def bucket_breakdown(
    picks: list[dict],
    key_fn,
    buckets: list[tuple[str, float, float | None]],
    views: tuple[str, ...] = ("gainers", "losers", "most_active"),
) -> list[dict]:
    """Does this bucket (gap size or RVOL) predict worse pct_change_since_entry
    than a smaller one? See GAP_BUCKETS/RVOL_BUCKETS for the hypothesis this
    exists to keep checking as more data accumulates. Rows come out in
    (view, bucket) declaration order, not sorted -- the point is reading
    them left-to-right as a monotonic trend.
    """
    rows = []
    for view_name in views:
        view_picks = [p for p in picks if p["view"] == view_name]
        for label, low, high in buckets:
            bucket_picks = [p for p in view_picks if key_fn(p) >= low and (high is None or key_fn(p) < high)]
            if not bucket_picks:
                continue
            wins = sum(1 for p in bucket_picks if p["pct_change_since_entry"] > 0)
            rows.append(
                {
                    "view": view_name,
                    "bucket": label,
                    "sample_size": len(bucket_picks),
                    "win_rate": round(wins / len(bucket_picks) * 100, 1),
                    "avg_return": round(
                        sum(p["pct_change_since_entry"] for p in bucket_picks) / len(bucket_picks), 2
                    ),
                }
            )
    return rows
