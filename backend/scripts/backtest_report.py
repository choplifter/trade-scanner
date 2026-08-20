"""Daily-bar historical backtest: replays past trading days through the
app's actual ranking functions (see app.scanners.backtest) to check the
gap%/RVOL fade-risk buckets against months of history instead of the
handful of days accumulated live in scanner_history.sqlite3.

Run from backend/ (after `pip install -e ".[dev]"`):
    python -m scripts.backtest_report [--lookback-days 180] [--horizon-days 1]
        [--max-symbols 300] [--symbols AAPL,TSLA,NVDA] [--batch-size 500]

NOT covered by this report -- see app.scanners.backtest's module
docstring for why each is out of scope for this pass:
  - Catalyst/headline boost (needs historical news data, a separate phase)
  - Float (FMP's bulk file is TODAY's float -- applying it to past dates
    is look-ahead bias)
  - Time-of-day-normalized RVOL (daily bars are whole sessions, so the
    session fraction is 1.0 and the normalized definition is identical
    here -- re-deriving formulas._FADE_RISK_RVOL for it needs an intraday
    replay this tool can't do at any lookback)
  - The momentum alarm itself: 15m % + shaved top/bottom (15m % is
    inherently a minute-resolution concept, needs historical minute bars)
  - Point-in-time universe membership -- this backtests TODAY's universe
    against past dates, which has survivorship bias.

The "most_active" view is replayed but reads differently from the other
two: --max-symbols selects by avg_dollar_vol_20d, so this ranks by dollar
volume over a set already pre-sorted by dollar volume. Expect it
near-degenerate at small --max-symbols.

Does cover is_shaved_top (app.market_data.candle_shape) on its own,
without the 15m % gate the live momentum alarm pairs it with: did the
entry day's own candle close at/near its high, regardless of how it got
there? This only needs the daily bar already being fetched, not minute
data.
"""

import argparse
import asyncio

from app.alpaca.client import AlpacaClients
from app.alpaca.universe import build_universe
from app.core.config import get_settings
from app.scanners import formulas
from app.scanners.backtest import run_backtest


def _fmt_bucket(label: str, stats: dict) -> str:
    if stats["sample_size"] == 0:
        return f"  {label}: no picks"
    return (
        f"  {label}: n={stats['sample_size']}, "
        f"win_rate={stats['win_rate']}%, avg_return={stats['avg_return']}%"
    )


def _print_report(report: dict) -> None:
    print(
        f"Backtest report -- {report['lookback_days']} calendar days back, "
        f"{report['horizon_days']}-trading-day forward return"
    )
    print(
        f"Universe: {report['symbol_count']} symbols requested, "
        f"{report['symbols_with_bars']} returned bar data"
    )
    print(f"Total ranked picks reconstructed: {report['sample_size']}\n")

    print("** NOT covered by this report: catalyst/headline boost (needs historical")
    print("   news, a separate phase), float (bulk float is today's, not point-in-")
    print("   time), time-of-day-normalized RVOL (identical to raw RVOL at daily")
    print("   resolution), the momentum alarm itself (15m % is a minute-resolution")
    print("   concept), and today's universe membership is applied across the whole")
    print("   lookback window (survivorship bias).")
    print("   most_active reads differently from the other two views: --max-symbols")
    print("   already pre-sorts by dollar volume, so its picks repeat far more than the")
    print("   other views' -- measured at the defaults it drew on 62.6% of the symbol")
    print("   pool with 16 names present on >=90% of days, against ~100% and none for")
    print("   gainers/losers. Concentrated, not degenerate; tightens further at small N. **\n")

    if report["sample_size"] == 0:
        print("No picks reconstructed -- nothing to report.")
        return

    min_n = report["min_sample_size"]

    print(f"Gap-size buckets (win rate / avg {report['horizon_days']}-day-forward return):")
    for row in report["gap_buckets"]:
        flag = "" if row["sample_size"] >= min_n else "  ** below n={} floor, noisy **".format(min_n)
        print(
            f"  {row['view']:10s} {row['bucket']:>7s}: n={row['sample_size']:4d} "
            f"win_rate={row['win_rate']}% avg_return={row['avg_return']}%{flag}"
        )
    print()

    print(f"RVOL buckets (win rate / avg {report['horizon_days']}-day-forward return):")
    for row in report["rvol_buckets"]:
        flag = "" if row["sample_size"] >= min_n else "  ** below n={} floor, noisy **".format(min_n)
        print(
            f"  {row['view']:10s} {row['bucket']:>7s}: n={row['sample_size']:4d} "
            f"win_rate={row['win_rate']}% avg_return={row['avg_return']}%{flag}"
        )
    print()

    benchmark = report.get("benchmark_symbol", "benchmark")
    print(f"Vs {benchmark} -- did the pick beat the market, or just go up with it?")
    if not report.get("alpha"):
        print("  No benchmark data -- every win rate below is raw.\n")
    else:
        for row in report["alpha"]:
            if row["alpha_win_rate"] is None:
                print(f"  {row['view']:12s} n={row['sample_size']:5d}  no benchmark overlap")
                continue
            flag = "" if row["sufficient_sample"] else f"  ** below n={min_n} floor, noisy **"
            print(
                f"  {row['view']:12s} n={row['sample_size_with_benchmark']:5d}  "
                f"win_rate={row['win_rate']}%  alpha_win_rate={row['alpha_win_rate']}%  "
                f"avg_alpha={row['avg_alpha']}%  median_alpha={row['median_alpha']}%{flag}"
            )
        print("  win_rate is 'closed positive'; alpha_win_rate is 'beat the benchmark'.")
        print("  On a broadly green day every long closes positive -- only the second")
        print("  number says whether the ranking itself contributed anything.\n")

    fade_risk = report["fade_risk"]
    threshold = fade_risk["threshold"]
    print(f"Fade risk (formulas._FADE_RISK_RVOL = {threshold}x, discount {1 - formulas._FADE_RISK_DISCOUNT:.0%} above it):")
    # Per view, never pooled -- "win" means the opposite thing on losers, so
    # a pooled number averages contradictory signals. Same breakdown the live
    # drift report uses (history_store._fade_risk_drift).
    for entry in fade_risk["views"]:
        print(f"  {entry['view']}:")
        print(_fmt_bucket(f"    rvol > {threshold}x", entry["rvol_above_threshold"]))
        print(_fmt_bucket(f"    rvol <= {threshold}x", entry["rvol_at_or_below_threshold"]))
        if not entry["sufficient_sample"]:
            print(f"    ** below n={min_n} for the >threshold group -- treat as noisy **")
    print()

    print(f"Shaved top (entry day's candle closed at/near its high, avg {report['horizon_days']}-day-forward return):")
    for row in report["shaved_top"]:
        print(f"  {row['view']}:")
        print(_fmt_bucket("    shaved top", row["shaved_top"]))
        print(_fmt_bucket("    not shaved top", row["not_shaved_top"]))
        if not row["sufficient_sample"]:
            print(f"    ** below n={min_n} floor in at least one group -- treat as noisy **")


async def _main(args: argparse.Namespace) -> None:
    settings = get_settings()
    clients = AlpacaClients(settings)

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        universe = await build_universe(clients, settings)
        ranked = sorted(universe.values(), key=lambda u: u.avg_dollar_vol_20d, reverse=True)
        symbols = [u.symbol for u in ranked[: args.max_symbols]]

    report = await run_backtest(
        clients,
        settings,
        symbols,
        lookback_days=args.lookback_days,
        horizon_days=args.horizon_days,
        batch_size=args.batch_size,
    )
    _print_report(report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lookback-days", type=int, default=180, help="Calendar days of history to replay (default: 180)")
    parser.add_argument("--horizon-days", type=int, default=1, help="Trading days forward to measure the outcome (default: 1)")
    parser.add_argument(
        "--max-symbols",
        type=int,
        default=300,
        help="Top-N of the live universe by dollar volume to backtest, ignored if --symbols is given (default: 300)",
    )
    parser.add_argument("--symbols", default=None, help="Comma-separated symbol override, e.g. AAPL,TSLA,NVDA")
    parser.add_argument(
        "--batch-size", type=int, default=500, help="Symbols per historical-bars request (default: 500)"
    )
    asyncio.run(_main(parser.parse_args()))
