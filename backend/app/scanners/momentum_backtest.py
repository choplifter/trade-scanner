"""Minute-resolution historical backtest for the live momentum alarm
(app.scanners.formulas.is_momentum_alert, long setups only) -- replays
historical minute bars through the *actual* production functions
(pct_change_over_window, aggregate_last_n_minutes, is_shaved_top,
SessionVwapState, is_momentum_alert itself, not reimplementations) to
check whether requiring the shaved-top/green/above-VWAP confirmation
(checked on a rolled-up CONFIRMATION_CANDLE_MINUTES-minute candle, not a
single noisy 1-minute print -- same as the live cache) actually improves
on the 15m% threshold alone, by comparing two conditions side by side:
"15m% crossed the threshold" vs. "15m% crossed the threshold AND the
candle confirms it." The confirmation checks are only ever evaluated
*after* the 15m% threshold is already reached -- never on their own --
both because that's the actual question being asked (is confirmation
useful once momentum's already there, not whether it predicts anything by
itself) and because it means skipping those checks entirely for the vast
majority of bars that never cross the threshold at all.

Long side only, by design -- matches the live alarm itself, which no
longer evaluates downward moves at all (a green-candle-and-above-VWAP
confirmation is fundamentally long-only, see formulas.is_momentum_alert's
docstring). Only pct_change_last_15m > 0 bars are ever considered.

Unlike the daily-bar backtest (app.scanners.backtest), there's no cross-
sectional ranking question here (no engine._rank_gainers/_rank_losers
equivalent) -- just "did this specific alert condition, at this specific
moment, predict a better outcome." Uses the same bucket_analysis.py
methodology as every other backtest/drift report in this app.
"""

from pathlib import Path

from app.alpaca.client import AlpacaClients
from app.core.config import Settings
from app.market_data.bars import aggregate_last_n_minutes
from app.market_data.candle_shape import is_shaved_top
from app.market_data.momentum import MOMENTUM_WINDOW, pct_change_over_window
from app.market_data.vwap import SessionVwapState
from app.scanners import bucket_analysis, formulas
from app.scanners.bar_cache import DEFAULT_CACHE_DIR, get_cached_minute_bars_multi
from app.scanners.momentum_cache import CONFIRMATION_CANDLE_MINUTES

# How many trailing bars to hand pct_change_over_window/aggregate_last_n_minutes
# at each step -- comfortably more than either window needs, without
# re-slicing the full history-so-far on every single bar (which would be
# O(n^2) over a multi-week walk).
_TRAILING_WINDOW_BARS = 30


def simulate_momentum_alerts(
    bars_by_symbol: dict[str, list],
    threshold: float,
    horizon_minutes: int = 15,
) -> list[dict]:
    """Pure, network-free. Walks each symbol's minute bars in order,
    computing the same pct_change_last_15m / is_shaved_top / is_green /
    is_above_vwap inputs the live MomentumCache computes, then evaluates
    two conditions at each bar: "threshold_only" (just the 15m% magnitude
    check) and "full_alert" (formulas.is_momentum_alert itself -- the
    actual live check, not an approximation of it). A downward 15m% move
    never satisfies threshold_only here -- long side only, see module
    docstring.

    VWAP is tracked with one SessionVwapState per symbol, fed every bar in
    order (not just the ones that cross threshold) -- it's a running
    cumulative total, so skipping bars would corrupt it. Resets on its own
    on every calendar-date change (see SessionVwapState), so a multi-week
    bar series naturally produces the correct *that day's* VWAP at each
    point without this function needing to track session boundaries itself.

    Only the *first* bar of a consecutive run where a given condition
    holds counts as a pick for that condition (a rising-edge trigger) --
    a real move often keeps a condition true for several consecutive
    minutes, and counting every one of those as a separate, independent
    sample would pseudo-replicate a single event into many highly-
    correlated ones, inflating the apparent sample size. This mirrors the
    live frontend's own semantics too (useAlarms.ts tracks "first seen,"
    not every tick). The two conditions are tracked independently, so a
    "full_alert" pick can fire more than once inside one ongoing
    "threshold_only" streak if the confirmation drops and returns.

    horizon_minutes is a bar-count offset (looks `horizon_minutes` bars
    ahead, not bars exactly `horizon_minutes` wall-clock minutes later --
    minute bars are sparse during quiet stretches, same simplification
    app.scanners.backtest's horizon_days already makes for daily bars).
    """
    picks: list[dict] = []

    for symbol, bars in bars_by_symbol.items():
        threshold_only_active = False
        full_alert_active = False
        vwap_state = SessionVwapState(symbol=symbol)
        for i in range(len(bars) - horizon_minutes):
            bar = bars[i]
            window = bars[max(0, i - _TRAILING_WINDOW_BARS) : i + 1]
            pct_15m = pct_change_over_window(window, MOMENTUM_WINDOW)
            vwap = vwap_state.update(
                timestamp=bar.timestamp,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                bar_vwap=getattr(bar, "vwap", None),
            )

            # Long side only: a downward move (or no move at all) never
            # counts as momentum here, regardless of magnitude. Requiring
            # pct_15m > 0 explicitly (not just >= threshold) keeps this
            # correct even in the pathological case of a zero/negative
            # threshold, where ">= threshold" alone wouldn't guarantee an
            # upward move.
            threshold_only = pct_15m is not None and pct_15m > 0 and pct_15m >= threshold
            # Confirmation is only computed once momentum's actually been
            # reached -- is_momentum_alert would short-circuit on the same
            # threshold check anyway, so this produces identical results
            # while skipping the extra work for the vast majority of bars
            # that never cross the threshold at all.
            full_alert = False
            if threshold_only:
                # window already covers comfortably more than
                # CONFIRMATION_CANDLE_MINUTES (see _TRAILING_WINDOW_BARS),
                # and its last bar is bars[i] -- same "as of right now"
                # candle the live cache would see.
                candle = aggregate_last_n_minutes(window, CONFIRMATION_CANDLE_MINUTES)
                shaved_top = is_shaved_top(candle.open, candle.high, candle.low, candle.close)
                is_green = candle.close > candle.open
                is_above_vwap = vwap is not None and candle.close > vwap
                full_alert = formulas.is_momentum_alert(pct_15m, shaved_top, is_green, is_above_vwap, threshold)

            if (threshold_only and not threshold_only_active) or (full_alert and not full_alert_active):
                entry_price = bar.close
                exit_price = bars[i + horizon_minutes].close
                if entry_price > 0:
                    outcome = (exit_price - entry_price) / entry_price * 100
                    if threshold_only and not threshold_only_active:
                        picks.append(
                            {
                                "symbol": symbol,
                                "timestamp": bar.timestamp.isoformat(),
                                "kind": "threshold_only",
                                "pct_change_since_entry": outcome,
                            }
                        )
                    if full_alert and not full_alert_active:
                        picks.append(
                            {
                                "symbol": symbol,
                                "timestamp": bar.timestamp.isoformat(),
                                "kind": "full_alert",
                                "pct_change_since_entry": outcome,
                            }
                        )

            threshold_only_active = threshold_only
            full_alert_active = full_alert

    return picks


async def run_momentum_backtest(
    clients: AlpacaClients,
    settings: Settings,
    symbols: list[str],
    lookback_days: int = 30,
    horizon_minutes: int = 15,
    threshold: float | None = None,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    force_refresh: bool = False,
    max_age_hours: float = 12.0,
) -> dict:
    """Fetch (via the disk cache)/orchestration wrapper, mirroring
    app.scanners.backtest.run_backtest's shape at minute resolution.
    `threshold` overrides settings.alarm_momentum_pct_threshold for just
    this run -- lets a caller try several threshold values against the
    same cached bars without touching backend/.env each time.
    """
    bars_by_symbol = await get_cached_minute_bars_multi(
        clients,
        symbols,
        lookback_days,
        cache_dir=cache_dir,
        force_refresh=force_refresh,
        max_age_hours=max_age_hours,
    )

    if threshold is None:
        threshold = settings.alarm_momentum_pct_threshold
    picks = simulate_momentum_alerts(bars_by_symbol, threshold, horizon_minutes)

    comparison = {
        "threshold_only": bucket_analysis.bucket_stats([p for p in picks if p["kind"] == "threshold_only"]),
        "full_alert": bucket_analysis.bucket_stats([p for p in picks if p["kind"] == "full_alert"]),
    }

    return {
        "symbol_count": len(symbols),
        "symbols_with_bars": len(bars_by_symbol),
        "lookback_days": lookback_days,
        "horizon_minutes": horizon_minutes,
        "threshold": threshold,
        "sample_size": len(picks),
        "comparison": comparison,
        "min_sample_size": bucket_analysis.MIN_SAMPLE_SIZE,
        # Raw per-pick rows, alongside the comparison stats above -- callers
        # that want to list individual wins/losses (see the Dash backtest
        # page) can use this directly instead of re-deriving it from bars.
        "picks": picks,
    }


def sweep_momentum_params(
    bars_by_symbol: dict[str, list],
    thresholds: list[float],
    horizons_minutes: list[int],
) -> list[dict]:
    """Pure, network-free -- runs simulate_momentum_alerts once per
    (threshold, horizon) combination against the SAME already-fetched
    bars, so exploring a parameter grid costs one fetch (see
    get_cached_minute_bars_multi) plus len(thresholds) * len(horizons)
    cheap in-memory simulations, not one fetch per combination. Returns
    one row per combination, in grid order (thresholds outer loop,
    horizons inner) -- not ranked; see
    backend/scripts/momentum_param_sweep.py for how results get sorted
    for display (sufficient-sample combinations first, by avg return).
    """
    results: list[dict] = []
    for threshold in thresholds:
        for horizon in horizons_minutes:
            picks = simulate_momentum_alerts(bars_by_symbol, threshold, horizon)
            results.append(
                {
                    "threshold": threshold,
                    "horizon_minutes": horizon,
                    "threshold_only": bucket_analysis.bucket_stats(
                        [p for p in picks if p["kind"] == "threshold_only"]
                    ),
                    "full_alert": bucket_analysis.bucket_stats([p for p in picks if p["kind"] == "full_alert"]),
                }
            )
    return results
