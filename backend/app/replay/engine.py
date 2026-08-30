"""Per-user history replay: a steppable, live-feeling version of the
one-shot batch reports app.scanners.backtest/intraday_backtest already
build.

The novel piece is the wiring, not the data: app.scanners.intraday_backtest's
build_rows_by_timestamp already walks 5-minute bars chronologically per
symbol, carrying running volume/high/low/trailing-window state forward
exactly like a live poll cycle would, producing a per-timestamp
cross-sectional row set (rows_by_ts). It just never gets fed through the
three *fixed* live ranking functions -- only through a user screen
(screener.run_screen). This module closes that gap: at any requested
as_of timestamp, it looks up the nearest bar at or before it and runs that
cohort through engine._rank_gainers/_rank_losers/_rank_most_active (the
same live functions app.scanners.backtest already reuses at daily
resolution, not a reimplementation), so a replay session sees exactly the
three views the live scanner would have shown at that moment.

Reduced fidelity by construction, same as every other backtest tool here:
no historical news (has_headline is always False, so the catalyst
boost/discount never applies -- see formulas.rank_score), no momentum
alarm or strategy signals (need the live momentum cache / strategy
runner, not touched here), no float/market cap/short interest/company
info (today's fundamentals applied to a past date would be look-ahead
bias), no spread % (no historical NBBO quotes fetched anywhere). Callers
building a ScannerRow from a replayed row leave all of those at their
schema defaults (None/False/[]) rather than fabricating a value.

Bars are held in memory only, never persisted -- the live app has never
persisted bar data anywhere (see app.market_data.bars' own module
docstring), and a ReplayEngine follows that same discipline. Only the
lightweight session metadata (which date range/symbols/pacing state a
user is on) is durable, in app.replay.store.ReplayStore; the bars
themselves are re-fetched (through the same disk cache
app.scanners.bar_cache already gives every other backtest tool, so a
repeat run over the same symbols/lookback costs nothing) whenever a
user's engine isn't already resident in the in-process ReplayEngineCache
below -- e.g. after a server restart.
"""

import logging
from bisect import bisect_left, bisect_right
from datetime import datetime, timedelta

from app.alpaca.client import AlpacaClients
from app.market_data.bars import get_daily_bars_multi
from app.market_data.volume_profile import DEFAULT_REFERENCE_SYMBOL, build_fraction_curve
from app.scanners.backtest import _WARMUP_CALENDAR_PADDING_DAYS, _HistoricalNews
from app.scanners.bar_cache import DEFAULT_CACHE_DIR, get_cached_5m_bars_multi
from app.scanners.engine import _rank_gainers, _rank_losers, _rank_most_active
from app.scanners.intraday_backtest import build_rows_by_timestamp, previous_closes
from app.scanners.rvol_backtest import trailing_avg_daily_volume
from app.scanners.schemas import ScannerRow

logger = logging.getLogger(__name__)

_BAR_LENGTH = timedelta(minutes=5)
# No historical headline source exists (see module docstring) -- every
# lookup returns None, same stand-in app.scanners.backtest already uses to
# feed the live ranking functions when there's nothing to feed them.
_NO_NEWS = _HistoricalNews({})


def _row_to_scanner_row(row) -> ScannerRow:
    """_IntradayRow -> the real ScannerRow schema, not a hand-rolled dict --
    so the frontend's existing types/components need zero changes to render
    a replayed row the same way they render a live one. Every field this
    can't reconstruct (see module docstring) is left at its schema default.
    """
    return ScannerRow(
        symbol=row.symbol,
        last_price=row.last_price,
        prev_close=row.last_price - row.last_price * row.pct_change / 100.0 if row.pct_change != -100 else 0.0,
        pct_change=row.pct_change,
        gap_pct=row.gap_pct,
        volume_today=row.volume_today,
        avg_vol_20d=row.avg_vol_20d,
        rvol=row.rvol,
        dollar_volume_today=row.dollar_volume_today,
        day_high=row.day_high,
        day_low=row.day_low,
        is_hod=row.is_hod,
        is_lod=row.is_lod,
        is_fade_risk=row.is_fade_risk,
        volume_1h=row.volume_1h,
        volume_surge=row.volume_surge,
        rvol_1h=row.rvol_1h,
        updated_at=datetime.now(),
    )


class ReplayEngine:
    """One user's already-fetched historical bars, answerable at any as_of
    timestamp. See module docstring for what this can and can't reconstruct.
    """

    def __init__(self, bars_by_symbol: dict[str, list], daily_bars_by_symbol: dict[str, list], curve: list):
        avg_vol_by_date = trailing_avg_daily_volume(daily_bars_by_symbol)
        prev_close_by_date = previous_closes(daily_bars_by_symbol)
        rows_by_ts, _exit_price, _session_bars = build_rows_by_timestamp(
            bars_by_symbol, avg_vol_by_date, prev_close_by_date, curve, _BAR_LENGTH
        )
        self._rows_by_ts = rows_by_ts
        self.timestamps: list[datetime] = sorted(rows_by_ts)
        if not self.timestamps:
            logger.warning(
                "ReplayEngine built with zero usable timestamps -- likely too few symbols had both "
                "a 20-trading-day volume baseline and a previous close inside the fetched range"
            )

    @property
    def start(self) -> datetime | None:
        return self.timestamps[0] if self.timestamps else None

    @property
    def end(self) -> datetime | None:
        return self.timestamps[-1] if self.timestamps else None

    def _at_or_before(self, as_of: datetime) -> datetime | None:
        i = bisect_right(self.timestamps, as_of) - 1
        return self.timestamps[i] if i >= 0 else None

    def first_at_or_after(self, as_of: datetime) -> datetime | None:
        """Where a newly started session's clock should land -- the first
        bar on or after the caller's requested start, since a raw calendar
        date rarely lines up with a session's first 5-minute bar (premarket
        gaps, weekends, holidays)."""
        i = bisect_left(self.timestamps, as_of)
        return self.timestamps[i] if i < len(self.timestamps) else None

    def _cohort(self, as_of: datetime) -> list:
        ts = self._at_or_before(as_of)
        return self._rows_by_ts[ts] if ts is not None else []

    def snapshot_views(self, as_of: datetime, min_dollar_volume: float) -> dict[str, list[ScannerRow]]:
        """The three fixed live views, as they would have read at `as_of` --
        or the nearest bar at or before it, since as_of rarely lands exactly
        on a 5-minute boundary. Empty lists before the first available
        timestamp or once bars run out, not an error: a replay clock can
        legitimately sit before/after the fetched range while paused.
        """
        cohort = self._cohort(as_of)
        if not cohort:
            return {"gainers": [], "losers": [], "most_active": []}
        return {
            "gainers": [_row_to_scanner_row(r) for r in _rank_gainers(cohort, _NO_NEWS, min_dollar_volume)],
            "losers": [_row_to_scanner_row(r) for r in _rank_losers(cohort, _NO_NEWS, min_dollar_volume)],
            "most_active": [
                _row_to_scanner_row(r) for r in _rank_most_active(cohort, _NO_NEWS, min_dollar_volume)
            ],
        }

    def reference_price(self, symbol: str, as_of: datetime) -> float | None:
        """What SimOrderService.reference_price returns for a live symbol --
        the price a replayed order should size/fill against."""
        for row in self._cohort(as_of):
            if row.symbol == symbol:
                return row.last_price
        return None

    def day_high(self, symbol: str, as_of: datetime) -> float | None:
        for row in self._cohort(as_of):
            if row.symbol == symbol:
                return row.day_high
        return None


async def load_replay_engine(
    clients: AlpacaClients,
    symbols: list[str],
    lookback_days: int,
    cache_dir=DEFAULT_CACHE_DIR,
    force_refresh: bool = False,
) -> ReplayEngine:
    """Fetch + build, the same orchestration
    app.scanners.intraday_backtest_runner.run_intraday_backtest already
    uses (disk-cached 5-minute bars, daily bars for the trailing-volume
    baseline/previous closes, the reference symbol's curve for
    time-of-day-normalized RVOL) -- reused here rather than duplicated.
    """
    bars_by_symbol = await get_cached_5m_bars_multi(
        clients, symbols, lookback_days, cache_dir=cache_dir, force_refresh=force_refresh
    )
    daily = await get_daily_bars_multi(clients, symbols, lookback_days=lookback_days + _WARMUP_CALENDAR_PADDING_DAYS)
    reference = await get_cached_5m_bars_multi(
        clients, [DEFAULT_REFERENCE_SYMBOL], lookback_days, cache_dir=cache_dir, force_refresh=force_refresh
    )
    curve = build_fraction_curve(reference.get(DEFAULT_REFERENCE_SYMBOL) or [])
    if not curve:
        logger.warning("Replay volume profile empty -- rvol_1h will be None for this session")
    return ReplayEngine(bars_by_symbol, daily, curve)


class ReplayEngineCache:
    """In-process, per-user cache of already-loaded ReplayEngine instances --
    not persisted (see module docstring). One app.state instance, shared by
    the router and the pacing loop, so starting a session and then playing
    it don't each pay their own fetch cost.
    """

    def __init__(self) -> None:
        self._by_user: dict[int, ReplayEngine] = {}

    def get(self, user_id: int) -> ReplayEngine | None:
        return self._by_user.get(user_id)

    def put(self, user_id: int, engine: ReplayEngine) -> None:
        self._by_user[user_id] = engine

    def discard(self, user_id: int) -> None:
        self._by_user.pop(user_id, None)
