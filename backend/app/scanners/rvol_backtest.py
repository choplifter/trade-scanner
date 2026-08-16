"""5-minute-resolution RVOL replay -- the tool that can re-derive
formulas._FADE_RISK_RVOL for the *time-normalized* RVOL definition, which
is the one thing standing between settings.scanner_rvol_time_normalized
and being turned on.

Why nothing else can do this. The 15x threshold (and the 25.6% win rate /
-10.38% avg return baseline behind it, see history_store) was calibrated
against un-normalized RVOL: today's cumulative volume over a *full-day*
20-day average. Normalized RVOL divides that denominator by the share of a
typical day normally traded by now (app.market_data.volume_profile), which
at 09:35 is ~2.6% -- so the same tape reads ~21x higher. Switching
definitions without moving the threshold would flag most of the morning as
fade risk. The daily-bar backtest (app.scanners.backtest) cannot help:
a daily bar is a whole session, so the session fraction is 1.0 and the two
definitions are *identical* there, at any lookback. Only an intraday walk
separates them.

Method. Walk each symbol's 5-minute bars, accumulating regular-session
volume from the open, and at every bar compute both RVOL definitions
through the real formulas.rvol (passing session_fraction, or not) -- not a
reimplementation. Then, for each candidate threshold, take the first bar
per (symbol, trading day) where RVOL crosses it and measure the forward
outcome. Sweeping the threshold shows where outcomes actually start to
degrade under each definition.

**Both definitions are swept side by side, and that is the point.** The
raw column is a control with a known answer: the live baseline says raw
RVOL >15x should show a depressed win rate and a clearly negative avg
return. If the raw sweep reproduces roughly that around 15x, the
methodology is sound and the normalized column's comparable point is the
re-derived threshold. If the raw sweep *doesn't* reproduce it, the
normalized number isn't trustworthy either and nothing here should be
shipped -- read the control first, always.

Deliberately out of scope, same as the sibling backtests:
- Cross-sectional ranking. This asks "did this symbol, at this moment,
  with this RVOL, do worse afterwards" -- a per-symbol-moment question,
  like app.scanners.momentum_backtest and unlike the daily backtest's
  "what would have ranked". No top-50 reconstruction, so no feedback loop
  where the discount being derived reshapes the sample it's derived from.
- Point-in-time universe membership (today's universe, past dates).
"""

import logging
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from statistics import mean, median

from app.alpaca.client import AlpacaClients
from app.core.config import Settings
from app.market_data.bars import get_daily_bars_multi
from app.market_data.momentum import is_regular_session_bar, same_trading_day
from app.market_data.volume_profile import (
    DEFAULT_REFERENCE_SYMBOL,
    build_fraction_curve,
    fraction_at,
)
from app.scanners import bucket_analysis, formulas
from app.scanners.bar_cache import DEFAULT_CACHE_DIR, get_cached_5m_bars_multi
from app.services.market_clock import ET

logger = logging.getLogger(__name__)

# Same warmup discipline as app.scanners.backtest: a day only gets an RVOL
# once it has 20 trading days of volume history *behind that date*, computed
# from bars rather than taken from UniverseSymbol.avg_vol_20d (which is
# today's live figure and invalid for a past date).
_WARMUP_TRADING_DAYS = 20
# Calendar padding on the daily-bar fetch so the earliest intraday day still
# has a full 20 trading days behind it -- same generous padding and same
# reason as backtest._WARMUP_CALENDAR_PADDING_DAYS.
_WARMUP_CALENDAR_PADDING_DAYS = 40

# Candidate thresholds to sweep. Deliberately spans both definitions' useful
# ranges in one grid: the raw control's answer should land near 15, while a
# normalized threshold has to be roughly an order of magnitude higher to mean
# the same thing (the denominator shrinks ~21x at 09:35, ~7x at 10:00, ~2.3x
# at noon, ~1x by the close -- so the multiple is not a single constant and
# has to be measured, not derived).
DEFAULT_THRESHOLDS = [1.0, 2.0, 3.0, 5.0, 7.0, 10.0, 15.0, 20.0, 30.0, 50.0, 75.0, 100.0, 150.0]

# Definition keys used throughout, so callers/reports never hardcode strings.
RAW = "raw"
NORMALIZED = "normalized"
DEFINITIONS = (RAW, NORMALIZED)


def trailing_avg_daily_volume(
    daily_bars_by_symbol: dict[str, list],
) -> dict[str, dict[date, float]]:
    """{symbol: {trading_date: mean daily volume over the prior 20 trading
    days}} -- the RVOL denominator, computed per date from that date's own
    trailing window so a replayed day never sees volume from its own future.

    Dates with fewer than _WARMUP_TRADING_DAYS of history behind them are
    simply absent, which is what makes an unwarmed day unmeasurable rather
    than measurable-but-wrong.
    """
    avg_by_symbol: dict[str, dict[date, float]] = {}
    for symbol, bars in daily_bars_by_symbol.items():
        if len(bars) <= _WARMUP_TRADING_DAYS:
            continue
        per_date: dict[date, float] = {}
        for i in range(_WARMUP_TRADING_DAYS, len(bars)):
            window = bars[i - _WARMUP_TRADING_DAYS : i]
            avg = mean(b.volume for b in window)
            if avg > 0:
                per_date[bars[i].timestamp.astimezone(ET).date()] = avg
        avg_by_symbol[symbol] = per_date
    return avg_by_symbol


def observe_rvol(
    bars_by_symbol: dict[str, list],
    avg_vol_by_symbol_date: dict[str, dict[date, float]],
    curve: list[tuple[time, float]],
    horizon_minutes: int | None = None,
) -> list[dict]:
    """Pure, network-free. One record per regular-session 5-minute bar that
    has both a warmed-up RVOL denominator and a measurable forward outcome.

    Volume accumulates across regular-session bars only, resetting each
    trading date -- matching what the live scanner actually divides, which
    is Alpaca's daily bar volume (premarket is not aggregated into it, see
    engine._compute_rows). The session fraction, by contrast, comes from a
    curve built the way VolumeProfileCache builds it live, extended-hours
    buckets included. That asymmetry is inherited from production on
    purpose rather than quietly corrected here: a threshold derived against
    a *fixed* version of the definition wouldn't be the threshold live code
    needs.

    `horizon_minutes=None` measures to that session's last regular-session
    bar, which is the closest intraday analogue of the "latest snapshot"
    end-of-day convention the live baseline was computed under (see
    history_store.compute_ranking_drift). An explicit horizon measures a
    fixed number of minutes forward instead, and drops entries too close to
    the close to have a full window -- same guard, and same reason, as
    momentum_backtest: a clean late-session entry must not be scored
    against a thin after-hours print.
    """
    records: list[dict] = []

    for symbol, bars in bars_by_symbol.items():
        avg_by_date = avg_vol_by_symbol_date.get(symbol)
        if not avg_by_date:
            continue

        session_bars: dict[date, list] = defaultdict(list)
        for bar in bars:
            if is_regular_session_bar(bar):
                session_bars[bar.timestamp.astimezone(ET).date()].append(bar)

        for trading_date, day_bars in session_bars.items():
            avg_vol = avg_by_date.get(trading_date)
            if not avg_vol:
                continue
            day_bars.sort(key=lambda b: b.timestamp)
            last_bar = day_bars[-1]

            cumulative_volume = 0.0
            for i, bar in enumerate(day_bars):
                cumulative_volume += bar.volume
                entry_price = bar.close
                if entry_price <= 0:
                    continue

                exit_bar = _exit_bar(day_bars, i, last_bar, horizon_minutes)
                if exit_bar is None:
                    continue

                fraction = fraction_at(curve, bar.timestamp)
                rvol_raw = formulas.rvol(cumulative_volume, avg_vol)
                rvol_normalized = formulas.rvol(cumulative_volume, avg_vol, fraction)
                if rvol_raw is None or rvol_normalized is None:
                    continue

                records.append(
                    {
                        "symbol": symbol,
                        "trading_date": trading_date.isoformat(),
                        "timestamp": bar.timestamp.isoformat(),
                        "session_fraction": fraction,
                        RAW: rvol_raw,
                        NORMALIZED: rvol_normalized,
                        "pct_change_since_entry": (exit_bar.close - entry_price) / entry_price * 100,
                    }
                )
    return records


def _exit_bar(day_bars: list, index: int, last_bar, horizon_minutes: int | None):
    """The bar an entry at `index` is scored against, or None when this
    entry has no valid forward window left in its own session.
    """
    if horizon_minutes is None:
        # To the close. The final bar of the day can't be an entry -- there
        # would be no forward move at all to measure.
        return last_bar if index < len(day_bars) - 1 else None

    entry_bar = day_bars[index]
    target = entry_bar.timestamp + timedelta(minutes=horizon_minutes)
    for candidate in day_bars[index + 1 :]:
        if candidate.timestamp >= target:
            return candidate if same_trading_day(candidate, entry_bar) else None
    # Ran out of session before the horizon closed -- dropped rather than
    # scored against a shorter window that would quietly mix horizons.
    return None


def first_crossings(records: list[dict], definition: str, threshold: float) -> list[dict]:
    """The first record per (symbol, trading day) whose RVOL under
    `definition` is at or above `threshold`.

    One entry per symbol per day, not one per qualifying bar. Cumulative
    RVOL barely moves between adjacent 5-minute bars, so counting every
    qualifying bar would turn one event into dozens of near-identical,
    highly-correlated samples and inflate every sample size accordingly.
    This also matches how the live store actually counts an appearance --
    UNIQUE(symbol, view, trading_date), see history_store -- so the sweep
    is sampling the same kind of unit the baseline was computed from.
    """
    seen: set[tuple[str, str]] = set()
    crossings: list[dict] = []
    for record in sorted(records, key=lambda r: r["timestamp"]):
        key = (record["symbol"], record["trading_date"])
        if key in seen or record[definition] < threshold:
            continue
        seen.add(key)
        crossings.append(record)
    return crossings


def sweep_thresholds(
    records: list[dict], thresholds: list[float] = DEFAULT_THRESHOLDS
) -> list[dict]:
    """One row per (definition, threshold): how did entries taken the first
    time RVOL crossed that level actually do?

    Read it as a curve, not a set of independent tests. The question is
    where win rate and average return start to degrade as the threshold
    rises -- a single row in isolation says very little, and the raw column
    exists to be checked against the known live baseline before the
    normalized column is believed at all (see the module docstring).
    """
    rows: list[dict] = []
    for definition in DEFINITIONS:
        for threshold in thresholds:
            crossings = first_crossings(records, definition, threshold)
            stats = bucket_analysis.bucket_stats(crossings)
            returns = [c["pct_change_since_entry"] for c in crossings]
            rows.append(
                {
                    "definition": definition,
                    "threshold": threshold,
                    **stats,
                    # bucket_stats' avg_return is a plain mean, which is not
                    # a usable statistic on this population: these are thin,
                    # low-priced names measured over a whole session, so the
                    # return distribution is violently fat-tailed. A single
                    # +215% observation was measured moving an 85-sample mean
                    # by ~2.5pp on its own. The median says what a typical
                    # entry did; read it, not avg_return, when deciding where
                    # outcomes actually degrade -- and read win_rate first,
                    # which is outlier-immune by construction.
                    "median_return": round(median(returns), 2) if returns else None,
                    # How much of the mean is one or two prints. Large gap
                    # between mean and median = the mean is describing
                    # outliers, not the population.
                    "max_return": round(max(returns), 2) if returns else None,
                    "min_return": round(min(returns), 2) if returns else None,
                    "sufficient_sample": stats["sample_size"] >= bucket_analysis.MIN_SAMPLE_SIZE,
                    # How many distinct symbols the entries came from -- a
                    # high threshold can reach a decent sample size while
                    # being a handful of names repeating, which is not the
                    # same evidence at all.
                    "distinct_symbols": len({c["symbol"] for c in crossings}),
                }
            )
    return rows


def baseline_comparison(rows: list[dict]) -> dict:
    """The raw-definition row at exactly formulas._FADE_RISK_RVOL, next to
    the live baseline that threshold was set from -- the control that says
    whether any of the rest of this is trustworthy.

    Not a pass/fail verdict: how close counts as "reproduced" is a judgment
    call, and the two samples differ in resolution, horizon and period, so
    an exact match was never the expectation. Same reasoning as
    compute_ranking_drift reporting raw numbers rather than a threshold.
    """
    control = next(
        (r for r in rows if r["definition"] == RAW and r["threshold"] == formulas._FADE_RISK_RVOL),
        None,
    )
    return {
        "threshold": formulas._FADE_RISK_RVOL,
        "measured": control,
        "baseline_win_rate": 25.6,
        "baseline_avg_return": -10.38,
    }


async def run_rvol_backtest(
    clients: AlpacaClients,
    settings: Settings,
    symbols: list[str],
    lookback_days: int = 30,
    horizon_minutes: int | None = None,
    thresholds: list[float] = DEFAULT_THRESHOLDS,
    reference_symbol: str = DEFAULT_REFERENCE_SYMBOL,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    force_refresh: bool = False,
    max_age_hours: float = 12.0,
) -> dict:
    """Fetch/orchestration wrapper, mirroring run_momentum_backtest's shape.

    Three fetches: 5-minute bars for the symbols (disk-cached), 5-minute
    bars for the volume-profile reference symbol, and daily bars for the
    trailing-volume denominator. The daily fetch is what keeps the RVOL
    denominator point-in-time -- deriving it from the 5-minute bars instead
    would need 20 extra trading days of intraday data for warmup alone.
    """
    bars_by_symbol = await get_cached_5m_bars_multi(
        clients,
        symbols,
        lookback_days,
        cache_dir=cache_dir,
        force_refresh=force_refresh,
        max_age_hours=max_age_hours,
    )

    # Built exactly the way VolumeProfileCache builds it live (all returned
    # bars, no session filtering) so the fraction this replay divides by is
    # the fraction production divides by.
    reference_bars = await get_cached_5m_bars_multi(
        clients,
        [reference_symbol],
        lookback_days,
        cache_dir=cache_dir,
        force_refresh=force_refresh,
        max_age_hours=max_age_hours,
    )
    curve = build_fraction_curve(reference_bars.get(reference_symbol) or [])
    if not curve:
        logger.warning(
            "Volume profile came back empty for %s -- normalized RVOL would equal raw, aborting",
            reference_symbol,
        )

    daily_bars = await get_daily_bars_multi(
        clients, symbols, lookback_days=lookback_days + _WARMUP_CALENDAR_PADDING_DAYS
    )
    avg_vol_by_symbol_date = trailing_avg_daily_volume(daily_bars)

    records = observe_rvol(bars_by_symbol, avg_vol_by_symbol_date, curve, horizon_minutes)
    rows = sweep_thresholds(records, thresholds)

    return {
        "symbol_count": len(symbols),
        "symbols_with_bars": len(bars_by_symbol),
        "symbols_with_avg_volume": len(avg_vol_by_symbol_date),
        "lookback_days": lookback_days,
        "horizon_minutes": horizon_minutes,
        "curve_buckets": len(curve),
        "observation_count": len(records),
        "trading_days": len({r["trading_date"] for r in records}),
        "sweep": rows,
        "baseline_comparison": baseline_comparison(rows),
        "min_sample_size": bucket_analysis.MIN_SAMPLE_SIZE,
        "fraction_samples": _fraction_samples(curve),
    }


def _fraction_samples(curve: list[tuple[time, float]]) -> list[dict]:
    """The curve read at a few reference times, so a report can show what
    the normalization is actually doing before anyone reads a threshold off
    it. These are the same checkpoints ea0b637 quoted from SPY's real
    curve, which makes a drifted or malformed curve obvious at a glance.
    """
    samples = []
    for label, at in (
        ("09:35", time(9, 35)),
        ("10:00", time(10, 0)),
        ("12:00", time(12, 0)),
        ("15:55", time(15, 55)),
    ):
        moment = datetime.combine(date(2026, 1, 5), at, tzinfo=ET)
        fraction = fraction_at(curve, moment)
        samples.append(
            {
                "time": label,
                "fraction": fraction,
                # What the denominator shrinking by that much does to RVOL.
                "rvol_multiple": (1 / fraction) if fraction else None,
            }
        )
    return samples
