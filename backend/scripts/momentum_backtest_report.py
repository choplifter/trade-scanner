"""Minute-resolution historical backtest for the live momentum alarm: does
requiring the shaved-top/green/above-VWAP confirmation actually improve
on the 15m% threshold alone? Replays historical minute bars through the
*actual* production functions (see app.scanners.momentum_backtest) -- not
a reimplementation of the live alarm's logic, the literal same
formulas.is_momentum_alert call. Long side only, matching the live alarm
itself.

Run from backend/ (after `pip install -e ".[dev]"`):
    python -m scripts.momentum_backtest_report [--lookback-days 30]
        [--horizon-minutes 15] [--threshold 7.0] [--max-symbols 100]
        [--symbols AAPL,TSLA,NVDA] [--force-refresh-cache] [--cache-max-age-hours 12]

--threshold overrides ALARM_MOMENTUM_PCT_THRESHOLD for just this run --
the same cached bars can be replayed against several threshold values
without touching backend/.env each time (the fetch is threshold-
independent, so a cache hit still applies even if only --threshold
changed between runs).

Fetches are cached to disk (see app.scanners.bar_cache) since a multi-
week minute-bar pull for a few hundred symbols can take real wall-clock
time (potentially minutes) and return millions of bars -- re-fetching on
every parameter tweak would make iterating painful. Pass
--force-refresh-cache to bypass a cached fetch.

Defaults are intentionally modest (30 days, 100 symbols) compared to the
daily-bar backtest's 180 days / 300 symbols -- minute-resolution data
volume scales with lookback-days x symbol-count much more steeply, so
start small and widen only once a run has proven out at this scale.

NOT covered: catalyst/headline boost (needs historical news, unbuilt),
and there's no cross-sectional ranking check the way the daily backtest
has (engine._rank_gainers/_rank_losers) -- this only asks whether the
alert *condition itself*, wherever/whenever it would have fired, predicted
a better outcome than the threshold alone, not whether it would have
been a top-ranked scanner pick that day.
"""

import argparse
import asyncio
from pathlib import Path

from app.alpaca.client import AlpacaClients
from app.alpaca.universe import build_universe
from app.core.config import get_settings
from app.scanners.bar_cache import DEFAULT_CACHE_DIR
from app.scanners.momentum_backtest import run_momentum_backtest


def _fmt_bucket(label: str, stats: dict, min_n: int) -> str:
    if stats["sample_size"] == 0:
        return f"  {label}: no picks"
    flag = "" if stats["sample_size"] >= min_n else "  ** below n={} floor, noisy **".format(min_n)
    return (
        f"  {label}: n={stats['sample_size']}, "
        f"win_rate={stats['win_rate']}%, avg_return={stats['avg_return']}%{flag}"
    )


def _print_report(report: dict) -> None:
    print(
        f"Momentum backtest report -- {report['lookback_days']} calendar days back, "
        f"{report['horizon_minutes']}-minute forward return, threshold={report['threshold']}%"
    )
    print(
        f"Universe: {report['symbol_count']} symbols requested, "
        f"{report['symbols_with_bars']} returned bar data"
    )
    print(f"Total trigger events reconstructed: {report['sample_size']}\n")

    print("** NOT covered: catalyst/headline boost (needs historical news, unbuilt).")
    print("   No cross-sectional ranking check (unlike the daily backtest) -- this only")
    print("   asks whether the alert condition itself predicted a better outcome, not")
    print("   whether it would have been a top-ranked scanner pick that day. **\n")

    if report["sample_size"] == 0:
        print("No trigger events reconstructed -- nothing to report.")
        return

    min_n = report["min_sample_size"]
    comparison = report["comparison"]

    print(f"Threshold only (15m% >= {report['threshold']}%, no confirmation check):")
    print(_fmt_bucket("threshold only", comparison["threshold_only"], min_n))
    print()

    print(f"Full alert (15m% >= {report['threshold']}% AND shaved top AND green AND above VWAP):")
    print(_fmt_bucket("full alert", comparison["full_alert"], min_n))


async def _main(args: argparse.Namespace) -> None:
    settings = get_settings()
    clients = AlpacaClients(settings)

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        universe = await build_universe(clients, settings)
        ranked = sorted(universe.values(), key=lambda u: u.avg_dollar_vol_20d, reverse=True)
        symbols = [u.symbol for u in ranked[: args.max_symbols]]

    report = await run_momentum_backtest(
        clients,
        settings,
        symbols,
        lookback_days=args.lookback_days,
        horizon_minutes=args.horizon_minutes,
        threshold=args.threshold,
        cache_dir=Path(args.cache_dir),
        force_refresh=args.force_refresh_cache,
        max_age_hours=args.cache_max_age_hours,
    )
    _print_report(report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lookback-days", type=int, default=30, help="Calendar days of minute-bar history to replay (default: 30)")
    parser.add_argument("--horizon-minutes", type=int, default=15, help="Bars forward to measure the outcome (default: 15)")
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="15m%% momentum threshold to test, overriding ALARM_MOMENTUM_PCT_THRESHOLD "
        "for just this run (default: whatever Settings.alarm_momentum_pct_threshold resolves to, 5.0 unless overridden in .env)",
    )
    parser.add_argument(
        "--max-symbols",
        type=int,
        default=100,
        help="Top-N of the live universe by dollar volume to backtest, ignored if --symbols is given (default: 100)",
    )
    parser.add_argument("--symbols", default=None, help="Comma-separated symbol override, e.g. AAPL,TSLA,NVDA")
    parser.add_argument(
        "--force-refresh-cache", action="store_true", help="Bypass a cached fetch and re-download from Alpaca"
    )
    parser.add_argument(
        "--cache-max-age-hours", type=float, default=12.0, help="Treat a cached fetch older than this as stale (default: 12)"
    )
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR), help="Where cached bar fetches are stored")
    asyncio.run(_main(parser.parse_args()))
