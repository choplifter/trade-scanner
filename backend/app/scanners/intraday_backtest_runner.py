"""Fetch/orchestration for the intraday screen backtest.

Separate from the pure simulation (app.scanners.intraday_backtest) for the
same reason run_backtest is separate from simulate_from_bars: the walking and
bucketing are testable offline, and only this layer needs credentials.

Three fetches, all reusing what already exists: disk-cached 5-minute bars
(bar_cache, the same cache the momentum and RVOL backtests use, so a repeat
run over the same symbols and lookback costs nothing), daily bars for the
point-in-time trailing-volume baseline and previous closes, and the reference
symbol's 5-minute bars for the volume-profile curve.
"""

import logging
from datetime import timedelta
from pathlib import Path

from app.alpaca.client import AlpacaClients
from app.core.config import Settings
from app.market_data.bars import get_daily_bars_multi
from app.market_data.volume_profile import DEFAULT_REFERENCE_SYMBOL, build_fraction_curve
from app.scanners import bucket_analysis, formulas
from app.scanners.backtest import (
    _BENCHMARK_SYMBOL,
    catalyst_split,
    _WARMUP_CALENDAR_PADDING_DAYS,
    alpha_by_view,
    fade_risk_by_view,
)
from app.scanners.bar_cache import DEFAULT_CACHE_DIR, get_cached_5m_bars_multi
from app.scanners.intraday_backtest import (
    benchmark_to_close,
    previous_closes,
    sample_picks,
    replication_factor,
    simulate_intraday_screen,
)
from app.scanners.exit_rules import Exit, ExitRule, expectancy
from app.scanners.rvol_backtest import trailing_avg_daily_volume

logger = logging.getLogger(__name__)

_VIEWS = ("screen",)


async def run_intraday_backtest(
    clients: AlpacaClients,
    settings: Settings,
    symbols: list[str],
    screen,
    lookback_days: int = 30,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    force_refresh: bool = False,
    fundamentals: dict | None = None,
    catalysts: dict | None = None,
    exit_rule: ExitRule | None = None,
) -> dict:
    """Replay `screen` at 5-minute resolution.

    Without an `exit_rule`, each pick is held to its session close. With one,
    each becomes a trade with a stop and a target, and the report gains an
    `expectancy` block measured in R -- which is the only figure here that
    answers "would this have made money" rather than "did price drift up".

    lookback_days defaults far shorter than the daily backtest's 180: a
    session of 5-minute bars is ~78 rows per symbol per day, so the fetch and
    the walk both scale roughly 78x. 30 days over a few hundred symbols is
    already a substantial pull.
    """
    bars_by_symbol = await get_cached_5m_bars_multi(
        clients, symbols, lookback_days, cache_dir=cache_dir, force_refresh=force_refresh
    )
    reference = await get_cached_5m_bars_multi(
        clients, [DEFAULT_REFERENCE_SYMBOL], lookback_days, cache_dir=cache_dir, force_refresh=force_refresh
    )
    curve = build_fraction_curve(reference.get(DEFAULT_REFERENCE_SYMBOL) or [])
    if not curve:
        logger.warning(
            "Volume profile empty -- rvol_1h will be None and any filter on it matches nothing"
        )

    daily = await get_daily_bars_multi(
        clients, symbols, lookback_days=lookback_days + _WARMUP_CALENDAR_PADDING_DAYS
    )
    # The benchmark's own 5-minute bars, so alpha spans the same minutes each
    # pick does. DEFAULT_REFERENCE_SYMBOL and _BENCHMARK_SYMBOL are both SPY,
    # so this is normally the fetch already made above rather than a new one.
    benchmark_intraday = (
        reference.get(_BENCHMARK_SYMBOL)
        if _BENCHMARK_SYMBOL == DEFAULT_REFERENCE_SYMBOL
        else (
            await get_cached_5m_bars_multi(
                clients, [_BENCHMARK_SYMBOL], lookback_days, cache_dir=cache_dir
            )
        ).get(_BENCHMARK_SYMBOL)
    ) or []

    window = timedelta(minutes=settings.scanner_volume_surge_window_minutes)
    picks = simulate_intraday_screen(
        bars_by_symbol,
        trailing_avg_daily_volume(daily),
        previous_closes(daily),
        curve,
        screen,
        settings.scanner_min_dollar_volume,
        window,
        benchmark_to_close(benchmark_intraday),
        fundamentals,
        catalysts,
        exit_rule,
    )

    return {
        "resolution": "intraday",
        "screened": True,
        "views": list(_VIEWS),
        "symbol_count": len(symbols),
        "symbols_with_bars": len(bars_by_symbol),
        "lookback_days": lookback_days,
        # Not a forward window: every pick is held to that session's close.
        "horizon_days": 0,
        "window_minutes": settings.scanner_volume_surge_window_minutes,
        "curve_buckets": len(curve),
        "benchmark_symbol": _BENCHMARK_SYMBOL,
        "min_sample_size": bucket_analysis.MIN_SAMPLE_SIZE,
        "sample_size": len(picks),
        # Every qualifying bar is a pick, so sample_size overstates the
        # independent evidence -- this is what makes that visible instead of
        # leaving a big n to be read at face value.
        "replication": replication_factor(picks),
        # Present only when a rule was supplied -- an empty block would read
        # as "measured, and it was nothing".
        "expectancy": (
            expectancy(
                [
                    Exit(price=0.0, reason=p["exit_reason"], r_multiple=p["r_multiple"])
                    for p in picks
                    if p.get("r_multiple") is not None
                ]
            )
            if exit_rule is not None
            else None
        ),
        "exit_rule": (
            {
                "stop_pct": exit_rule.stop_pct,
                "reward_ratio": exit_rule.reward_ratio,
                "cost_bps": exit_rule.cost_bps,
            }
            if exit_rule is not None
            else None
        ),
        "gap_buckets": bucket_analysis.bucket_breakdown(
            picks, lambda p: abs(p["entry_pct_change"]), bucket_analysis.GAP_BUCKETS, views=_VIEWS
        ),
        "rvol_buckets": bucket_analysis.bucket_breakdown(
            picks, lambda p: p["entry_rvol"], bucket_analysis.RVOL_BUCKETS, views=_VIEWS
        ),
        "alpha": alpha_by_view(picks, _VIEWS),
        "catalyst_split": catalyst_split(picks),
        "fade_risk": {
            "threshold": formulas._FADE_RISK_RVOL,
            "views": fade_risk_by_view(picks, _VIEWS),
        },
        # Capped for transport, spread across the whole period -- every stat
        # above used the full list.
        # Full list alongside the sampled one: every aggregate here already
        # used it, and a CLI caller doing its own analysis needs it too.
        "picks_all": picks,
        "picks": sample_picks(picks),
        "picks_truncated": len(picks) > len(sample_picks(picks)),
    }
