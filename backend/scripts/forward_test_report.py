"""Is the 3-8% band still working, on data it was not chosen from?

The scanner's ranked views were measured this month against a random pick
from the same tradable universe and came out indistinguishable from it:
base rate 49.3%, gainers 48.4%, losers 49.4%, most_active 49.0%. One
selection survived a hostile out-of-sample test -- entries between +3% and
+8%, which won 52.0% in-sample and 58.7% out-of-sample at +0.31% alpha, and
was the only result that improved on unseen days rather than decaying. That
became the moderate_movers preset and a tracked live view.

Everything measured so far is a *replay*: today's universe applied to past
dates, with survivorship built in and a stop-loss nobody could actually have
placed. This reads the live record instead -- appearances the engine wrote
down as they happened, checked afterward -- which is the only test the
backtest cannot flatter.

Run from backend/:
    python -m scripts.forward_test_report [--days 30] [--view moderate_movers]

What this is really guarding against is the reason a forward test is worth
running at all: you already want it to work. So the report leads with how
much evidence exists rather than with the win rate, refuses to call a
difference that the sample cannot support, and counts trading days rather
than rows -- everything flagged on one morning shares one market, and
treating those as independent is what turned a 21,303-pick sample into 792
real ones earlier in this project.
"""

import argparse
import asyncio
import math
import sqlite3

from app.core.config import get_settings
from app.scanners.history_store import ScannerHistoryStore

# What the out-of-sample backtest claimed, and what a coin flip on the same
# tradable universe actually paid. The gap between them is the whole question.
BACKTEST_WIN_RATE = 58.7
BACKTEST_ALPHA = 0.31
BASE_RATE = 49.3


def _independent_counts(db_path: str, view: str, days: int) -> tuple[int, int]:
    """(symbol-days, trading days) actually recorded for this view.

    Rows overstate the evidence: one symbol flagged repeatedly in a session is
    one observation, and a whole morning of flagged names shares a single
    market move. Trading days is the conservative unit and the one the
    interval below is built on.
    """
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT COUNT(DISTINCT symbol || trading_date), COUNT(DISTINCT trading_date)
            FROM appearances
            WHERE view = ?
              AND trading_date >= date('now', ?)
            """,
            (view, f"-{days} day"),
        ).fetchone()
    return (row[0] or 0, row[1] or 0)


def _standard_error_pp(n: int) -> float | None:
    """Standard error of a win rate, in percentage points, at n observations.

    Uses 0.5 for the proportion: near a coin flip that is both the maximum
    and very close to the truth here, so the interval is honest rather than
    optimistic.
    """
    if n <= 0:
        return None
    return math.sqrt(0.25 / n) * 100


def _needed_for(delta_pp: float, sigmas: float = 2.0) -> int:
    """Observations needed before a `delta_pp` difference clears `sigmas`."""
    if delta_pp <= 0:
        return 0
    return math.ceil(0.25 / ((delta_pp / 100.0) / sigmas) ** 2)


def _verdict(win_rate: float | None, n: int) -> str:
    if win_rate is None or n <= 0:
        return "no reading yet"
    se = _standard_error_pp(n) or 0.0
    vs_random = win_rate - BASE_RATE
    vs_backtest = win_rate - BACKTEST_WIN_RATE

    if abs(vs_random) < 2 * se:
        return (
            f"cannot distinguish from random ({BASE_RATE}%): "
            f"{vs_random:+.1f}pp against a {2 * se:.1f}pp two-sigma band"
        )
    if abs(vs_backtest) < 2 * se:
        return f"beating random, and consistent with the backtest's {BACKTEST_WIN_RATE}%"
    if vs_backtest < 0:
        return (
            f"beating random but below the backtest: {vs_backtest:+.1f}pp, "
            "which is the decay a replay usually hides"
        )
    return f"above the backtest by {vs_backtest:+.1f}pp -- treat with suspicion, not delight"


async def main(args) -> None:
    settings = get_settings()
    store = ScannerHistoryStore(settings.scanner_history_db_path)
    report = await store.compute_performance(days=args.days, view=args.view)

    symbol_days, trading_days = _independent_counts(
        settings.scanner_history_db_path, args.view, args.days
    )

    print(f"Forward test -- {args.view}, last {args.days} trading days\n")
    print("Evidence so far")
    print(f"   trading days   {trading_days}")
    print(f"   symbol-days    {symbol_days}")
    print("   (rows are not the unit: same-session repeats are one observation,")
    print("    and a whole morning shares one market move)\n")

    rows = [r for r in report.get("summary", []) if r["view"] == args.view]
    if not rows:
        print("No completed observations yet. Give it a few sessions.")
        return

    print(f"{'horizon':<10} {'n':>6} {'win%':>7} {'avg ret':>9} {'avg alpha':>10}")
    for r in rows:
        print(
            f"{r['horizon']:<10} {r['sample_size']:>6} "
            f"{(r['win_rate'] if r['win_rate'] is not None else float('nan')):>6.1f}% "
            f"{(r['avg_return'] if r['avg_return'] is not None else float('nan')):>8.2f}% "
            f"{(r['avg_alpha'] if r['avg_alpha'] is not None else float('nan')):>9.2f}%"
        )

    # "latest" is the closest live analogue of the backtest's entry-to-close
    # hold; the 30m/60m horizons answer a different question.
    # session_close is the only horizon that matches what the backtest
    # measured: entry to the end of that same session.
    at_close = next((r for r in rows if r["horizon"] == "session_close"), None)
    print("\nAgainst the claim")
    print(f"   backtest (out-of-sample):  {BACKTEST_WIN_RATE}% win, {BACKTEST_ALPHA:+.2f}% alpha")
    print(f"   random pick, same universe: {BASE_RATE}% win")
    if at_close:
        se = _standard_error_pp(trading_days or symbol_days)
        print(f"   live (session close):      {at_close['win_rate']}% win, "
              f"{at_close['avg_alpha']:+.2f}% alpha "
              f"[{at_close.get('measured_size')} of {at_close['sample_size']} confirmed]")
        if se is not None:
            print(f"   two-sigma band at this sample: +/-{2 * se:.1f}pp")
        print(f"\n   {_verdict(at_close['win_rate'], trading_days or symbol_days)}")

    need_random = _needed_for(BACKTEST_WIN_RATE - BASE_RATE)
    print("\nHow much more is needed")
    print(f"   to separate {BACKTEST_WIN_RATE}% from {BASE_RATE}% at two sigma: "
          f"~{need_random} independent observations")
    print(f"   have {trading_days} trading days / {symbol_days} symbol-days")
    print("   Read against trading days if the picks move together, symbol-days if")
    print("   they do not. The truth is between, which is why both are printed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--view", default="moderate_movers")
    asyncio.run(main(parser.parse_args()))
