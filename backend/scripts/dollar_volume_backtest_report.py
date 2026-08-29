"""Daily-bar sweep of settings.scanner_min_dollar_volume: does win rate
improve as the tradability floor rises, and where (if anywhere) does raising
it stop mattering? See app.scanners.backtest.dollar_volume_sweep for the
method.

Run from backend/ (after `pip install -e ".[dev]"`):
    python -m scripts.dollar_volume_backtest_report [--lookback-days 180]
        [--horizon-days 1] [--from-history] [--max-symbols 300]
        [--symbols AAPL,TSLA] [--thresholds 0,500000,1000000,...]

--from-history is strongly preferred, same reasoning as
rvol_backtest_report.py: symbols selected by top-N avg_dollar_vol_20d are
already liquid enough to clear almost any floor in the sweep, which would
hide exactly the effect being measured. --from-history uses every symbol
that has actually been ranked, including the thin ones that only barely
cleared today's $1M floor -- the population this setting actually gates.

READ THE BASE RATE FIRST. A higher floor doesn't just filter which symbols
the ranking sees -- it also selects a generally safer, more liquid
population on its own, same as metric_validation_report.py's base rate
exists to isolate for the ranking generally. So each view's win_rate here
is printed next to `edge`, the same floor's base rate subtracted off (a
random *tradable-at-that-floor* symbol-day). If win_rate rises with the
floor but edge doesn't, the floor is just trading toward calmer names, not
making the ranking better -- and there'd be no reason to give up sample
size for it.
"""

import argparse
import asyncio
import sqlite3

from app.alpaca.client import AlpacaClients
from app.alpaca.universe import build_universe
from app.core.config import get_settings
from app.scanners import bucket_analysis
from app.scanners.backtest import (
    _BENCHMARK_SYMBOL,
    _FETCH_BATCH_SIZE,
    _WARMUP_CALENDAR_PADDING_DAYS,
    benchmark_returns_by_date,
    dollar_volume_sweep,
)
from app.scanners.metric_validation import base_rate
from app.market_data.bars import get_daily_bars_multi

DEFAULT_THRESHOLDS = [0, 500_000, 1_000_000, 2_000_000, 5_000_000, 10_000_000, 20_000_000, 30_000_000, 50_000_000]


def _fmt_dollars(value: float) -> str:
    return "$0" if value == 0 else f"${value / 1_000_000:.1f}M"


def _symbols_from_history(db_path: str) -> list[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT DISTINCT symbol FROM appearances ORDER BY symbol").fetchall()
    return [symbol for (symbol,) in rows]


def _print_report(
    rows: list[dict],
    base_rates: dict[float, dict | None],
    lookback_days: int,
    horizon_days: int,
    live_floor: float,
) -> None:
    print(
        f"Dollar-volume floor sweep -- {lookback_days} calendar days back, "
        f"{horizon_days}-trading-day forward return\n"
    )
    print(f"Live setting: scanner_min_dollar_volume = {_fmt_dollars(live_floor)}\n")

    print("** BASE RATE -- random tradable-at-that-floor symbol-day (read this first) **")
    print(f"  {'floor':>8s}  {'n':>6s}  {'win_rate':>8s}  {'avg_return':>10s}")
    for threshold, stats in base_rates.items():
        marker = "  <- live" if threshold == live_floor else ""
        if not stats or not stats["sample_size"]:
            print(f"  {_fmt_dollars(threshold):>8s}  no data{marker}")
            continue
        print(
            f"  {_fmt_dollars(threshold):>8s}  {stats['sample_size']:>6d}  "
            f"{stats['win_rate']:>7.1f}%  {stats['mean']:>9.2f}%{marker}"
        )
    print()

    by_view: dict[str, list[dict]] = {}
    for row in rows:
        by_view.setdefault(row["view"], []).append(row)

    for view_name, view_rows in by_view.items():
        print(f"{view_name}:")
        print(
            f"  {'floor':>8s}  {'n':>6s}  {'symbols':>7s}  {'win_rate':>8s}  "
            f"{'avg_return':>10s}  {'edge':>7s}"
        )
        for row in view_rows:
            marker = "  <- live" if row["min_dollar_volume"] == live_floor else ""
            flag = "" if row["sufficient_sample"] else f"  ** n<{bucket_analysis.MIN_SAMPLE_SIZE}, noisy **"
            win = "-" if row["win_rate"] is None else f"{row['win_rate']}%"
            ret = "-" if row["avg_return"] is None else f"{row['avg_return']}%"
            base = base_rates.get(row["min_dollar_volume"])
            edge = (
                "-"
                if row["win_rate"] is None or not base or not base["sample_size"]
                else f"{row['win_rate'] - base['win_rate']:+.1f}pp"
            )
            print(
                f"  {_fmt_dollars(row['min_dollar_volume']):>8s}  {row['sample_size']:>6d}  "
                f"{row['distinct_symbols']:>7d}  {win:>8s}  {ret:>10s}  {edge:>7s}{marker}{flag}"
            )
        print()

    print("Reading this: 'edge' is this view's win_rate minus the SAME floor's base rate --")
    print("the part of the win rate the ranking itself is responsible for, with the")
    print("population effect subtracted out. Follow edge, not win_rate, across each view's")
    print("row as the floor rises. If win_rate improves but edge doesn't, the floor is just")
    print("trading toward a calmer population, not a better-ranked one, and raising it")
    print("further would only shrink the sample for no benefit. A view that gets *worse* at")
    print("a higher floor (fewer, more concentrated names) is also a valid finding -- see")
    print("distinct_symbols before trusting any of this at a small n.")


async def _main(args: argparse.Namespace) -> None:
    settings = get_settings()
    clients = AlpacaClients(settings)

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    elif args.from_history:
        symbols = _symbols_from_history(settings.scanner_history_db_path)
        if args.max_symbols:
            symbols = symbols[: args.max_symbols]
        print(f"Symbols: {len(symbols)} from {settings.scanner_history_db_path} (previously ranked)\n")
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
    benchmark_returns = benchmark_returns_by_date(benchmark_bars, args.horizon_days)

    print(f"{len(bars_by_symbol)}/{len(symbols)} symbols returned bars\n")

    thresholds = (
        [float(x.strip()) for x in args.thresholds.split(",") if x.strip()]
        if args.thresholds
        else DEFAULT_THRESHOLDS
    )
    rows = dollar_volume_sweep(bars_by_symbol, thresholds, args.horizon_days, benchmark_returns)
    base_rates = {t: base_rate(bars_by_symbol, args.horizon_days, t) for t in thresholds}
    _print_report(
        rows, base_rates, args.lookback_days, args.horizon_days, settings.scanner_min_dollar_volume
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lookback-days", type=int, default=180, help="Calendar days of history to replay (default: 180)")
    parser.add_argument("--horizon-days", type=int, default=1, help="Trading days forward to measure the outcome (default: 1)")
    parser.add_argument("--max-symbols", type=int, default=300, help="Cap on the symbol list; 0 for no cap with --from-history (default: 300)")
    parser.add_argument("--symbols", default=None, help="Comma-separated symbol override, e.g. AAPL,TSLA")
    parser.add_argument(
        "--from-history",
        action="store_true",
        help="Use every symbol that has actually been ranked (scanner_history.sqlite3) instead of the "
        "universe's top-N by dollar volume -- strongly preferred, see module docstring.",
    )
    parser.add_argument("--thresholds", default=None, help="Comma-separated dollar-volume floors to sweep")
    asyncio.run(_main(parser.parse_args()))
