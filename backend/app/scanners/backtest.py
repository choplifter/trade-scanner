"""Daily-bar-resolution historical backtest: replays past trading days
through the app's *actual* ranking functions (engine._rank_gainers/
_rank_losers, not a reimplementation) to reconstruct what would have
ranked as a gainer/loser, then buckets win-rate/avg-return with the same
methodology already trusted for live data (bucket_analysis.py).

Deliberately out of scope for this pass:
- Catalyst/headline backtesting -- needs historical news data (a harder,
  separate phase), so every row here is ranked with has_headline=False
  (news_cache=None passed to _rank_gainers/_rank_losers).
- Minute-resolution signals (time-of-day RVOL, the momentum alarm's
  marubozu check) -- would need historical minute bars, far more data
  volume than a daily-bar pass.
- Point-in-time universe reconstruction -- this backtests today's live
  universe membership against past dates, which has survivorship bias
  (a stock that doesn't qualify for today's universe might have
  legitimately qualified months ago). Accepted, not solved, here.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from statistics import mean

from app.alpaca.client import AlpacaClients
from app.core.config import Settings
from app.market_data.bars import get_daily_bars_multi
from app.scanners import bucket_analysis, formulas
from app.scanners.engine import _rank_gainers, _rank_losers

# Trading days of trailing volume history needed before a day can get an
# avg_vol_20d (and therefore an RVOL) computed from the bars themselves --
# never from UniverseSymbol.avg_vol_20d, which is today's live figure and
# not valid for a past date.
_WARMUP_TRADING_DAYS = 20
# Calendar-day padding requested on top of the caller's lookback_days so
# the earliest evaluated day still has a full 20 *trading* days behind it
# -- 20 trading days can span ~28-30+ calendar days across weekends/
# holidays, so this pads generously rather than exactly.
_WARMUP_CALENDAR_PADDING_DAYS = 40
# get_daily_bars_multi doesn't chunk symbols itself -- mirrors
# app.alpaca.universe._BATCH_SIZE's precedent for the same reason.
_FETCH_BATCH_SIZE = 500


@dataclass
class _BacktestRow:
    """Just enough fields for engine._tradable/_rank_gainers/_rank_losers
    and formulas.rank_score to work -- those functions are duck-typed over
    any object with these attributes, not tied to the full ScannerRow
    pydantic model (which would need fabricating unused fields like
    updated_at for no benefit here).
    """

    symbol: str
    last_price: float
    pct_change: float
    volume_today: float
    dollar_volume_today: float
    rvol: float


def simulate_from_bars(
    bars_by_symbol: dict[str, list],
    min_dollar_volume: float,
    horizon_days: int = 1,
) -> list[dict]:
    """Pure, network-free -- reconstructs each historical trading day's
    gainers/losers exactly as engine._rank_gainers/_rank_losers would have
    ranked it live, using only that day's trailing-20-bar history.

    Rows are grouped by each bar's own `.timestamp.date()`, not by list
    index: two symbols' bar lists aren't guaranteed to line up index-for-
    index (a newer listing starts later, a halt leaves a gap), so grouping
    by index would silently rank a symbol against the wrong day's cohort
    the moment any symbol's history isn't perfectly continuous. The
    trailing-RVOL window and the forward-horizon outcome stay index-based
    *within one symbol's own series* -- those are inherently per-symbol,
    trading-day-count concepts -- only the cross-sectional ranking cohort
    is grouped by date.

    `bars_by_symbol` values are Bar-like: need `.close`, `.volume`,
    `.timestamp` (a datetime). Oldest-first per symbol, as
    get_daily_bars_multi returns them.
    """
    rows_by_date: dict[date, list[_BacktestRow]] = defaultdict(list)
    exit_price_by_symbol_date: dict[tuple[str, date], float] = {}

    for symbol, bars in bars_by_symbol.items():
        if len(bars) < _WARMUP_TRADING_DAYS + horizon_days + 1:
            continue
        for i in range(_WARMUP_TRADING_DAYS, len(bars) - horizon_days):
            bar = bars[i]
            prev_close = bars[i - 1].close
            pct = formulas.pct_change(bar.close, prev_close)
            if pct is None:
                continue
            avg_vol_20d = mean(b.volume for b in bars[i - _WARMUP_TRADING_DAYS : i])
            row_rvol = formulas.rvol(bar.volume, avg_vol_20d) or 0.0
            trading_date = bar.timestamp.date()
            rows_by_date[trading_date].append(
                _BacktestRow(
                    symbol=symbol,
                    last_price=bar.close,
                    pct_change=pct,
                    volume_today=bar.volume,
                    dollar_volume_today=formulas.dollar_volume(bar.volume, bar.close),
                    rvol=row_rvol,
                )
            )
            exit_price_by_symbol_date[(symbol, trading_date)] = bars[i + horizon_days].close

    picks: list[dict] = []
    for trading_date, day_rows in rows_by_date.items():
        for view_name, ranked in (
            ("gainers", _rank_gainers(day_rows, None, min_dollar_volume)),
            ("losers", _rank_losers(day_rows, None, min_dollar_volume)),
        ):
            for row in ranked:
                exit_price = exit_price_by_symbol_date.get((row.symbol, trading_date))
                if exit_price is None or row.last_price <= 0:
                    continue
                picks.append(
                    {
                        "symbol": row.symbol,
                        "trading_date": trading_date.isoformat(),
                        "view": view_name,
                        "entry_pct_change": row.pct_change,
                        "entry_rvol": row.rvol,
                        "pct_change_since_entry": (exit_price - row.last_price) / row.last_price * 100,
                    }
                )
    return picks


async def run_backtest(
    clients: AlpacaClients,
    settings: Settings,
    symbols: list[str],
    lookback_days: int = 180,
    horizon_days: int = 1,
    batch_size: int = _FETCH_BATCH_SIZE,
) -> dict:
    """Fetch/orchestration wrapper: chunks symbols (get_daily_bars_multi
    doesn't chunk itself), merges results, replays via simulate_from_bars,
    then buckets with the same bucket_analysis primitives history_store.py
    uses for the live drift report -- so a backtest report and a live
    drift report measure win rate/avg return identically.
    """
    fetch_lookback = lookback_days + _WARMUP_CALENDAR_PADDING_DAYS
    bars_by_symbol: dict[str, list] = {}
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i : i + batch_size]
        bars_by_symbol.update(await get_daily_bars_multi(clients, batch, lookback_days=fetch_lookback))

    picks = simulate_from_bars(bars_by_symbol, settings.scanner_min_dollar_volume, horizon_days)

    gap_buckets = bucket_analysis.bucket_breakdown(
        picks, lambda p: abs(p["entry_pct_change"]), bucket_analysis.GAP_BUCKETS, views=("gainers", "losers")
    )
    rvol_buckets = bucket_analysis.bucket_breakdown(
        picks, lambda p: p["entry_rvol"], bucket_analysis.RVOL_BUCKETS, views=("gainers", "losers")
    )

    above = [p for p in picks if p["entry_rvol"] > formulas._FADE_RISK_RVOL]
    at_or_below = [p for p in picks if p["entry_rvol"] <= formulas._FADE_RISK_RVOL]
    fade_risk = {
        "rvol_above_threshold": bucket_analysis.bucket_stats(above),
        "rvol_at_or_below_threshold": bucket_analysis.bucket_stats(at_or_below),
        "threshold": formulas._FADE_RISK_RVOL,
        "sufficient_sample": len(above) >= bucket_analysis.MIN_SAMPLE_SIZE,
    }

    return {
        "symbol_count": len(symbols),
        "symbols_with_bars": len(bars_by_symbol),
        "lookback_days": lookback_days,
        "horizon_days": horizon_days,
        "sample_size": len(picks),
        "gap_buckets": gap_buckets,
        "rvol_buckets": rvol_buckets,
        "fade_risk": fade_risk,
        "min_sample_size": bucket_analysis.MIN_SAMPLE_SIZE,
    }
