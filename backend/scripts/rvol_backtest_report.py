"""Intraday (5-minute) RVOL replay: re-derives formulas._FADE_RISK_RVOL for
the time-normalized RVOL definition, which is what settings.
scanner_rvol_time_normalized is waiting on. See app.scanners.rvol_backtest
for the method and for why the daily-bar backtest structurally cannot
answer this.

Run from backend/ (after `pip install -e ".[dev]"`):
    python -m scripts.rvol_backtest_report [--lookback-days 30]
        [--horizon-minutes 60] [--max-symbols 100] [--symbols AAPL,TSLA]
        [--thresholds 5,10,15,20,30] [--force-refresh]

READ THE CONTROL FIRST. The report sweeps both RVOL definitions side by
side. The raw column has a known expected answer -- the live baseline says
raw RVOL >15x should show a depressed win rate and a clearly negative
average return. If the raw column doesn't roughly reproduce that, the
normalized column is not evidence of anything and no threshold should be
taken from this run.
"""

import argparse
import asyncio

from app.alpaca.client import AlpacaClients
from app.alpaca.universe import build_universe
from app.core.config import get_settings
from app.scanners import formulas
from app.scanners.rvol_backtest import (
    DEFAULT_THRESHOLDS,
    DEFINITIONS,
    NORMALIZED,
    RAW,
    run_rvol_backtest,
)


def _fmt(value, suffix="") -> str:
    # ASCII rather than an em-dash: this prints to a Windows console whose
    # default cp1252 codepage can't encode one, and a UnicodeEncodeError
    # mid-report would lose the whole run.
    return "-" if value is None else f"{value}{suffix}"


def _symbols_from_history(db_path: str) -> list[str]:
    """Every symbol that has actually appeared in a ranked view.

    This is the right population to derive a fade-risk threshold from, and
    "top N by dollar volume" is the wrong one: the discount only ever
    applies to rows the scanner is ranking, and extreme RVOL is a thin,
    news-driven, small-cap event that the most liquid names in the universe
    essentially never produce. A first run against the top 50 by dollar
    volume reached zero entries above 5x raw RVOL across 21 trading days --
    not evidence that high RVOL is rare, just that it doesn't happen to the
    symbols that were sampled.

    Conditioning on "was ranked" is deliberate, not a leak: it's the same
    conditioning the live baseline in history_store was computed under.
    """
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT DISTINCT symbol FROM appearances ORDER BY symbol").fetchall()
    return [symbol for (symbol,) in rows]


def _print_report(report: dict) -> None:
    horizon = (
        "to session close" if report["horizon_minutes"] is None
        else f"{report['horizon_minutes']}-minute forward"
    )
    print(
        f"Intraday RVOL replay -- {report['lookback_days']} calendar days back, "
        f"{horizon} outcome"
    )
    print(
        f"Universe: {report['symbol_count']} symbols requested, "
        f"{report['symbols_with_bars']} returned 5m bars, "
        f"{report['symbols_with_avg_volume']} had a warmed-up volume baseline"
    )
    print(
        f"Observations: {report['observation_count']} regular-session bars over "
        f"{report['trading_days']} trading days\n"
    )

    if not report["curve_buckets"]:
        print("** Volume profile is EMPTY -- normalized RVOL equals raw RVOL here.")
        print("   Nothing in this report can distinguish the two definitions. **\n")
        return

    print(f"Volume profile ({report['curve_buckets']} buckets) -- share of a typical day done by:")
    for sample in report["fraction_samples"]:
        multiple = sample["rvol_multiple"]
        print(
            f"  {sample['time']}: {sample['fraction'] * 100:5.1f}%"
            + (f"  (raw RVOL understates by {multiple:.1f}x here)" if multiple else "")
        )
    print()

    control = report["baseline_comparison"]
    print(f"** CONTROL -- raw definition at the live threshold ({control['threshold']}x) **")
    measured = control["measured"]
    if measured is None or not measured["sample_size"]:
        print("   No entries at that level in this sample -- the control cannot be checked,")
        print("   so treat the normalized column below as unvalidated.\n")
    else:
        print(
            f"   measured: n={measured['sample_size']}, "
            f"win_rate={_fmt(measured['win_rate'], '%')}, "
            f"avg_return={_fmt(measured['avg_return'], '%')}"
        )
        print(
            f"   live baseline: win_rate={control['baseline_win_rate']}%, "
            f"avg_return={control['baseline_avg_return']}%"
        )
        print("   If these disagree badly, stop here -- see the module docstring.\n")

    min_n = report["min_sample_size"]
    for definition in DEFINITIONS:
        label = "RAW (control -- current live definition)" if definition == RAW else (
            "NORMALIZED (candidate -- what enabling the flag would measure)"
        )
        print(f"{label}:")
        print(
            f"  {'threshold':>10s}  {'n':>6s}  {'symbols':>7s}  {'win_rate':>8s}  "
            f"{'median':>8s}  {'mean':>9s}  {'worst':>9s}  {'best':>9s}"
        )
        for row in report["sweep"]:
            if row["definition"] != definition:
                continue
            flag = "" if row["sufficient_sample"] else f"  ** n<{min_n}, noisy **"
            print(
                f"  {row['threshold']:>9.0f}x  {row['sample_size']:>6d}  "
                f"{row['distinct_symbols']:>7d}  {_fmt(row['win_rate'], '%'):>8s}  "
                f"{_fmt(row['median_return'], '%'):>8s}  {_fmt(row['avg_return'], '%'):>9s}  "
                f"{_fmt(row['min_return'], '%'):>9s}  {_fmt(row['max_return'], '%'):>9s}{flag}"
            )
        print()

    print("Reading this: follow win_rate down each column and find where it starts")
    print(f"degrading. The raw column's degradation should sit near formulas._FADE_RISK_RVOL")
    print(f"({formulas._FADE_RISK_RVOL}x); the normalized column's is the candidate replacement.")
    print()
    print("Use win_rate, then median. Do NOT read `mean` -- these are thin, low-priced")
    print("names measured over a full session, so the return distribution is violently")
    print("fat-tailed and the mean describes its outliers rather than its population")
    print("(compare the worst/best columns against median to see by how much). The live")
    print("baseline this is checked against is itself a mean, which is why the control")
    print("can disagree on avg_return while agreeing on direction.")
    print()
    print("A row flagged noisy, or whose entries come from few distinct symbols, is not")
    print("a result -- it's a coincidence with a sample size attached.")


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

    thresholds = (
        [float(x.strip()) for x in args.thresholds.split(",") if x.strip()]
        if args.thresholds
        else DEFAULT_THRESHOLDS
    )

    report = await run_rvol_backtest(
        clients,
        settings,
        symbols,
        lookback_days=args.lookback_days,
        horizon_minutes=args.horizon_minutes,
        thresholds=thresholds,
        force_refresh=args.force_refresh,
    )
    _print_report(report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lookback-days", type=int, default=30, help="Calendar days of 5m history (default: 30)")
    parser.add_argument(
        "--horizon-minutes",
        type=int,
        default=None,
        help="Minutes forward to measure the outcome; omit to measure to that session's close (default)",
    )
    parser.add_argument(
        "--max-symbols", type=int, default=100, help="Cap on the symbol list; 0 for no cap with --from-history (default: 100)"
    )
    parser.add_argument("--symbols", default=None, help="Comma-separated symbol override, e.g. AAPL,TSLA")
    parser.add_argument(
        "--from-history",
        action="store_true",
        help="Use every symbol that has actually been ranked (scanner_history.sqlite3) instead of the "
        "universe's top-N by dollar volume. Strongly preferred for deriving a fade-risk threshold -- "
        "the most liquid names essentially never reach high RVOL, so the default selection cannot "
        "produce the events being measured.",
    )
    parser.add_argument("--thresholds", default=None, help="Comma-separated RVOL thresholds to sweep")
    parser.add_argument("--force-refresh", action="store_true", help="Ignore the cached bar fetch")
    asyncio.run(_main(parser.parse_args()))
