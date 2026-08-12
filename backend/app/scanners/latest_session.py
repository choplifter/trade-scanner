"""Real fallback for scanner views when there's nothing live to show (e.g.
markets closed): computes every symbol's most recently completed session's
real close-to-close move, for app.scanners.engine to rank per-view
(gainers/losers/most_active) on top of, instead of showing an empty scanner
or fabricating data. Unfiltered/unranked here deliberately -- a single
fetch covers every view instead of hitting Alpaca once per view.
"""

import logging
from datetime import datetime, timezone

from app.alpaca.client import AlpacaClients
from app.alpaca.universe import UniverseSymbol
from app.market_data.bars import get_daily_bars_multi
from app.scanners import formulas
from app.scanners.schemas import ScannerRow

logger = logging.getLogger(__name__)

_BATCH_SIZE = 300


async def compute_latest_session_rows(
    clients: AlpacaClients, universe: dict[str, UniverseSymbol]
) -> list[ScannerRow]:
    symbols = list(universe.keys())
    now = datetime.now(timezone.utc)
    rows: list[ScannerRow] = []

    for i in range(0, len(symbols), _BATCH_SIZE):
        batch = symbols[i : i + _BATCH_SIZE]
        try:
            # 10 calendar days comfortably covers 2+ trading sessions even
            # across a long weekend or holiday.
            bars_by_symbol = await get_daily_bars_multi(clients, batch, lookback_days=10)
        except Exception:
            logger.exception(
                "Latest-session rows fetch failed for a batch of %d symbols", len(batch)
            )
            continue

        for symbol, bars in bars_by_symbol.items():
            uni = universe.get(symbol)
            if uni is None or len(bars) < 2:
                continue
            prev_bar, last_bar = bars[-2], bars[-1]
            pct = formulas.pct_change(last_bar.close, prev_bar.close)
            if pct is None:
                continue
            volume = last_bar.volume
            row_rvol = formulas.rvol(volume, uni.avg_vol_20d) or 0.0
            rows.append(
                ScannerRow(
                    symbol=symbol,
                    exchange=uni.exchange,
                    last_price=last_bar.close,
                    prev_close=prev_bar.close,
                    pct_change=pct,
                    volume_today=volume,
                    avg_vol_20d=uni.avg_vol_20d,
                    rvol=row_rvol,
                    dollar_volume_today=formulas.dollar_volume(volume, last_bar.close),
                    day_high=last_bar.high,
                    day_low=last_bar.low,
                    is_hod=formulas.is_hod(last_bar.close, last_bar.high),
                    is_lod=formulas.is_lod(last_bar.close, last_bar.low),
                    spread_pct=None,
                    is_fade_risk=formulas.is_fade_risk(row_rvol),
                    updated_at=now,
                )
            )

    return rows
