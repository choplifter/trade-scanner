"""Does a news catalyst actually predict a better outcome?

The 1.15x boost in formulas._CATALYST_BOOST rests on +9.1pp measured across
three trading days of live scanner_history. This measures the same thing over
months, using dated FMP news (app.scanners.news_history) that only became
available once the news fallback was built -- every previous run of the daily
backtest ranked with has_headline=False and said so.

Run from backend/ (after `pip install -e ".[dev]"`):
    python -m scripts.catalyst_backtest_report [--lookback-days 120]
        [--max-symbols 150] [--horizon-days 1] [--from-history]

Fetches one request per symbol, disk-cached, so a repeat run over the same
window and symbols costs nothing. The first run over 150 symbols is 150
requests.

What the measurement has survived so far, since "the flag must be wrong" is
the natural first reaction to a negative result:

  - not a loose flag: --primary-wire-only halves coverage and makes gainers
    worse (-4.0pp rather than -3.0pp);
  - not move size: catalyst days genuinely do move more (mean |gap| 3.52% vs
    2.91%, RVOL 1.19 vs 1.02), but stratifying by gap bucket leaves the
    effect intact at -1.5 / -3.4 / -1.0 / -3.6pp;
  - not RVOL: same within RVOL buckets, -1.7 / -1.2 / -4.3pp;
  - not the symbol population: --from-history (the small caps the boost was
    originally derived from) gives -3.0pp, the same as the top-120 by dollar
    volume.

That explanation has now been tested, and it holds: the entry, not the
catalyst, is what decides the sign. Entering at the close of a news day and
holding overnight gives -3.0pp; entering intraday and holding to that
session's close (--intraday) gives **+1.7pp win rate and +1.5pp alpha**. Both
are true, of different questions -- and the original +9.1pp asked the second
one, since the live drift report reads each appearance's latest same-day
snapshot.

So the boost's *direction* survives on the measurement it was derived from.
Its magnitude looks refuted too -- +1.7pp is a long way from +9.1pp, which
seems to say 1.15 should fall to about 1.03. Run --solve-multiplier: it
doesn't, and the reasoning behind 1.03 is a units error.

The boost rescales ranking *magnitude*, so it's an exchange rate between
"has a catalyst" and "moved further", and the conversion factor is the slope
of outcome against gap -- not the size of the edge. That slope is shallow
(+2.9pp of win rate per *doubling* of gap, and only positive below ~8% gap),
so even the reduced edge buys slightly over one doubling: m ~= 2.1 by win
rate, 1.2 by alpha. Both sit at or above the shipped 1.15.

What actually limits the answer is sample, not effect size. The intraday
replay emits one pick per qualifying bar, so 11,405 catalyst picks come from
210 symbol-days -- and bootstrapping over symbol-days puts the 90% interval
at 0.50 .. 16.3, with 20% of draws implying no boost at all. 1.15 is inside
that and conservative against both point estimates, so it stays.

The original leading explanation, for the record: This enters
at the *close* of the news day, by which point a catalyst is priced -- what's
left is the overshoot reverting. The payoff ratio supports that: catalyst
gainers run 0.94 against 1.19 without, so their wins are smaller relative to
their losses, which is what buying an exhausted move looks like. Note also
that the original +9.1pp came from each appearance's latest same-day snapshot
-- "does it keep going today" -- while this asks "does it continue tomorrow".
Those are different questions and can honestly have different answers.

Read the per-view split, never the pooled number. "Win" means the opposite
thing on losers -- a flagged loser rising is the move reversing -- and it was
exactly that pooling which made the original catalyst figure look like +8.5pp
"across all three views" when the per-view truth was +9.1pp on gainers and
inside a standard error of zero elsewhere.
"""

import argparse
import asyncio
import sqlite3
from datetime import timedelta

import httpx

from app.alpaca.client import AlpacaClients
from app.alpaca.universe import build_universe
from app.core.config import get_settings
from app.market_data.bars import get_daily_bars_multi
from app.scanners import bucket_analysis
from app.scanners.backtest import (
    _BENCHMARK_SYMBOL,
    _FETCH_BATCH_SIZE,
    _WARMUP_CALENDAR_PADDING_DAYS,
    benchmark_returns_by_date,
    simulate_from_bars,
)
from app.scanners import screener
from app.scanners.intraday_backtest_runner import run_intraday_backtest
from app.scanners import formulas
from app.scanners.metric_validation import expectancy, implied_multiplier
from app.scanners.news_history import catalyst_days, fetch_symbol_news
from app.services.market_clock import ET


def _fmt(stats: dict | None) -> str:
    if not stats:
        return "(no picks)"
    payoff = "-" if stats["payoff_ratio"] is None else f"{stats['payoff_ratio']}"
    return (
        f"n={stats['sample_size']:5d}  win={stats['win_rate']:5.1f}%  "
        f"median={stats['median']:+6.2f}%  mean={stats['mean']:+7.2f}%  payoff={payoff}"
    )


def _print_report(report: dict) -> None:
    print(
        f"Catalyst backtest -- {report['lookback_days']} days, "
        f"{report['symbols_with_bars']}/{report['symbol_count']} symbols, "
        f"{report['horizon_days']}-day forward return"
    )
    print(
        f"Catalyst coverage: {report['symbol_days_with_catalyst']} symbol-days flagged "
        f"across {report['symbols_with_any_catalyst']} symbols\n"
    )
    print(f"Baseline to beat: formulas._CATALYST_BOOST assumes +{report['baseline_delta_pp']}pp "
          f"on gainers (measured over 3 trading days of live data)\n")

    for row in report["views"]:
        print(f"{row['view']}:")
        print(f"   with catalyst    {_fmt(row['with_catalyst'])}")
        print(f"   without catalyst {_fmt(row['without_catalyst'])}")
        if row["win_rate_delta_pp"] is None:
            print("   delta: not measurable (one side empty)")
        else:
            noisy = "" if row["sufficient_sample"] else f"   ** below n={bucket_analysis.MIN_SAMPLE_SIZE} floor **"
            print(f"   delta: {row['win_rate_delta_pp']:+.1f}pp win rate, "
                  f"{row['alpha_delta_pp']:+.1f}pp beating {report['benchmark_symbol']}{noisy}")
        print()

    print("A positive delta on gainers supports the boost; near zero or negative says the")
    print("1.15x is unearned. Read alpha alongside win rate -- on a green day everything")
    print("closes positive, so only the benchmark-relative split isolates the catalyst.")


def _print_multiplier(result: dict | None) -> None:
    if not result:
        print("\nNot enough picks on both sides to solve for a multiplier.")
        return
    print("\n" + "=" * 72)
    print("What multiplier does that edge justify?")
    print("=" * 72)
    print("formulas._CATALYST_BOOST rescales ranking *magnitude*, so a boost of m ranks a")
    print("flagged name where an unflagged name with m-times the gap sits. The multiplier is")
    print("therefore edge / (slope of outcome against gap) -- NOT the edge itself. Rescaling")
    print("1.15 by the ratio of effect sizes is a units error.\n")

    print("Outcome by gap band, unflagged picks (the yardstick being priced against):")
    print("   gap band          n     win%     mean alpha")
    for band in result["bands"]:
        print("   %5.1f-%-6.1f %7d   %5.1f%%   %+9.3f%%"
              % (band["low"], band["high"], band["sample_size"], band["win_rate"], band["mean_alpha"]))
    print(f"   ({result['picks_excluded_above_limit']} picks above "
          f"{result['gap_limit_pct']:.0f}% excluded -- the relation inverts there)\n")

    for name in ("win_rate", "alpha"):
        m = result[f"{name}_multiplier"]
        print("   by %-8s  slope %+7.3fpp/doubling   edge %+6.2fpp   ->  %s"
              % (name, result[f"{name}_slope_pp_per_doubling"], result[f"{name}_edge_pp"],
                 "no multiplier (slope ~flat)" if m is None else f"m = {m:.2f}"))

    boot = result.get("bootstrap")
    if boot:
        print(f"\nBootstrapped over {result['symbol_days']} symbol-days "
              f"({result['flagged_symbol_days']} flagged), not over picks -- every 5-minute bar")
        print("of a day repeats the same catalyst flag, so pick-level resampling would report")
        print("an interval several times too tight.")
        print(f"   90% interval: {boot['p05']:.2f} .. {boot['p95']:.2f}   (median {boot['median']:.2f})")
        print(f"   draws implying no boost at all: {boot['share_below_one_pct']:.0f}%")
    print(f"\nCurrently shipped: {formulas._CATALYST_BOOST}")


async def _build(args) -> dict:
    settings = get_settings()
    clients = AlpacaClients(settings)

    if args.from_history:
        with sqlite3.connect(settings.scanner_history_db_path) as conn:
            symbols = [s for (s,) in conn.execute("SELECT DISTINCT symbol FROM appearances ORDER BY symbol")]
    else:
        universe = await build_universe(clients, settings)
        ranked = sorted(universe.values(), key=lambda u: u.avg_dollar_vol_20d, reverse=True)
        symbols = [u.symbol for u in ranked]
    symbols = symbols[: args.max_symbols] if args.max_symbols else symbols

    fetch_lookback = args.lookback_days + _WARMUP_CALENDAR_PADDING_DAYS
    bars: dict[str, list] = {}
    for i in range(0, len(symbols), _FETCH_BATCH_SIZE):
        bars.update(await get_daily_bars_multi(clients, symbols[i : i + _FETCH_BATCH_SIZE], lookback_days=fetch_lookback))
    benchmark = (await get_daily_bars_multi(clients, [_BENCHMARK_SYMBOL], lookback_days=fetch_lookback)).get(_BENCHMARK_SYMBOL) or []

    sessions = sorted({b.timestamp.astimezone(ET).date() for series in bars.values() for b in series})
    news_from = sessions[0] - timedelta(days=3) if sessions else None
    news_to = sessions[-1] if sessions else None
    print(f"Fetching news for {len(bars)} symbols over {news_from} .. {news_to} (cached after first run)")

    catalysts: dict[str, dict] = {}
    semaphore = asyncio.Semaphore(4)

    async def one(client, symbol):
        async with semaphore:
            items = await fetch_symbol_news(client, settings.fmp_api_key, symbol, news_from, news_to)
        found = catalyst_days(items, sessions, args.primary_wire_only)
        if found:
            catalysts[symbol] = found

    async with httpx.AsyncClient(timeout=40) as client:
        await asyncio.gather(*(one(client, s) for s in bars))

    if args.intraday:
        # Entry intraday, held to that session's close -- the same question
        # the original +9.1pp asked (each appearance's latest same-day
        # snapshot), which a close-entry daily replay structurally cannot.
        result = await run_intraday_backtest(
            clients, settings, list(bars), screener.Screen(limit=50),
            lookback_days=min(args.lookback_days, 45), catalysts=catalysts,
        )
        picks = result["picks_all"]
    else:
        picks = simulate_from_bars(
            bars,
            settings.scanner_min_dollar_volume,
            args.horizon_days,
            benchmark_returns_by_date(benchmark, args.horizon_days),
            catalysts=catalysts,
        )

    # The intraday replay produces one "screen" view rather than the three
    # named ones -- looping VIEWS there finds nothing at all.
    view_names = ("screen",) if args.intraday else bucket_analysis.VIEWS
    views = []
    for view in view_names:
        vp = [p for p in picks if p["view"] == view]
        with_c = [p for p in vp if p["has_catalyst"]]
        without = [p for p in vp if not p["has_catalyst"]]
        w, wo = expectancy(with_c), expectancy(without)
        wa = expectancy(with_c, key="alpha_vs_benchmark")
        woa = expectancy(without, key="alpha_vs_benchmark")
        views.append(
            {
                "view": view,
                "with_catalyst": w,
                "without_catalyst": wo,
                "win_rate_delta_pp": round(w["win_rate"] - wo["win_rate"], 1) if w and wo else None,
                "alpha_delta_pp": round(wa["win_rate"] - woa["win_rate"], 1) if wa and woa else None,
                "sufficient_sample": bool(w and wo and min(w["sample_size"], wo["sample_size"]) >= bucket_analysis.MIN_SAMPLE_SIZE),
            }
        )

    # Only solvable on the intraday picks: it needs each pick's own gap, and
    # a slope fit through picks whose edge is negative would price a boost
    # below 1 for a question the ranking doesn't ask.
    multiplier = (
        implied_multiplier(picks) if getattr(args, "solve_multiplier", False) else None
    )

    return {
        "multiplier": multiplier,
        "lookback_days": args.lookback_days,
        "horizon_days": args.horizon_days,
        "symbol_count": len(symbols),
        "symbols_with_bars": len(bars),
        "benchmark_symbol": _BENCHMARK_SYMBOL,
        "baseline_delta_pp": 9.1,
        "symbols_with_any_catalyst": len(catalysts),
        "symbol_days_with_catalyst": sum(len(v) for v in catalysts.values()),
        "views": views,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lookback-days", type=int, default=120)
    parser.add_argument("--horizon-days", type=int, default=1)
    parser.add_argument("--max-symbols", type=int, default=150)
    parser.add_argument("--solve-multiplier", action="store_true",
                        help="Solve for what formulas._CATALYST_BOOST should be, rather than "
                             "only reporting the edge. Implies --intraday")
    parser.add_argument("--intraday", action="store_true",
                        help="Enter intraday and hold to that session's close, rather than "
                             "entering at the close and holding overnight")
    parser.add_argument("--primary-wire-only", action="store_true",
                        help="Count only company announcements (GlobeNewsWire/Business Wire/PRNewsWire), "
                             "not third parties writing about the company")
    parser.add_argument("--from-history", action="store_true",
                        help="Use symbols that have actually been ranked rather than the universe's top-N")
    args = parser.parse_args()
    if args.solve_multiplier:
        # The multiplier can only be solved where the edge is real, and the
        # edge is only positive on the intraday entry -- solving it against
        # the overnight measurement would price a boost for a question the
        # ranking doesn't ask.
        args.intraday = True
    report = asyncio.run(_build(args))
    _print_report(report)
    if args.solve_multiplier:
        _print_multiplier(report["multiplier"])
