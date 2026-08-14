"""Has fresh scanner_history.sqlite3 data held up against the one-off
analysis the catalyst-boost/fade-risk ranking change (commit 1ce30a2,
deployed 2026-08-12) was based on?

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
_DEPLOY_DATE = "2026-08-12"


def _fmt_bucket(label: str, stats: dict) -> str:
    if stats["sample_size"] == 0:
        return f"  {label}: no picks"
    return (
        f"  {label}: n={stats['sample_size']}, "
        f"win_rate={stats['win_rate']}%, avg_return={stats['avg_return']}%"
    )


def _print_report(report: dict) -> None:
    print(f"Ranking drift report -- appearances since {report['since']}")
    print(f"Total picks with a follow-up snapshot: {report['sample_size']}\n")

    if report["sample_size"] == 0:
        print("No data yet -- nothing to compare.")
        return

    catalyst = report["catalyst"]
    print("Catalyst boost (formulas._CATALYST_BOOST = 1.15x when a headline is cached):")
    print(_fmt_bucket("with headline", catalyst["with_headline"]))
    print(_fmt_bucket("without headline", catalyst["without_headline"]))
    if catalyst["win_rate_delta_pp"] is not None:
        print(
            f"  win-rate delta: {catalyst['win_rate_delta_pp']:+.1f}pp "
            f"(baseline: {catalyst['baseline_win_rate_delta_pp']:+.1f}pp)"
        )
    else:
        print("  win-rate delta: not enough data in one or both groups")
    if not catalyst["sufficient_sample"]:
        print("  ** sample size below 30 in at least one group -- treat as noisy **")
    print()

    fade_risk = report["fade_risk"]
    threshold = fade_risk["threshold"]
    print(f"Fade risk (formulas._FADE_RISK_RVOL = {threshold}x, discount 0.7x above it):")
    print(_fmt_bucket(f"rvol > {threshold}x", fade_risk["rvol_above_threshold"]))
    print(_fmt_bucket(f"rvol <= {threshold}x", fade_risk["rvol_at_or_below_threshold"]))
    print(
        f"  baseline for rvol > {threshold}x: "
        f"win_rate={fade_risk['baseline_win_rate']}%, avg_return={fade_risk['baseline_avg_return']}%"
    )
    if not fade_risk["sufficient_sample"]:
        print("  ** sample size below 30 for the >threshold group -- treat as noisy **")


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
