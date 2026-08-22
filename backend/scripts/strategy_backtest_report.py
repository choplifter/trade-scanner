"""Replay one registered strategy over 5-minute bars and report what it was
worth, in R.

Different question from every other backtest script here. Those measure a
fixed forward horizon -- "where was price 15 minutes later" -- which says
whether a signal precedes drift. This one gives each signal its own
structural stop and target and scores it where the trade actually ended, so
the answer accounts for the path and not only the destination. See
app.scanners.strategy_backtest.

Run from backend/ (after `pip install -e ".[dev]"`):
    python -m scripts.strategy_backtest_report --strategy vwap
        [--lookback-days 30] [--cost-bps 10] [--max-symbols 100]
        [--symbols AAPL,TSLA,NVDA] [--force-refresh-cache]

--cost-bps is charged entering and leaving. It is an assumption, not a
measurement -- historical quotes are not fetched anywhere in this app, so
spread cannot be backtested (see backtest.BACKTESTABLE_FIELDS). Leaving it
at 0 reports the gross figure; a real edge has to survive a realistic one.

Fetches are cached to disk (see app.scanners.bar_cache), so replaying
several strategies over the same lookback costs one download.
"""

import argparse
import asyncio
from pathlib import Path

from app.alpaca.client import AlpacaClients
from app.alpaca.universe import build_universe
from app.core.config import get_settings
from app.scanners import universes
from app.scanners.bar_cache import DEFAULT_CACHE_DIR
from app.scanners.strategy_backtest import run_strategy_backtest
from app.strategies.loader import load_strategies


def _print_report(report: dict) -> None:
    print(
        f"Strategy backtest -- {report['strategy']}, {report['lookback_days']} calendar "
        f"days back, costs {report['cost_bps']}bp per side"
    )
    print(
        f"Universe: {report['universe']} -- {report['symbol_count']} symbols requested, "
        f"{report['symbols_with_bars']} returned bar data"
    )
    print(f"Signals (rising edge, one per event): {report['sample_size']}\n")

    if report["sample_size"] == 0:
        print("No signals fired -- nothing to report.")
        return

    stats = report["expectancy"]
    min_n = report["min_sample_size"]
    if report["sample_size"] < min_n:
        print(f"** n={report['sample_size']} is below the n={min_n} floor -- too noisy to call. **\n")

    # Expectancy first, win rate second and deliberately so: a rule taking
    # +0.3R wins and -1R losses can win 70% of the time and still lose money.
    print(f"Expectancy:  {stats['expectancy_r']:+.3f}R per trade")
    print(f"Win rate:    {stats['win_rate']}%")
    print(f"Avg win:     {stats['avg_win_r']}R")
    print(f"Avg loss:    {stats['avg_loss_r']}R")
    print(f"Exits:       {stats['exits']}")

    ambiguous = stats["ambiguous_exits"]
    share = ambiguous / report["sample_size"] * 100
    print(f"\nAmbiguous:   {ambiguous} of {report['sample_size']} exits ({share:.1f}%)")
    print("  Bars that touched stop and target both. A 5-minute bar does not say")
    print("  which came first, so these are scored as stops by assumption. The")
    print("  higher this share, the more the result rests on that assumption")
    print("  rather than on the tape -- read it as pessimistic by an unmeasured")
    print("  amount, not as a number.")

    returns = report["returns"]
    print(
        f"\nSame exits as a percentage move: n={returns['sample_size']}, "
        f"win_rate={returns['win_rate']}%, avg_return={returns['avg_return']}%"
    )
    print("  Measured cost-free, so it stays comparable with the other backtests")
    print("  in this app. The R figures above carry the cost; these do not.")


async def _main(args: argparse.Namespace) -> None:
    # Loaded once, here, and held for the whole walk -- a report has to
    # describe one version of a rule, not whichever version was on disk as
    # the walk passed each bar.
    strategies, errors = load_strategies(only=args.strategy)

    # Reported before anything else runs. A file that failed to load produces
    # no signals, which looks exactly like a rule that found no setups.
    for err in errors:
        print(f"!! {err.filename} failed to load: {err.error}")

    if not strategies:
        available, _ = load_strategies()
        known = ", ".join(s.name for s in available) or "none found"
        raise SystemExit(f"No strategy matched {args.strategy!r}. Available: {known}.")
    if len(strategies) > 1:
        names = ", ".join(s.name for s in strategies)
        raise SystemExit(f"{args.strategy!r} matched more than one strategy: {names}.")
    strategy = strategies[0]

    settings = get_settings()
    clients = AlpacaClients(settings)

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        universe = await build_universe(clients, settings)
        symbols = await universes.select(
            clients, universe, args.universe, args.max_symbols, args.lookback_days
        )
        if not symbols:
            raise SystemExit(f"No symbols matched the {args.universe!r} universe.")

    report = await run_strategy_backtest(
        clients,
        strategy,
        symbols,
        lookback_days=args.lookback_days,
        cost_bps=args.cost_bps,
        cache_dir=Path(args.cache_dir),
        force_refresh=args.force_refresh_cache,
        max_age_hours=args.cache_max_age_hours,
    )
    report["universe"] = args.symbols and "explicit list" or args.universe
    _print_report(report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--strategy",
        required=True,
        help="Strategy NAME or filename stem -- one of the .py files in app/strategies/",
    )
    parser.add_argument("--lookback-days", type=int, default=30, help="Calendar days of 5-minute-bar history to replay (default: 30)")
    parser.add_argument(
        "--cost-bps",
        type=float,
        default=0.0,
        help="Spread/commission charged on each side, in basis points (default: 0, i.e. gross)",
    )
    parser.add_argument(
        "--max-symbols",
        type=int,
        default=100,
        help="Top-N of the live universe by dollar volume, ignored if --symbols is given (default: 100)",
    )
    parser.add_argument(
        "--universe",
        default=universes.LIQUID,
        choices=universes.CHOICES,
        help=(
            "Which symbols to run over, ignored if --symbols is given. "
            "'liquid' is the most-traded names in the configured price band; "
            "'gappers' is names that repeatedly open away from their prior "
            "close, selected from history *before* the test window so the "
            "choice cannot flatter the result (default: liquid)"
        ),
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
