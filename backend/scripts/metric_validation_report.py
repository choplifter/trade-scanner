"""Does the scanner's ranking predict anything, and how high can its win
rate honestly go? See app.scanners.metric_validation for the method.

Run from backend/ (after `pip install -e ".[dev]"`):
    python -m scripts.metric_validation_report [--lookback-days 180]
        [--from-history] [--max-symbols 300] [--symbols AAPL,TSLA]
        [--oos-fraction 0.30]

This report is built to be hostile to the scanner. Its two headline numbers
are the base rate (what picking at random from the same universe would have
done -- the bar the ranking has to clear) and the out-of-sample column of
the condition search (what the best-looking rules do on dates they weren't
chosen from). A tool that can only confirm the scanner works would be worth
nothing here.
"""

import argparse
import asyncio
import sqlite3

from app.alpaca.client import AlpacaClients
from app.alpaca.universe import build_universe
from app.core.config import get_settings
from app.market_data.bars import get_daily_bars_multi
from app.scanners.backtest import (
    _BENCHMARK_SYMBOL,
    _FETCH_BATCH_SIZE,
    _WARMUP_CALENDAR_PADDING_DAYS,
    benchmark_returns_by_date,
    simulate_from_bars,
)
from app.scanners.metric_validation import (
    DEFAULT_OOS_FRACTION,
    MIN_CONDITION_SAMPLE,
    base_rate,
    overfitting_gauge,
    per_view_expectancy,
    picks_by_horizon_summary,
    search_conditions,
    split_by_date,
)

_HORIZONS = (1, 2, 3, 5)


def _fmt(stats: dict | None) -> str:
    if not stats:
        return "(no data)"
    payoff = "-" if stats["payoff_ratio"] is None else f"{stats['payoff_ratio']}"
    return (
        f"n={stats['sample_size']:6d}  win={stats['win_rate']:5.1f}%  "
        f"median={stats['median']:+6.2f}%  mean={stats['mean']:+7.2f}%  "
        f"avgW={stats['avg_win']:+6.2f}%  avgL={stats['avg_loss']:+6.2f}%  payoff={payoff}"
    )


def _print_report(report: dict) -> None:
    print("=" * 100)
    print("A. BASE RATE -- what picking at random from this same universe would have done")
    print("=" * 100)
    print(f"  random tradable day  {_fmt(report['base_rate'])}")
    print(f"  (any day, unfiltered) {_fmt(report['base_rate_unfiltered'])}")
    print("  The first line is the bar -- it applies the same dollar-volume filter the")
    print("  ranking does, so both populations were actually rankable. The second counts")
    print("  illiquid days the scanner would never surface; it is shown only to make the")
    print("  difference visible, and comparing against it would flatter nothing.")
    print("  A view whose win rate matches the first line is sorting noise.\n")

    print("=" * 100)
    print(f"B. PER VIEW -- raw outcome, and alpha vs {report['benchmark_symbol']}")
    print("=" * 100)
    for row in report["per_view"]:
        print(f"  {row['view']}")
        print(f"    raw    {_fmt(row['raw'])}")
        print(f"    alpha  {_fmt(row['alpha'])}")
    print("  'raw' counts closing positive; 'alpha' counts beating the benchmark.")
    print("  On a green day every long closes positive -- only alpha isolates the ranking.\n")

    print("=" * 100)
    print("C. HOLDING HORIZON -- is the signal just slower than 1 day?")
    print("=" * 100)
    for row in report["by_horizon"]:
        print(
            f"  {row['horizon_days']}d  {row['view']:12s} n={row['sample_size']:6d}  "
            f"win={row['win_rate']:5.1f}%  median={row['median']:+6.2f}%  "
            f"mean={row['mean']:+7.2f}%  payoff={row['payoff_ratio']}"
        )
    print()

    print("=" * 100)
    print("D. CONDITION SEARCH -- best in-sample rules, and what they did out-of-sample")
    print("=" * 100)
    search = report["search"]
    print(
        f"  in-sample < {report['split_date']} ({report['in_sample_size']} picks), "
        f"out-of-sample >= {report['split_date']} ({report['out_of_sample_size']} picks)"
    )
    print(f"  {len(search)} conditions cleared the n>={MIN_CONDITION_SAMPLE} in-sample floor.\n")
    for result in search[: report["top_n"]]:
        print(f"  {result['label']}")
        print(f"    IN   {_fmt(result['in_sample'])}")
        print(f"    OUT  {_fmt(result['out_of_sample'])}")
    print()

    gauge = report["overfitting"]
    print("=" * 100)
    print("OVERFITTING GAUGE")
    print("=" * 100)
    if gauge is None:
        print("  Not enough out-of-sample data behind the top rules to judge.\n")
    else:
        print(f"  Across the top {gauge['rules_compared']} in-sample rules with a usable OOS sample:")
        print(f"    mean win-rate drop   {gauge['mean_win_rate_drop_pp']:+.1f}pp")
        print(f"    median win-rate drop {gauge['median_win_rate_drop_pp']:+.1f}pp")
        print(f"    rules that held up or improved: {gauge['rules_that_held_up']}/{gauge['rules_compared']}")
        print()
        print("  A drop near zero means the search found something structural. A large")
        print("  positive drop means it found the shape of its own sample -- and is the")
        print("  measured answer to how much a win-rate target can be reached by picking")
        print("  conditions off a backtest.")


async def _build_report(args) -> dict:
    settings = get_settings()
    clients = AlpacaClients(settings)

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    elif args.from_history:
        with sqlite3.connect(settings.scanner_history_db_path) as conn:
            symbols = [s for (s,) in conn.execute("SELECT DISTINCT symbol FROM appearances ORDER BY symbol")]
        if args.max_symbols:
            symbols = symbols[: args.max_symbols]
    else:
        universe = await build_universe(clients, settings)
        ranked = sorted(universe.values(), key=lambda u: u.avg_dollar_vol_20d, reverse=True)
        symbols = [u.symbol for u in ranked[: args.max_symbols]]

    fetch_lookback = args.lookback_days + _WARMUP_CALENDAR_PADDING_DAYS
    bars_by_symbol: dict[str, list] = {}
    for i in range(0, len(symbols), _FETCH_BATCH_SIZE):
        batch = symbols[i : i + _FETCH_BATCH_SIZE]
        bars_by_symbol.update(await get_daily_bars_multi(clients, batch, lookback_days=fetch_lookback))
    benchmark_bars = (
        await get_daily_bars_multi(clients, [_BENCHMARK_SYMBOL], lookback_days=fetch_lookback)
    ).get(_BENCHMARK_SYMBOL) or []

    print(f"{len(bars_by_symbol)}/{len(symbols)} symbols returned bars; "
          f"{len(benchmark_bars)} {_BENCHMARK_SYMBOL} bars\n")

    picks_by_horizon = {
        horizon: simulate_from_bars(
            bars_by_symbol,
            settings.scanner_min_dollar_volume,
            horizon,
            benchmark_returns_by_date(benchmark_bars, horizon),
        )
        for horizon in _HORIZONS
    }
    picks = picks_by_horizon[1]
    in_sample, out_of_sample = split_by_date(picks, args.oos_fraction)
    search = search_conditions(in_sample, out_of_sample)

    return {
        "benchmark_symbol": _BENCHMARK_SYMBOL,
        # Same tradability filter the ranking uses, so this is "a random
        # *rankable* symbol-day" rather than "any symbol-day at all".
        "base_rate": base_rate(bars_by_symbol, 1, settings.scanner_min_dollar_volume),
        "base_rate_unfiltered": base_rate(bars_by_symbol),
        "per_view": per_view_expectancy(picks),
        "by_horizon": picks_by_horizon_summary(picks_by_horizon),
        "split_date": min((p["trading_date"] for p in out_of_sample), default="n/a"),
        "in_sample_size": len(in_sample),
        "out_of_sample_size": len(out_of_sample),
        "search": search,
        "overfitting": overfitting_gauge(search),
        "top_n": args.top,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lookback-days", type=int, default=180, help="Calendar days of history (default: 180)")
    parser.add_argument("--max-symbols", type=int, default=300, help="Cap on the symbol list; 0 for no cap (default: 300)")
    parser.add_argument("--symbols", default=None, help="Comma-separated symbol override")
    parser.add_argument(
        "--from-history",
        action="store_true",
        help="Use every symbol that has actually been ranked (scanner_history.sqlite3) rather than the "
        "universe's top-N by dollar volume -- the population the metrics actually describe",
    )
    parser.add_argument(
        "--oos-fraction", type=float, default=DEFAULT_OOS_FRACTION,
        help=f"Share of trading dates held out of the condition search (default: {DEFAULT_OOS_FRACTION})",
    )
    parser.add_argument("--top", type=int, default=8, help="How many searched conditions to print (default: 8)")
    _print_report(asyncio.run(_build_report(parser.parse_args())))
