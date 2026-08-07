import asyncio
import logging
from datetime import datetime, timezone

from alpaca.data.requests import StockBarsRequest, StockSnapshotRequest
from alpaca.data.timeframe import TimeFrame

from app.alpaca.client import AlpacaClients
from app.alpaca.universe import UniverseSymbol
from app.core.config import Settings
from app.market_data.bars import today_premarket_start_utc
from app.scanners import formulas
from app.scanners.schemas import ScannerRow
from app.services.market_clock import ET, current_session, trading_hours_for
from app.ws.connection_manager import ConnectionManager

logger = logging.getLogger(__name__)

_SNAPSHOT_BATCH_SIZE = 300
_TOP_N = 50


def _rank_gainers(rows) -> list[ScannerRow]:
    tradable = [r for r in rows if r.volume_today > 0]
    return sorted(
        (r for r in tradable if r.pct_change > 0),
        key=lambda r: r.pct_change,
        reverse=True,
    )[:_TOP_N]


class ScannerEngine:
    """Polls Alpaca snapshots for the whole universe and broadcasts ranked
    scanner views over the "scanner:<name>" WebSocket topics.

    v1 ships exactly two views -- "gainers" and "premarket_gainers" -- both
    computed from the identical %-change-from-prior-close ranking (that IS
    what a "gap" is). During premarket they're intentionally the same live
    list; the moment the regular session opens, "premarket_gainers" freezes
    to a snapshot of the gap as it stood at 09:30 ET while "gainers" keeps
    tracking the live session, so the two widgets diverge instead of staying
    duplicates all day. losers/high_rvol/gap scanners reuse this same
    pipeline and are a v2 addition, not a redesign.
    """

    def __init__(
        self,
        clients: AlpacaClients,
        settings: Settings,
        universe: dict[str, UniverseSymbol],
        manager: ConnectionManager,
    ):
        self.clients = clients
        self.settings = settings
        self.universe = universe
        self.manager = manager
        self.rows: dict[str, ScannerRow] = {}
        self.session: str = "closed"
        self._premarket_snapshot: list[ScannerRow] | None = None

    def _compute_rows(self, snapshots: dict) -> None:
        now = datetime.now(timezone.utc)
        for symbol, snap in snapshots.items():
            uni = self.universe.get(symbol)
            if uni is None or snap is None:
                continue

            latest_trade_price = snap.latest_trade.price if snap.latest_trade else None
            daily_close = snap.daily_bar.close if snap.daily_bar else None
            prev_close = (
                snap.previous_daily_bar.close if snap.previous_daily_bar else uni.prev_close
            )

            last = formulas.resolve_last_price(latest_trade_price, daily_close, prev_close)
            if last is None or not prev_close:
                continue

            pct = formulas.pct_change(last, prev_close)
            if pct is None:
                continue

            volume_today = snap.daily_bar.volume if snap.daily_bar else 0.0
            day_high = snap.daily_bar.high if snap.daily_bar else None
            day_low = snap.daily_bar.low if snap.daily_bar else None
            quote = snap.latest_quote

            self.rows[symbol] = ScannerRow(
                symbol=symbol,
                exchange=uni.exchange,
                last_price=last,
                prev_close=prev_close,
                pct_change=pct,
                volume_today=volume_today,
                avg_vol_20d=uni.avg_vol_20d,
                rvol=formulas.rvol(volume_today, uni.avg_vol_20d) or 0.0,
                dollar_volume_today=formulas.dollar_volume(volume_today, last),
                day_high=day_high,
                day_low=day_low,
                is_hod=formulas.is_hod(last, day_high),
                is_lod=formulas.is_lod(last, day_low),
                spread_pct=formulas.spread_pct(
                    quote.bid_price if quote else None, quote.ask_price if quote else None
                ),
                updated_at=now,
            )

    def _live_gainers(self) -> list[ScannerRow]:
        return _rank_gainers(self.rows.values())

    def _build_views(self) -> dict[str, list[ScannerRow]]:
        gainers = self._live_gainers()

        if self.session == "premarket":
            premarket_gainers = gainers
        elif self._premarket_snapshot is not None:
            premarket_gainers = self._premarket_snapshot
        else:
            # No premarket session observed yet today (e.g. the app was
            # started mid-day) -- fall back to the live view rather than
            # showing an empty widget with no explanation.
            premarket_gainers = gainers

        return {"gainers": gainers, "premarket_gainers": premarket_gainers}

    def snapshot_view(self, name: str) -> list[ScannerRow]:
        return self._build_views().get(name, [])

    async def backfill_premarket_snapshot(self) -> None:
        """Retroactively compute today's premarket-gap snapshot from
        historical minute bars.

        The live loop only freezes "premarket_gainers" when it *observes* a
        premarket-to-regular transition (see run_loop). If the app starts
        (or restarts) after the open, there's nothing to freeze and the view
        just mirrors live "gainers" all day with no explanation. Called once
        at startup so a same-day restart still gets a real premarket view
        instead of a silent duplicate.
        """
        if not self.universe or not self.clients.settings.has_credentials:
            return

        now_et = datetime.now(ET)
        hours = trading_hours_for(now_et.date())
        if hours is None:
            return
        market_open, _ = hours
        if now_et < market_open:
            # Still premarket (or earlier) -- the live loop handles this
            # case naturally once it starts polling, nothing to backfill.
            return

        start = today_premarket_start_utc()
        end = market_open.astimezone(timezone.utc)
        symbols = list(self.universe.keys())
        rows: dict[str, ScannerRow] = {}

        for i in range(0, len(symbols), _SNAPSHOT_BATCH_SIZE):
            batch = symbols[i : i + _SNAPSHOT_BATCH_SIZE]
            try:
                bar_set = await asyncio.to_thread(
                    self.clients.data.get_stock_bars,
                    StockBarsRequest(
                        symbol_or_symbols=batch,
                        timeframe=TimeFrame.Minute,
                        start=start,
                        end=end,
                        feed=self.clients.feed,
                    ),
                )
            except Exception:
                logger.exception(
                    "Premarket backfill failed for a batch of %d symbols", len(batch)
                )
                continue

            for symbol, bars in bar_set.data.items():
                uni = self.universe.get(symbol)
                if uni is None or not bars:
                    continue
                last_bar = bars[-1]
                pct = formulas.pct_change(last_bar.close, uni.prev_close)
                if pct is None:
                    continue
                volume = sum(b.volume for b in bars)
                rows[symbol] = ScannerRow(
                    symbol=symbol,
                    exchange=uni.exchange,
                    last_price=last_bar.close,
                    prev_close=uni.prev_close,
                    pct_change=pct,
                    volume_today=volume,
                    avg_vol_20d=uni.avg_vol_20d,
                    rvol=formulas.rvol(volume, uni.avg_vol_20d) or 0.0,
                    dollar_volume_today=formulas.dollar_volume(volume, last_bar.close),
                    day_high=max(b.high for b in bars),
                    day_low=min(b.low for b in bars),
                    is_hod=False,
                    is_lod=False,
                    updated_at=datetime.now(timezone.utc),
                )

        self._premarket_snapshot = _rank_gainers(rows.values())
        logger.info("Backfilled premarket snapshot: %d gainers", len(self._premarket_snapshot))

    async def _poll_once(self) -> None:
        symbols = list(self.universe.keys())
        batches = [
            symbols[i : i + _SNAPSHOT_BATCH_SIZE]
            for i in range(0, len(symbols), _SNAPSHOT_BATCH_SIZE)
        ]
        for batch in batches:
            try:
                # alpaca-py's client is a blocking requests call -- run it off
                # the event loop so it doesn't stall WS broadcasts/HTTP
                # handling for the ~1-2s a batch of snapshots can take.
                snaps = await asyncio.to_thread(
                    self.clients.data.get_stock_snapshot,
                    StockSnapshotRequest(symbol_or_symbols=batch, feed=self.clients.feed),
                )
                self._compute_rows(snaps)
            except Exception:
                logger.exception("Snapshot poll failed for a batch of %d symbols", len(batch))

    async def run_loop(self) -> None:
        if not self.clients.settings.has_credentials:
            logger.warning("No Alpaca credentials configured -- scanner loop idling")

        while True:
            previous_session = self.session
            self.session = current_session()

            if previous_session == "premarket" and self.session != "premarket":
                # Regular session just opened (or premarket ended without an
                # open, e.g. a holiday) -- freeze "premarket gainers" as a
                # snapshot of the gap as it stood at that moment, using
                # whatever we last polled, before it gets overwritten by the
                # live regular-session poll below.
                self._premarket_snapshot = self._live_gainers()

            if self.session == "closed" or not self.universe or not self.clients.settings.has_credentials:
                await asyncio.sleep(self.settings.scanner_poll_interval_regular)
                continue

            interval = (
                self.settings.scanner_poll_interval_premarket
                if self.session == "premarket"
                else self.settings.scanner_poll_interval_regular
            )

            await self._poll_once()

            views = self._build_views()
            for name, rows in views.items():
                await self.manager.broadcast(
                    f"scanner:{name}",
                    {
                        "type": "scanner_update",
                        "scanner": name,
                        "session": self.session,
                        "rows": [r.model_dump(mode="json") for r in rows],
                    },
                )

            await asyncio.sleep(interval)
