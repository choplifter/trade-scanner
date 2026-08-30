"""Has fresh scanner_history.sqlite3 data held up against the baseline the
catalyst-boost/fade-risk ranking change (commit 1ce30a2, deployed
2026-08-12) is checked against? That baseline was recalibrated 2026-08-30
using post-SIP data (see history_store._DEPLOY_DATE and the comment above
it) -- the ranking change's own deploy date didn't move, only the
comparison data did, after the 2026-08-20 IEX->SIP feed cutover made the
original pre-SIP baseline non-comparable to fresh data.

Run from backend/ (after `pip install -e ".[dev]"`):
    python -m scripts.ranking_drift_report [--since YYYY-MM-DD]

See app.scanners.history_store.ScannerHistoryStore.compute_ranking_drift
for the underlying computation, and formulas.py's _CATALYST_BOOST /
_FADE_RISK_RVOL / _FADE_RISK_DISCOUNT for the ranking constants this
checks against. This only reports drift -- it doesn't change any ranking
behavior on its own.
"""

import argparse
import asyncio

from app.core.config import get_settings
from app.scanners.history_store import ScannerHistoryStore

# Must match history_store._DEPLOY_DATE.
_DEPLOY_DATE = "2026-08-20"


def _fmt_bucket(label: str, stats: dict) -> str:
    if stats["sample_size"] == 0:
        return f"  {label}: no picks"
    return (
        f"  {label}: n={stats['sample_size']}, "
        f"win_rate={stats['win_rate']}%, avg_return={stats['avg_return']}%"
    )


# "win = positive return" is the scanner's own thesis for gainers and
# most_active, but the opposite of it for losers -- so the losers view needs
# reading in reverse, and must never be averaged in with the others.
_WIN_MEANING = {
    "gainers": "win = price rose after being flagged (thesis confirmed)",
    "losers": "win = price ROSE after being flagged, i.e. the flagged drop REVERSED "
    "-- read this view inverted",
    "most_active": "win = price rose after being flagged",
    "moderate_movers": "win = price rose after being flagged",
}


def _print_view(view_name: str, block: dict) -> None:
    print(f"--- {view_name} (n={block['sample_size']}) ---")
    print(f"  {_WIN_MEANING[view_name]}")

    catalyst = block["catalyst"]
    print("  Catalyst boost (formulas._CATALYST_BOOST = 1.15x when a headline is cached):")
    print(f"  {_fmt_bucket('with headline', catalyst['with_headline'])}")
    print(f"  {_fmt_bucket('without headline', catalyst['without_headline'])}")
    if catalyst["win_rate_delta_pp"] is not None:
        print(
            f"    win-rate delta: {catalyst['win_rate_delta_pp']:+.1f}pp "
            f"(baseline: {catalyst['baseline_win_rate_delta_pp']:+.1f}pp)"
        )
    else:
        print("    win-rate delta: not enough data in one or both groups")
    if not catalyst["sufficient_sample"]:
        print("    ** sample size below 30 in at least one group -- treat as noisy **")

    fade_risk = block["fade_risk"]
    threshold = fade_risk["threshold"]
    print(f"  Fade risk (formulas._FADE_RISK_RVOL = {threshold}x, discount 0.7x above it):")
    print(f"  {_fmt_bucket(f'rvol > {threshold}x', fade_risk['rvol_above_threshold'])}")
    print(f"  {_fmt_bucket(f'rvol <= {threshold}x', fade_risk['rvol_at_or_below_threshold'])}")
    print(
        f"    baseline for rvol > {threshold}x: "
        f"win_rate={fade_risk['baseline_win_rate']}%, avg_return={fade_risk['baseline_avg_return']}%"
    )
    if not fade_risk["sufficient_sample"]:
        print("    ** sample size below 30 for the >threshold group -- treat as noisy **")
    print()


def _print_report(report: dict) -> None:
    print(f"Ranking drift report -- appearances since {report['since']}")
    print(f"Total picks with a follow-up snapshot: {report['sample_size']}")
    excluded = report["excluded_non_trading_day"]
    if excluded:
        print(
            f"Excluded {excluded} pick(s) recorded on non-trading days "
            f"(weekend/holiday fallback data -- frozen tape, so every one would "
            f"score as an automatic 0% loss)"
        )
    print()

    if report["sample_size"] == 0:
        print("No data yet -- nothing to compare.")
        return

    print(
        "Broken down per view, never pooled: the baselines below were themselves\n"
        "computed pooled across all three views in the original one-off analysis,\n"
        "so treat them as a rough reference rather than an exact like-for-like.\n"
    )
    for view_name, block in report["views"].items():
        _print_view(view_name, block)


async def _main(since: str) -> None:
    settings = get_settings()
    store = ScannerHistoryStore(settings.scanner_history_db_path)
    report = await store.compute_ranking_drift(since_date=since)
    _print_report(report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--since",
        default=_DEPLOY_DATE,
        help=f"Only include appearances on/after this trading date (default: deploy date, {_DEPLOY_DATE})",
    )
    args = parser.parse_args()
    asyncio.run(_main(args.since))
