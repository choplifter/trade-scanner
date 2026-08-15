"""Parameter sweep for the momentum backtest's long side: which 15m%
threshold and forward-return horizon combination looks best against
history? Fetches 5-minute bars ONCE (cached, see app.scanners.bar_cache)
then cheaply re-simulates app.scanners.momentum_backtest.
simulate_momentum_alerts in memory for every (threshold, horizon)
combination in the grid -- no re-fetching per combination, so widening
the grid only costs more (fast) simulation, not more network time.

Run from backend/ (after `pip install -e ".[dev]"`):
    python -m scripts.momentum_param_sweep [--lookback-days 30]
        [--thresholds 1,2,3,5,7,10] [--horizons 5,10,15,30,60]
        [--max-symbols 100] [--symbols AAPL,TSLA,NVDA]
        [--force-refresh-cache] [--cache-max-age-hours 12]

Results are ranked by full-alert avg return, with combinations under the
n=30 sample floor pushed to the bottom regardless of how good their
number looks -- a lucky n=2 sample topping the list by raw avg_return
would be exactly the kind of overstated-thin-result this app has tried
hard to avoid elsewhere (the ranking-drift and backtest reports both do
the same noise-floor flagging).

Same scope as momentum_backtest_report.py: long side only, no catalyst/
headline data (needs historical news, unbuilt), no cross-sectional
ranking check (unlike the daily backtest).
"""

import argparse
import asyncio
from pathlib import Path

from app.alpaca.client import AlpacaClients
from app.alpaca.universe import build_universe
from app.core.config import get_settings
from app.scanners import bucket_analysis
from app.scanners.bar_cache import DEFAULT_CACHE_DIR, get_cached_5m_bars_multi
from app.scanners.momentum_backtest import sweep_momentum_params


def _sufficient(result: dict) -> bool:
    return result["full_alert"]["sample_size"] >= bucket_analysis.MIN_SAMPLE_SIZE


def _full_alert_avg_return(result: dict) -> float:
    avg_return = result["full_alert"]["avg_return"]
    return avg_return if avg_return is not None else float("-inf")


def _rank(results: list[dict]) -> list[dict]:
    """Sufficient-sample combinations first (best full-alert avg return
    first), then thin ones after (same sort among themselves) -- never
    let a thin sample's number outrank a reliable one just because it
    happens to look bigger.
    """
    sufficient = sorted((r for r in results if _sufficient(r)), key=_full_alert_avg_return, reverse=True)
    thin = sorted((r for r in results if not _sufficient(r)), key=_full_alert_avg_return, reverse=True)
    return sufficient + thin


def _fmt_stats(stats: dict) -> str:
    if stats["sample_size"] == 0:
        return f"{'0':>4} {'--':>7} {'--':>8}"
    return f"{stats['sample_size']:>4} {stats['win_rate']:>6.1f}% {stats['avg_return']:>7.2f}%"


def _print_report(results: list[dict], symbol_count: int, symbols_with_bars: int, lookback_days: int) -> None:
    print(f"Momentum parameter sweep -- {lookback_days} calendar days back")
    print(f"Universe: {symbol_count} symbols requested, {symbols_with_bars} returned bar data")
    print(f"{len(results)} (threshold, horizon) combinations tested\n")

    print("** Long side only, no catalyst/headline data, no cross-sectional ranking check --")
    print("   same scope as momentum_backtest_report.py. Ranked by full-alert avg return;")
    print(f"   combinations under n={bucket_analysis.MIN_SAMPLE_SIZE} are pushed to the bottom")
    print("   regardless of how good the number looks. **\n")

    print(f"{'Threshold':>9} {'Horizon':>8} | {'Threshold-only':^19} | {'Full-alert':^19}")
    print(f"{'':>9} {'':>8} | {'n':>4} {'win%':>6} {'avg%':>8} | {'n':>4} {'win%':>6} {'avg%':>8}")

    for r in _rank(results):
        flag = "" if _sufficient(r) else "  ** noisy (n<{}) **".format(bucket_analysis.MIN_SAMPLE_SIZE)
        print(
            f"{r['threshold']:>8.1f}% {r['horizon_minutes']:>6}min "
            f"| {_fmt_stats(r['threshold_only'])} "
            f"| {_fmt_stats(r['full_alert'])}{flag}"
        )


async def _main(args: argparse.Namespace) -> None:
    settings = get_settings()
    clients = AlpacaClients(settings)

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        universe = await build_universe(clients, settings)
        ranked = sorted(universe.values(), key=lambda u: u.avg_dollar_vol_20d, reverse=True)
        symbols = [u.symbol for u in ranked[: args.max_symbols]]

    bars_by_symbol = await get_cached_5m_bars_multi(
        clients,
        symbols,
        args.lookback_days,
        cache_dir=Path(args.cache_dir),
        force_refresh=args.force_refresh_cache,
        max_age_hours=args.cache_max_age_hours,
    )

    thresholds = [float(x) for x in args.thresholds.split(",")]
    horizons = [int(x) for x in args.horizons.split(",")]
    results = sweep_momentum_params(bars_by_symbol, thresholds, horizons)

    _print_report(results, len(symbols), len(bars_by_symbol), args.lookback_days)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lookback-days", type=int, default=30, help="Calendar days of 5-minute-bar history to replay (default: 30)")
    parser.add_argument(
        "--thresholds", default="1,2,3,5,7,10", help="Comma-separated 15m%% thresholds to test (default: 1,2,3,5,7,10)"
    )
    parser.add_argument(
        "--horizons",
        default="5,10,15,30,60",
        help="Comma-separated forward-return horizons in minutes, each rounded to the nearest "
        "5-minute bar (default: 5,10,15,30,60)",
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
