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

The leading untested explanation is the entry, not the catalyst. This enters
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
from app.scanners.metric_validation import expectancy
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

    picks = simulate_from_bars(
        bars,
        settings.scanner_min_dollar_volume,
        args.horizon_days,
        benchmark_returns_by_date(benchmark, args.horizon_days),
        catalysts=catalysts,
    )

    views = []
    for view in bucket_analysis.VIEWS:
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

    return {
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
    parser.add_argument("--primary-wire-only", action="store_true",
                        help="Count only company announcements (GlobeNewsWire/Business Wire/PRNewsWire), "
                             "not third parties writing about the company")
    parser.add_argument("--from-history", action="store_true",
                        help="Use symbols that have actually been ranked rather than the universe's top-N")
    _print_report(asyncio.run(_build(parser.parse_args())))
