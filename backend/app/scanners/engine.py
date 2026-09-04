import asyncio
import logging
import time
from datetime import date, datetime, timedelta, timezone

import httpx
from alpaca.data.requests import StockBarsRequest, StockSnapshotRequest
from alpaca.data.timeframe import TimeFrame

from app.alpaca.client import AlpacaClients
from app.alpaca.universe import (
    MergerAction,
    UniverseSymbol,
    fetch_merger_actions,
    fetch_movers_backstop,
    fetch_split_ratios,
)
from app.core.config import Settings
from app.fundamentals.cache import FundamentalsCache
from app.market_data.bars import today_premarket_start_utc
from app.market_data.gamma_exposure import SYMBOLS as GEX_SYMBOLS
from app.market_data.gamma_exposure import GexReading, fetch_gex
from app.market_data.market_conditions import (
    MarketConditions,
    compute_market_conditions,
    fetch_high_impact_events_today,
    fetch_vix,
)
from app.market_data.news import fetch_headlines, is_roundup_headline
from app.market_data import news_cache as news_cache_mod
from app.market_data.fmp_news import fetch_fmp_headlines
from app.market_data.news_cache import NewsCache
from app.market_data.news_feed import NewsFeedTracker
from app.market_data import volume_surge
from app.market_data import session_marks
from app.market_data.momentum import MOMENTUM_WINDOW_MINUTES
from app.market_data.volume_profile import VolumeProfileCache
from app.scanners import formulas, screener
from app.scanners.benchmark_tracker import ScannerBenchmarkTracker
from app.scanners.history_store import NewAppearance, ScannerHistoryStore
from app.scanners.momentum_cache import MomentumCache
from app.strategies import runner as strategy_runner
from app.strategies.loader import load_strategies
from app.scanners.latest_session import compute_latest_session_rows
from app.scanners.schemas import MergerActionRow, ScannerRow, StrategySignalRow
from app.scanners.sector_attribution import SECTOR_ETFS
from app.services.market_clock import ET, current_session, trading_hours_for
from app.ws.connection_manager import ConnectionManager

logger = logging.getLogger(__name__)

# Sector ETFs move on a different timescale than the names being scanned --
# a whole sector does not reprice inside five seconds -- so they are refreshed
# on their own slower clock rather than on every poll tick.
_SECTOR_ETF_INTERVAL = 60.0

_SNAPSHOT_BATCH_SIZE = 300
_TOP_N = 50


def _last_per_strategy(firings: list) -> list:
    """The most recent firing of each strategy, in name order.

    Per strategy rather than one overall, so two rules setting up on the same
    symbol both show -- and only the latest of each, because a rule that
    qualified on nine consecutive bars set up once.
    """
    latest: dict[str, tuple] = {}
    for index, signal in firings:
        latest[signal.strategy] = (index, signal)
    return [latest[name] for name in sorted(latest)]


def _has_headline(row: ScannerRow, news_cache: NewsCache | None) -> bool:
    """True only for a headline that's actually about this symbol -- a
    Benzinga movers-roundup mention (see
    app.market_data.news.is_roundup_headline) doesn't count, even though
    it's a real, current headline: being one of a dozen names listed
    because it happened to move today isn't evidence of *why*, so it
    shouldn't earn rank_score's catalyst boost the way an earnings/FDA/M&A
    story would. Still shown to the user as recent_headline regardless --
    this only affects ranking.
    """
    if news_cache is None:
        return False
    headline = news_cache.get(row.symbol)
    return headline is not None and not is_roundup_headline(headline)


def _tradable(rows, min_dollar_volume: float) -> list[ScannerRow]:
    return [
        r for r in rows if r.volume_today > 0 and r.dollar_volume_today >= min_dollar_volume
    ]


def _rank_gainers(
    rows, news_cache: NewsCache | None = None, min_dollar_volume: float = 0.0
) -> list[ScannerRow]:
    tradable = _tradable(rows, min_dollar_volume)
    return sorted(
        (r for r in tradable if r.pct_change > 0),
        key=lambda r: formulas.rank_score(r.pct_change, _has_headline(r, news_cache), r.rvol),
        reverse=True,
    )[:_TOP_N]


def _rank_losers(
    rows, news_cache: NewsCache | None = None, min_dollar_volume: float = 0.0
) -> list[ScannerRow]:
    # rank_score's discount multiplies whatever magnitude it's given, so
    # applying it directly to pct_change (negative here) still sorts correctly
    # ascending: a fade-risk discount pulls a negative number toward zero
    # (ranks lower) -- same as it does for the positive gainers case. The
    # catalyst_boost=True below (2026-08-29, an explicit override -- see
    # formulas._NO_CATALYST_DISCOUNT) works the same way in both directions:
    # a catalyst-backed loser gets pushed *more* negative (_CATALYST_BOOST,
    # ranks higher -- more notable a loser), a no-catalyst one gets pulled
    # toward zero (_NO_CATALYST_DISCOUNT, ranks lower), same arithmetic as
    # the fade-risk discount just described.
    tradable = _tradable(rows, min_dollar_volume)
    return sorted(
        (r for r in tradable if r.pct_change < 0),
        key=lambda r: formulas.rank_score(r.pct_change, _has_headline(r, news_cache), r.rvol),
    )[:_TOP_N]


def _rank_most_active(
    rows, news_cache: NewsCache | None = None, min_dollar_volume: float = 0.0
) -> list[ScannerRow]:
    """By dollar volume traded today -- direction-agnostic, unlike
    gainers/losers, so no pct_change filter.

    Ranked on dollar volume rather than raw share volume. The original reason
    was the IEX feed -- one exchange's slice of the consolidated tape, varying
    by symbol, so a raw volume *level* was only ever a partial count and
    weighting by price at least made the ordering track "where the money went"
    instead of "which symbol routes more flow through IEX". That reason is
    gone as of 2026-08-20 (ALPACA_DATA_FEED=sip: the volume is now the real
    volume), but the ranking stays as-is on its own merit: dollar volume is
    the better "most active" measure regardless of feed, since it doesn't rank
    a $6 name above a $45 one purely on share count.

    catalyst_boost=False: the headline multiplier/discount is gainers- and
    (since 2026-08-29) losers-only, not this view -- the per-view win-rate
    data here read -3.1pp, i.e. actually negative, so unlike losers this one
    was never a borderline case worth overriding (see
    formulas._NO_CATALYST_DISCOUNT).
    """
    tradable = _tradable(rows, min_dollar_volume)
    return sorted(
        tradable,
        key=lambda r: formulas.rank_score(
            r.dollar_volume_today, _has_headline(r, news_cache), r.rvol, catalyst_boost=False
        ),
        reverse=True,
    )[:_TOP_N]


def _rank_moderate_movers(
    rows, news_cache: NewsCache | None = None, min_dollar_volume: float = 0.0
) -> list[ScannerRow]:
    """The 3-8% band, run straight off its preset rather than restating the
    numbers here -- see screener.PRESETS["moderate_movers"] for what the band
    is and why it has an upper bound.

    Tracked as a live view (not just offered as a screen) so real forward
    performance accumulates for it in ScannerBenchmarkTracker and
    ScannerHistoryStore. It's the one selection that survived an
    out-of-sample test, and a backtest number nobody re-checks against live
    data is how a result quietly stops being true.

    Only rank_score is supplied as a derived value -- the preset needs no
    others, and it keeps the poll loop from doing a fundamentals lookup per
    row. If the preset is ever edited to filter on float or short interest,
    those would read as missing here and match nothing; screen the live
    screener for that instead, which computes the full set.
    """
    tradable = _tradable(rows, min_dollar_volume)
    derived = {
        "rank_score": {
            r.symbol: formulas.rank_score(
                r.pct_change,
                _has_headline(r, news_cache),
                r.rvol,
                catalyst_boost=r.pct_change > 0,
            )
            for r in tradable
        }
    }
    return screener.run_screen(
        tradable, screener.PRESETS["moderate_movers"]["screen"], derived
    ).rows


class ScannerEngine:
    """Polls Alpaca snapshots for the whole universe and broadcasts ranked
    scanner views over the "scanner:<name>" WebSocket topics.

    Five views: "gainers" and "losers" (ranked by %-change from prior
    close, opposite directions), "most_active" (ranked by dollar volume,
    direction-agnostic), "moderate_movers" (the 3-8% band, run from its
    preset -- see _rank_moderate_movers), and "premarket_gainers". During
    premarket,
    "premarket_gainers" is intentionally the same live list as "gainers";
    the moment the regular session opens, it freezes to a snapshot of the
    gap as it stood at 09:30 ET while "gainers" keeps tracking the live
    session, so the two widgets diverge instead of staying duplicates all
    day -- losers/most_active don't have a premarket-freeze variant, only
    gainers does. Every view falls back to the most recently completed
    session's real data when there's nothing live (see
    backfill_latest_session_rows), ranked the same way as the live case.
    """

    def __init__(
        self,
        clients: AlpacaClients,
        settings: Settings,
        universe: dict[str, UniverseSymbol],
        manager: ConnectionManager,
        fundamentals: FundamentalsCache,
        benchmark_tracker: ScannerBenchmarkTracker,
        history_store: ScannerHistoryStore,
        news_cache: NewsCache,
        momentum_cache: MomentumCache,
        http_client: httpx.AsyncClient,
        news_feed_tracker: NewsFeedTracker,
    ):
        self.clients = clients
        self.settings = settings
        self.universe = universe
        self.manager = manager
        self.fundamentals = fundamentals
        self.benchmark_tracker = benchmark_tracker
        self.history_store = history_store
        self.news_cache = news_cache
        self.momentum_cache = momentum_cache
        self.http_client = http_client
        self.news_feed_tracker = news_feed_tracker
        # Live user-defined screens, one per connected socket. Set by main.py
        # after construction (the WS layer owns it) -- None when the engine is
        # built without a websocket layer at all, e.g. in tests.
        self.screen_subscriptions = None
        # Supplies the time-of-day denominator for RVOL. Built lazily on the
        # first poll and refreshed daily; until then fraction_at returns 1.0 and
        # RVOL is the plain full-day ratio. See app.market_data.volume_profile.
        self.volume_profile = VolumeProfileCache(clients)
        self.market_conditions: MarketConditions | None = None
        self._last_market_conditions_refresh: float = 0.0
        self.gex: dict[str, GexReading] = {}
        self._last_gex_refresh: float = 0.0
        self.benchmark_symbol = "SPY"
        self.benchmark_price: float | None = None
        self.rows: dict[str, ScannerRow] = {}
        self.session: str = "closed"
        self._premarket_snapshot: list[ScannerRow] | None = None
        self._latest_session_rows: dict[str, ScannerRow] | None = None
        self._last_backstop_refresh: float = 0.0
        # Day % change per sector ETF, for app.scanners.sector_attribution.
        # Polled here rather than by the page that reads it so stock and
        # sector prices go stale together: both are gated on the same
        # can_poll, and comparing a live ETF against closed-market fallback
        # rows would subtract two different days from each other.
        self.sector_etf_pct_change: dict[str, float] = {}
        self.sector_etf_updated_at: datetime | None = None
        self._last_sector_etf_refresh: float = 0.0
        self._last_history_snapshot: float = 0.0
        self._split_ratios: dict[str, tuple[float, date]] = {}
        self._last_split_refresh: float = 0.0
        self._merger_actions: dict[str, MergerAction] = {}
        self._last_merger_refresh: float = 0.0
        self._last_news_feed_refresh: float = 0.0
        # The operator's pause switch (Settings / scanner header, persisted
        # in app_kv): no market-wide polling, no history snapshots, no
        # backstop movers while set. Market conditions, GEX and the news
        # feed keep running -- they serve the charts, not the scanner.
        self.paused: bool = False

    def _session_volume_fraction(self, at: datetime) -> float | None:
        """Time-of-day RVOL denominator, or None to leave RVOL un-normalized.

        Gated on settings.scanner_rvol_time_normalized -- see that setting for
        why it's off by default (the fade-risk threshold is calibrated against
        the un-normalized definition).
        """
        if not self.settings.scanner_rvol_time_normalized:
            return None
        return self.volume_profile.fraction_at(at)

    async def _ensure_volume_profile(self) -> None:
        """Built unconditionally, not gated on scanner_rvol_time_normalized.

        That flag governs whether formulas.rvol *rescales* RVOL -- a change to
        an existing metric's definition, which is why it ships off. The curve
        itself is just data (one SPY 5-minute fetch a day) and the
        volume-acceleration fields need it regardless, so gating the build on
        the flag would leave rvol_1h permanently None by default.
        """
        await self.volume_profile.ensure_fresh()

    def _compute_rows(self, snapshots: dict) -> None:
        now = datetime.now(timezone.utc)
        # One lookup per batch rather than per row -- the fraction only depends
        # on the clock, not the symbol. None when disabled, which makes
        # formulas.rvol fall back to the plain full-day ratio.
        session_fraction = self._session_volume_fraction(now)
        for symbol, snap in snapshots.items():
            uni = self.universe.get(symbol)
            if uni is None or snap is None:
                continue

            latest_trade_price = snap.latest_trade.price if snap.latest_trade else None
            daily_close = snap.daily_bar.close if snap.daily_bar else None
            # Timestamp of whatever's actually backing last_price below --
            # mirrors resolve_last_price's own priority (latest_trade over
            # daily_bar) closely enough for staleness purposes without
            # needing that function to report which source it picked.
            # None (only the previous-day close was available) means no
            # live confirmation at all today.
            last_trade_at = (
                snap.latest_trade.timestamp
                if snap.latest_trade
                else (snap.daily_bar.timestamp if snap.daily_bar else None)
            )
            prev_close = (
                snap.previous_daily_bar.close if snap.previous_daily_bar else uni.prev_close
            )
            # Rescale prev_close onto the post-split share basis if it
            # still comes from a session before the split -- see
            # fetch_split_ratios' docstring for why this can't just be an
            # `adjustment` param on the request instead, and why this has
            # to compare against previous_daily_bar's own date rather than
            # assuming "today" (the market can be closed for hours after
            # the calendar date rolls over, during which previous_daily_bar
            # still correctly lags the split by more than a day).
            split_info = self._split_ratios.get(symbol)
            if split_info and snap.previous_daily_bar and prev_close:
                ratio, ex_date = split_info
                if snap.previous_daily_bar.timestamp.date() < ex_date:
                    prev_close = prev_close * ratio

            volume_today = snap.daily_bar.volume if snap.daily_bar else 0.0
            day_high = snap.daily_bar.high if snap.daily_bar else None
            day_low = snap.daily_bar.low if snap.daily_bar else None

            last = formulas.resolve_last_price(
                latest_trade_price, daily_close, prev_close, day_low, day_high
            )
            if last is None or not prev_close:
                continue

            pct = formulas.pct_change(last, prev_close)
            if pct is None:
                continue

            quote = snap.latest_quote
            row_rvol = formulas.rvol(volume_today, uni.avg_vol_20d, session_fraction) or 0.0

            self.rows[symbol] = ScannerRow(
                symbol=symbol,
                exchange=uni.exchange,
                last_price=last,
                prev_close=prev_close,
                pct_change=pct,
                # Today's open against the same prev_close pct_change uses,
                # so the two are directly comparable. The daily bar carries
                # the session open; before the open there's no bar and this
                # stays None rather than reporting a gap that hasn't happened.
                gap_pct=(
                    formulas.pct_change(snap.daily_bar.open, prev_close)
                    if snap.daily_bar and snap.daily_bar.open
                    else None
                ),
                volume_today=volume_today,
                avg_vol_20d=uni.avg_vol_20d,
                rvol=row_rvol,
                dollar_volume_today=formulas.dollar_volume(volume_today, last),
                day_high=day_high,
                day_low=day_low,
                is_hod=formulas.is_hod(last, day_high),
                is_lod=formulas.is_lod(last, day_low),
                spread_pct=formulas.spread_pct(
                    quote.bid_price if quote else None, quote.ask_price if quote else None
                ),
                last_trade_at=last_trade_at,
                is_stale=formulas.is_stale(
                    last_trade_at, now, self.settings.scanner_stale_row_seconds
                ),
                is_fade_risk=formulas.is_fade_risk(row_rvol),
                shortable=uni.shortable,
                merger_action=self._merger_action_row(symbol),
                updated_at=now,
            )

    def _live_gainers(self) -> list[ScannerRow]:
        return _rank_gainers(
            self.rows.values(), self.news_cache, self.settings.scanner_min_dollar_volume
        )

    @property
    def is_latest_session_fallback(self) -> bool:
        """True when there's no live snapshot data at all (e.g. markets
        closed) and every view is instead the most recently completed
        session's real data -- see backfill_latest_session_rows.

        Deliberately keyed on self.rows itself, not any one view's ranked
        result: a live but broadly red day can have zero live gainers (or
        a broadly green one zero live losers) without that meaning the
        *data* isn't live -- the flag is about live-vs-fallback data, not
        about whether a specific view happens to be non-empty.
        """
        return not bool(self.rows)

    def _build_views(self) -> dict[str, list[ScannerRow]]:
        fallback_rows = (self._latest_session_rows or {}).values()
        min_dollar_volume = self.settings.scanner_min_dollar_volume

        live_gainers = self._live_gainers()
        gainers = live_gainers or _rank_gainers(fallback_rows, self.news_cache, min_dollar_volume)

        if self.session == "premarket":
            premarket_gainers = gainers
        elif self._premarket_snapshot is not None:
            premarket_gainers = self._premarket_snapshot
        elif live_gainers:
            # No premarket session observed yet today (e.g. the app was
            # started mid-day) -- fall back to the live view rather than
            # showing an empty widget with no explanation.
            premarket_gainers = live_gainers
        else:
            premarket_gainers = _rank_gainers(fallback_rows, self.news_cache, min_dollar_volume)

        losers = _rank_losers(self.rows.values(), self.news_cache, min_dollar_volume) or _rank_losers(
            fallback_rows, self.news_cache, min_dollar_volume
        )
        most_active = _rank_most_active(
            self.rows.values(), self.news_cache, min_dollar_volume
        ) or _rank_most_active(fallback_rows, self.news_cache, min_dollar_volume)

        moderate_movers = _rank_moderate_movers(
            self.rows.values(), self.news_cache, min_dollar_volume
        ) or _rank_moderate_movers(fallback_rows, self.news_cache, min_dollar_volume)

        return {
            "gainers": gainers,
            "premarket_gainers": premarket_gainers,
            "losers": losers,
            "most_active": most_active,
            "moderate_movers": moderate_movers,
        }

    def snapshot_view(self, name: str) -> list[ScannerRow]:
        return self._build_views().get(name, [])

    async def backfill_latest_session_rows(self) -> None:
        """Real fallback for when live polling has nothing yet (e.g. the
        app starts while markets are closed) -- computed once at startup
        from the most recently completed session's real close-to-close
        move for every symbol, instead of showing an empty scanner or
        fabricating data. Each view (gainers/losers/most_active/
        moderate_movers) applies
        its own ranking on top of this shared row set at build time -- see
        _build_views.
        """
        if not self.universe or not self.clients.settings.has_credentials:
            return
        try:
            rows = await compute_latest_session_rows(self.clients, self.universe)
        except Exception:
            logger.exception("Latest-session rows backfill failed")
            return
        self._latest_session_rows = {r.symbol: r for r in rows}
        logger.info("Backfilled latest-session rows: %d symbols", len(self._latest_session_rows))

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

        # These rows are cumulative premarket volume as of the open, so the
        # RVOL denominator is the share of a typical day done by 09:30 -- a few
        # percent, not a whole day. Without this a busy premarket reads as a
        # trivial RVOL. Built here rather than relying on the poll loop, since
        # this backfill runs once at startup and may get there first.
        await self._ensure_volume_profile()
        session_fraction = self._session_volume_fraction(end)

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
                bar_rvol = formulas.rvol(volume, uni.avg_vol_20d, session_fraction) or 0.0
                rows[symbol] = ScannerRow(
                    symbol=symbol,
                    exchange=uni.exchange,
                    last_price=last_bar.close,
                    prev_close=uni.prev_close,
                    pct_change=pct,
                    volume_today=volume,
                    avg_vol_20d=uni.avg_vol_20d,
                    rvol=bar_rvol,
                    dollar_volume_today=formulas.dollar_volume(volume, last_bar.close),
                    day_high=max(b.high for b in bars),
                    day_low=min(b.low for b in bars),
                    is_hod=False,
                    is_lod=False,
                    is_fade_risk=formulas.is_fade_risk(bar_rvol),
                    shortable=uni.shortable,
                    updated_at=datetime.now(timezone.utc),
                )

        self._premarket_snapshot = _rank_gainers(
            rows.values(), self.news_cache, self.settings.scanner_min_dollar_volume
        )
        logger.info("Backfilled premarket snapshot: %d gainers", len(self._premarket_snapshot))

    async def _refresh_movers_backstop(self) -> dict[str, UniverseSymbol]:
        """Merge in any new symbols from the movers backstop (see
        fetch_movers_backstop) on a slower cadence than the regular poll --
        the screener endpoint reflects the whole live tape when the market's
        open and the last completed session's close when it's not, so it
        doesn't need per-poll-interval freshness and isn't gated on can_poll
        (see run_loop) -- a backstop-only mover should still be findable
        while the market's closed, not just invisible until the next open.

        self.universe is the same dict object main.py stored on app.state,
        so mutating it in place here is enough for both to see the update.
        Returns whatever new symbols were actually added (possibly empty),
        so run_loop can fold them into the closed-market fallback view when
        live polling isn't running to pick them up on its own.
        """
        now = time.monotonic()
        if now - self._last_backstop_refresh < self.settings.movers_backstop_interval:
            return {}
        self._last_backstop_refresh = now

        new_symbols = await fetch_movers_backstop(self.clients, self.settings, self.universe)
        if new_symbols:
            self.universe.update(new_symbols)
        return new_symbols

    async def _refresh_split_ratios(self) -> None:
        """Refresh recent stock-split adjustments (see fetch_split_ratios)
        on a slower cadence than the regular poll. Not gated on can_poll so
        the ratios are already warm the moment live polling resumes,
        rather than every symbol's first live tick of the day briefly
        showing an unadjusted gap%.

        Only applied to the live path (_compute_rows) below -- the
        closed-market fallback (latest_session.py) computes its own
        prev_close straight from historical bars and isn't corrected here;
        a stale gap% there is a narrower edge case (only matters for a
        symbol that both split very recently *and* is being shown from
        fallback data, e.g. right after a fresh app start with markets
        closed) than the live path this fixes.
        """
        now = time.monotonic()
        if now - self._last_split_refresh < self.settings.split_ratio_refresh_interval:
            return
        self._last_split_refresh = now

        self._split_ratios = await fetch_split_ratios(self.clients)

    async def _refresh_merger_actions(self) -> None:
        """Refresh active merger corporate actions (see fetch_merger_actions)
        on the same slow, not-gated-on-can_poll shape as
        _refresh_split_ratios above -- a merger situation doesn't change
        status minute to minute, and staying warm across a poll pause means
        a symbol's M&A badge doesn't flicker off just because polling did.
        """
        now = time.monotonic()
        if now - self._last_merger_refresh < self.settings.merger_refresh_interval:
            return
        self._last_merger_refresh = now

        self._merger_actions = await fetch_merger_actions(
            self.clients, lookback_days=self.settings.merger_lookback_days
        )

    def _merger_action_row(self, symbol: str) -> MergerActionRow | None:
        action = self._merger_actions.get(symbol)
        if action is None:
            return None
        return MergerActionRow(
            acquirer_symbol=action.acquirer_symbol,
            sub_type=action.sub_type,
            cash_consideration=action.cash_consideration,
            announced_date=action.announced_date,
            effective_date=action.effective_date,
        )

    async def _refresh_news_feed(self, views: dict[str, list[ScannerRow]]) -> None:
        """Tell the news feed which symbols are ranked right now (the same
        canonical-view scope _record_new_appearances uses below, not ad hoc
        user screens), then poll once, market-wide, for anything the news
        websocket (app.market_data.news_stream) has not delivered --
        broadcasting each new article over the "news:feed" WS topic. See
        app.market_data.news_feed.NewsFeedTracker. One request per
        settings.news_feed_refresh_interval, whatever the market does.
        """
        now = time.monotonic()
        if now - self._last_news_feed_refresh < self.settings.news_feed_refresh_interval:
            return
        self._last_news_feed_refresh = now

        if not self.clients.settings.has_credentials:
            return

        symbols = {
            row.symbol
            for view_name in ("gainers", "losers", "most_active", "moderate_movers")
            for row in views.get(view_name, [])
        }
        self.news_feed_tracker.set_ranked(symbols)

        try:
            new_items = await self.news_feed_tracker.poll(self.clients)
        except Exception:
            logger.exception("News feed poll failed")
            return

        for item in new_items:
            await self.manager.broadcast(
                "news:feed", {"type": "news_feed_item", "item": item.model_dump(mode="json")}
            )

    async def _refresh_market_conditions(self) -> None:
        """Refresh the red/yellow/green market-conditions readout (VIX,
        today's high-impact global economic events, scanner breadth) on a
        slow cadence -- none of these need poll-tick freshness. Not gated
        on can_poll, same reasoning as the other slow refreshers here: the
        reading stays useful (and warm for whenever polling resumes) even
        while the market's closed. Gated on has_fmp_credentials since both
        external signals ride on FMP -- degrades to "unavailable"
        (self.market_conditions stays None) rather than an error when no
        FMP_API_KEY is configured, same as every other FMP-dependent
        feature in this app.
        """
        if not self.settings.has_fmp_credentials:
            return
        now = time.monotonic()
        if now - self._last_market_conditions_refresh < self.settings.market_conditions_refresh_interval:
            return
        self._last_market_conditions_refresh = now

        try:
            vix, events = await asyncio.gather(
                fetch_vix(self.http_client, self.settings.fmp_api_key),
                fetch_high_impact_events_today(
                    self.http_client, self.settings.fmp_api_key, datetime.now(ET).date()
                ),
            )
        except Exception:
            logger.exception("Market conditions refresh failed")
            return

        breadth_pct = None
        if self.rows:
            up = sum(1 for r in self.rows.values() if r.pct_change > 0)
            breadth_pct = up / len(self.rows) * 100

        self.market_conditions = compute_market_conditions(vix, events, breadth_pct)

    async def _refresh_gex(self) -> None:
        """Refresh net dealer gamma exposure for each covered symbol -- see
        app.market_data.gamma_exposure.SYMBOLS. Same not-gated-on-can_poll, slow-
        cadence reasoning as _refresh_market_conditions; gated on
        has_credentials (Alpaca, not FMP) since this rides entirely on
        Alpaca's options endpoints.
        """
        if not self.settings.has_credentials:
            return
        now = time.monotonic()
        if now - self._last_gex_refresh < self.settings.gex_refresh_interval:
            return
        self._last_gex_refresh = now

        # fetch_gex is itself best-effort (catches and logs internally,
        # returns None rather than raising), so one symbol's failure can't
        # take the other down -- no return_exceptions needed here.
        readings = await asyncio.gather(*(fetch_gex(self.clients, symbol) for symbol in GEX_SYMBOLS))
        for symbol, reading in zip(GEX_SYMBOLS, readings):
            if reading is not None:
                self.gex[symbol] = reading

    async def _merge_backstop_into_fallback(self, new_symbols: dict[str, UniverseSymbol]) -> None:
        """Fold freshly backstop-admitted symbols into the closed-market
        fallback row set (see backfill_latest_session_rows) -- only called
        from run_loop when can_poll is False, since live polling would
        otherwise pick these up on its own via the next full-universe
        _poll_once instead. Unranked here -- each view ranks the merged
        set for itself at build time (see _build_views).
        """
        try:
            new_rows = await compute_latest_session_rows(self.clients, new_symbols)
        except Exception:
            logger.exception(
                "Failed computing latest-session data for %d backstop symbol(s)",
                len(new_symbols),
            )
            return
        if not new_rows:
            return

        if self._latest_session_rows is None:
            self._latest_session_rows = {}
        for row in new_rows:
            self._latest_session_rows[row.symbol] = row
        logger.info(
            "Merged %d backstop symbol(s) into the closed-market fallback data", len(new_rows)
        )

    async def _attach_fundamentals(self, views: dict[str, list[ScannerRow]]) -> None:
        """Fill in float/market cap/short interest/country/company name for
        whatever's actually ranked right now -- see
        app.fundamentals.cache.FundamentalsCache for why market cap and profile
        are scoped to the ranked views instead of the whole universe.

        Float is the exception: it comes from FMP's bulk shares-float file
        (universe-wide, ~8 requests) rather than the per-symbol endpoint, so it's
        available for every row immediately instead of only after that symbol's
        own fundamentals fetch has landed.
        """
        symbols = {r.symbol for rows in views.values() for r in rows}
        if not symbols:
            return
        await self.fundamentals.ensure_fresh(symbols)
        for rows in views.values():
            for row in rows:
                row.float_shares = self.fundamentals.float_shares(row.symbol)
                data = self.fundamentals.get(row.symbol)
                if data is not None:
                    row.float_shares = data.float_shares or row.float_shares
                    row.market_cap = data.market_cap
                    row.short_interest_pct = data.short_interest_pct
                    row.country = data.profile.country if data.profile else None
                    row.company_name = data.profile.name if data.profile else None
                    row.sector = data.profile.sector if data.profile else None
                    row.logo_url = data.profile.logo_url if data.profile else None

    async def _attach_news(self, views: dict[str, list[ScannerRow]]) -> None:
        """Fill in each row's most recent news headline for whatever's
        actually ranked right now -- see app.market_data.news_cache.NewsCache
        for why this is scoped to the ranked views (and refreshed on a slow
        cadence) instead of fetched per symbol on every poll tick.
        """
        symbols = {r.symbol for rows in views.values() for r in rows}
        if not symbols:
            return
        await self.news_cache.ensure_fresh(symbols)
        for rows in views.values():
            for row in rows:
                row.recent_headline = self.news_cache.get(row.symbol)

    async def _attach_strategies(self, views: dict[str, list[ScannerRow]]) -> None:
        """Run the strategy scripts over whatever is ranked right now.

        Reads the 5-minute bars MomentumCache already holds, so this costs no
        extra fetch -- and is therefore scoped to the ranked views like
        momentum_pct, for the same reason.

        **The levels differ from the chart's, deliberately and visibly.** A
        strategy aims at the nearest level; the chart merges multi-day hourly
        structure with the day's own marks, while this has only the cached
        5-minute window and so uses the marks alone (premarket range,
        prior-session range). Adding structure levels can only put a level
        *nearer*, so a row flagged here can turn out, on the chart, to have a
        nearer target and a worse ratio. The marker is an invitation to look;
        the chart is the authority on the numbers.

        Loaded once per tick rather than held, which is what keeps a strategy
        file editable without a restart (see app.strategies.loader). One
        strategy raising must not stall the poll loop, so errors are
        swallowed here -- the backtest does the opposite.
        """
        symbols = {r.symbol for rows in views.values() for r in rows}
        if not symbols:
            return

        strategies, errors = load_strategies()
        for err in errors:
            logger.warning("Strategy %s failed to load: %s", err.filename, err.error)
        if not strategies:
            for rows in views.values():
                for row in rows:
                    row.strategy_signals = []
            return

        for rows in views.values():
            for row in rows:
                bars = self.momentum_cache.bars(row.symbol)
                if not bars:
                    # Set explicitly rather than left alone, so a symbol whose
                    # bars have gone away clears instead of showing a marker
                    # from a previous tick forever.
                    row.strategy_signals = []
                    continue

                session_date = bars[-1].timestamp.astimezone(ET).date()
                levels = tuple(sorted(session_marks.marks_for_session(bars, session_date)))
                firings = strategy_runner.walk_latest_session(
                    row.symbol, bars, strategies, levels, swallow_errors=True
                )
                # The last setup of the session, not one firing on this exact
                # bar. Two reasons, and the second is decisive:
                #
                # It matches what the chart draws, so the table and the chart
                # cannot disagree about whether a symbol set up today. And a
                # strict now-flag would mostly be invisible anyway -- bars are
                # five minutes wide and this cache refreshes every
                # scanner_momentum_refresh_interval seconds, so a firing that
                # lasted one bar would usually fall between two refreshes.
                #
                # fired_at carries the age so a reader can judge it rather
                # than assume the setup is live.
                row.strategy_signals = [
                    StrategySignalRow(
                        strategy=signal.strategy,
                        side=signal.side,
                        entry_price=signal.entry_price,
                        stop_price=signal.stop_price,
                        target_price=signal.target_price,
                        reason=signal.reason,
                        fired_at=bars[index].timestamp,
                    )
                    for index, signal in _last_per_strategy(firings)
                ]

    def scanner_update_payload(
        self,
        scanner: str,
        rows: list[ScannerRow],
        is_latest_session: bool | None = None,
    ) -> dict:
        """The one place a scanner_update message is shaped.

        Two callers push this message -- ws.scanner_ws on subscribe and
        run_loop on every poll tick -- and they had drifted: the subscribe
        reply carried window_minutes and momentum_window_minutes, the
        broadcast did not. Since the client replaces its whole state from each
        message, the column headers rendered "30m %" and "60m" for one moment
        and fell back to their generic labels on the very next tick.
        TypeScript could not catch it: the socket payload is untyped JSON cast
        at the boundary.

        run_loop passes is_latest_session explicitly because it reads the flag
        once per tick, so every view in that tick agrees rather than
        re-reading a property that could flip midway through the loop.
        """
        return {
            "type": "scanner_update",
            "scanner": scanner,
            "session": self.session,
            "is_latest_session": (
                self.is_latest_session_fallback if is_latest_session is None else is_latest_session
            ),
            # The window row.rvol_1h was computed with. Fixed views always run
            # on the global setting -- only a custom screen can override it --
            # but the client labels its column from this either way rather
            # than assuming 60.
            "window_minutes": self.settings.scanner_volume_surge_window_minutes,
            # The window row.momentum_pct was computed with. Global, unlike
            # the one above -- sent all the same so the client never has to
            # hardcode a number that goes stale when the window changes.
            "momentum_window_minutes": MOMENTUM_WINDOW_MINUTES,
            "rows": [r.model_dump(mode="json") for r in rows],
        }

    async def _attach_momentum(self, views: dict[str, list[ScannerRow]]) -> None:
        """Fill in each row's trailing-window pct change for whatever's
        actually ranked right now -- see
        app.scanners.momentum_cache.MomentumCache for why this is scoped to
        the ranked views (and refreshed on its own slow cadence) instead of
        fetching minute bars for every poll tick.
        """
        symbols = {r.symbol for rows in views.values() for r in rows}
        if not symbols:
            return
        await self.momentum_cache.ensure_fresh(symbols)
        window = timedelta(minutes=self.settings.scanner_volume_surge_window_minutes)
        for rows in views.values():
            for row in rows:
                row.momentum_pct = self.momentum_cache.get(row.symbol)
                # How much of the real tape our feed saw today -- a health
                # figure for volume levels, not for VWAP. See
                # FundamentalsCache.tape_coverage_pct. Correct here only
                # because run_loop refreshes the fundamentals cache before
                # calling this; see the prefetch there.
                row.tape_coverage_pct = self.fundamentals.tape_coverage_pct(
                    row.symbol, row.volume_today
                )
                row.is_momentum_alert = formulas.is_momentum_alert(
                    row.momentum_pct,
                    self.momentum_cache.is_shaved_top(row.symbol),
                    self.momentum_cache.is_green(row.symbol),
                    self.momentum_cache.is_above_vwap(row.symbol),
                    self.settings.alarm_momentum_pct_threshold,
                )
                # Computed here rather than in the cache because this is the
                # only place holding all three inputs: the cache's window
                # volumes, the universe's avg_vol_20d, and the intraday
                # profile curve.
                row.volume_1h = self.momentum_cache.window_volume(row.symbol)
                row.volume_surge = volume_surge.surge_ratio(
                    row.volume_1h, self.momentum_cache.prior_window_volume(row.symbol)
                )
                row.rvol_1h = volume_surge.windowed_rvol(
                    self.momentum_cache.bars(row.symbol),
                    row.avg_vol_20d,
                    self.volume_profile.curve,
                    window,
                )

    def _evaluate_screens(self) -> list[tuple[object, list[ScannerRow], dict]]:
        """Run every connected client's live screen against the current rows.

        Returns (websocket, matched_rows, payload) so run_loop can enrich the
        rows before the payload is serialized -- the rows in the payload are
        the same objects, so enrichment lands in what gets sent.

        Imported lazily: app.ws.screen_subscriptions imports the screener,
        which imports this module, and a module-level import here would close
        that cycle at import time.
        """
        if self.screen_subscriptions is None or not self.screen_subscriptions.any_active():
            return []

        from app.scanners.screener_service import run_live_screen

        results = []
        for websocket, screen in self.screen_subscriptions.active():
            try:
                rows, meta = run_live_screen(self, self.settings, screen)
            except Exception:
                logger.exception("Failed evaluating a live screen")
                continue
            results.append((websocket, rows, meta))
        return results

    async def _broadcast_screens(self, results) -> None:
        """Serialized here, after run_loop has enriched the rows -- doing it
        in _evaluate_screens would snapshot them before fundamentals, news
        and momentum landed.
        """
        if not results or self.screen_subscriptions is None:
            return
        from app.scanners.screener_service import serialize_screen

        await self.screen_subscriptions.broadcast(
            [
                (websocket, {"type": "screen_update", **serialize_screen(rows, meta)})
                for websocket, rows, meta in results
            ]
        )

    async def _poll_once(self) -> None:
        # Cheap no-op after the first successful build (daily refresh), and
        # skipped entirely when time-of-day RVOL is disabled.
        await self._ensure_volume_profile()
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

    async def _poll_benchmark(self) -> None:
        """Refresh self.benchmark_price (SPY) -- not part of self.universe
        (it's outside the $1-$50 universe price filter and shouldn't show
        up as a scanner row), so it needs its own small snapshot call
        rather than riding along with _poll_once's batches.
        """
        try:
            snap = await asyncio.to_thread(
                self.clients.data.get_stock_snapshot,
                StockSnapshotRequest(symbol_or_symbols=[self.benchmark_symbol], feed=self.clients.feed),
            )
            s = snap.get(self.benchmark_symbol)
            if s is None:
                return
            price = formulas.resolve_last_price(
                s.latest_trade.price if s.latest_trade else None,
                s.daily_bar.close if s.daily_bar else None,
                s.previous_daily_bar.close if s.previous_daily_bar else None,
            )
            if price is not None:
                self.benchmark_price = price
        except Exception:
            logger.exception("Benchmark (%s) snapshot poll failed", self.benchmark_symbol)

    async def _poll_sector_etfs(self) -> None:
        """Refresh self.sector_etf_pct_change -- one snapshot for the eleven
        SPDR sector ETFs plus SPY.

        Like the benchmark above, these sit outside the universe's price
        filter and need their own small call. Unlike it, what is wanted here
        is a *day change* rather than a bare price, because the attribution
        subtracts three same-day percentages -- benchmark_price is a price
        because the tracker measures entry-to-now instead.

        Throttled on the same monotonic guard the movers backstop uses.
        """
        now = time.monotonic()
        if (
            self.sector_etf_pct_change
            and now - self._last_sector_etf_refresh < _SECTOR_ETF_INTERVAL
        ):
            return
        self._last_sector_etf_refresh = now

        symbols = sorted({*SECTOR_ETFS.values(), self.benchmark_symbol})
        try:
            snap = await asyncio.to_thread(
                self.clients.data.get_stock_snapshot,
                StockSnapshotRequest(symbol_or_symbols=symbols, feed=self.clients.feed),
            )
        except Exception:
            logger.exception("Sector ETF snapshot poll failed")
            return

        for symbol in symbols:
            # Per symbol, and never clearing the dict wholesale: one
            # unparseable snapshot must not cost the other eleven.
            try:
                s = snap.get(symbol)
                if s is None or s.previous_daily_bar is None:
                    continue
                prev_close = s.previous_daily_bar.close
                last = formulas.resolve_last_price(
                    s.latest_trade.price if s.latest_trade else None,
                    s.daily_bar.close if s.daily_bar else None,
                    prev_close,
                    # Passed so the bad-print sanity check applies, the way
                    # _compute_rows does -- _poll_benchmark omits them.
                    s.daily_bar.low if s.daily_bar else None,
                    s.daily_bar.high if s.daily_bar else None,
                )
                if last is None:
                    continue
                pct = formulas.pct_change(last, prev_close)
                if pct is not None:
                    self.sector_etf_pct_change[symbol] = pct
            except Exception:
                logger.warning("Could not read sector ETF snapshot for %s", symbol, exc_info=True)

        if self.sector_etf_pct_change:
            self.sector_etf_updated_at = datetime.now(timezone.utc)

    async def _record_new_appearances(self, views: dict[str, list[ScannerRow]]) -> None:
        """Log the first time each symbol shows up in a *ranked* view (not
        premarket_gainers, which mirrors/freezes "gainers" rather than
        being an independent signal -- would just double-count the same
        symbols) so ScannerBenchmarkTracker (in-memory, this process only)
        and ScannerHistoryStore (SQLite, survives restarts -- see
        history_store.py) can compare performance since then against
        self.benchmark_price. See benchmark_tracker.py for why only the
        first appearance counts; history_store dedupes per trading day
        instead, via its own UNIQUE constraint.

        A news headline is attached to each newly-recorded appearance (not
        re-fetched on repeat ranks) as context for *why* it moved -- fetched
        only for candidates existing_keys_for_date says aren't already in
        the store today, so a fetch isn't wasted on the same ~150 ranked
        symbols every 5-10s poll tick for the rest of the day. The same
        headlines feed ScannerBenchmarkTracker too (it has its own, looser
        "new" semantics -- see benchmark_tracker.py -- so right after a
        restart it can consider a symbol new that history_store's
        per-trading-day check doesn't; that symbol just won't have a
        headline yet, same as any other best-effort-missing data here).
        """
        candidates = [
            (view_name, row)
            for view_name in ("gainers", "losers", "most_active", "moderate_movers")
            for row in views.get(view_name, [])
        ]

        trading_date = datetime.now(timezone.utc).astimezone(ET).date().isoformat()
        try:
            existing_keys = await self.history_store.existing_keys_for_date(trading_date)
        except Exception:
            logger.exception("Failed reading existing scanner history keys")
            existing_keys = set()

        new_symbols = {row.symbol for view_name, row in candidates if (row.symbol, view_name) not in existing_keys}
        headlines: dict[str, str] = {}
        headline_sources: dict[str, str] = {}
        if new_symbols:
            try:
                headlines = await fetch_headlines(self.clients, sorted(new_symbols))
                headline_sources = {s: news_cache_mod.ALPACA for s in headlines}
            except Exception:
                logger.exception("Failed fetching news headlines for new scanner appearances")

            # Same Alpaca-first, FMP-for-the-gaps policy the live NewsCache
            # uses, applied here too -- this is the path that writes
            # scanner_history, so a coverage gap here becomes a permanently
            # missing catalyst in every later drift report.
            uncovered = sorted(new_symbols - set(headlines))
            if uncovered and self.settings.has_fmp_credentials:
                try:
                    fallback = await fetch_fmp_headlines(
                        self.http_client, self.settings.fmp_api_key, uncovered
                    )
                except Exception:
                    logger.exception("FMP headline fallback failed for new scanner appearances")
                    fallback = {}
                for symbol, headline in fallback.items():
                    headlines[symbol] = headline
                    headline_sources[symbol] = news_cache_mod.FMP

        for view_name, row in candidates:
            self.benchmark_tracker.record_if_new(
                symbol=row.symbol,
                view=view_name,
                entry_price=row.last_price,
                entry_pct_change=row.pct_change,
                entry_rvol=row.rvol,
                benchmark_entry_price=self.benchmark_price,
                entry_headline=headlines.get(row.symbol),
                entry_dollar_volume=row.dollar_volume_today,
            )

        new_entries = [
            NewAppearance(
                symbol=row.symbol,
                view=view_name,
                entry_price=row.last_price,
                entry_pct_change=row.pct_change,
                entry_rvol=row.rvol,
                benchmark_entry_price=self.benchmark_price,
                entry_headline=headlines.get(row.symbol),
                entry_headline_source=headline_sources.get(row.symbol),
                # Taken from the universe-wide bulk float map rather than
                # row.float_shares: the row's own copy is only populated once
                # the symbol has been in a ranked view long enough for the
                # per-symbol fundamentals fetch to land, so it's frequently
                # None at exactly the moment an appearance is first recorded.
                entry_float_shares=self.fundamentals.float_shares(row.symbol),
            )
            for view_name, row in candidates
        ]
        try:
            await self.history_store.record_appearances(new_entries)
        except Exception:
            logger.exception("Failed recording scanner appearances to history store")

    async def _write_periodic_snapshots(self) -> None:
        """Follow-up price check for symbols with a recent open appearance
        in the history store, throttled to
        settings.scanner_history_snapshot_interval -- same throttle pattern
        as _refresh_movers_backstop.
        """
        now = time.monotonic()
        if now - self._last_history_snapshot < self.settings.scanner_history_snapshot_interval:
            return
        self._last_history_snapshot = now

        prices = {symbol: row.last_price for symbol, row in self.rows.items()}
        if not prices:
            return
        try:
            await self.history_store.write_snapshots(prices, self.benchmark_price)
        except Exception:
            logger.exception("Failed writing periodic scanner history snapshots")

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

            can_poll = (
                self.session != "closed"
                and bool(self.universe)
                and self.clients.settings.has_credentials
                and not self.paused
            )
            has_backstop_data = bool(self.universe) and self.clients.settings.has_credentials and not self.paused

            # Not gated on can_poll -- see _refresh_movers_backstop for why
            # this needs to keep running while the market's closed too.
            new_symbols = (
                await self._refresh_movers_backstop() if has_backstop_data else {}
            )
            await self._refresh_split_ratios()
            await self._refresh_merger_actions()
            await self._refresh_market_conditions()
            await self._refresh_gex()

            if can_poll:
                interval = (
                    self.settings.scanner_poll_interval_premarket
                    if self.session == "premarket"
                    else self.settings.scanner_poll_interval_regular
                )
                await self._poll_once()
                await self._poll_benchmark()
                await self._poll_sector_etfs()
            else:
                interval = self.settings.scanner_poll_interval_regular
                if new_symbols:
                    # Live polling isn't running to pick these up on its
                    # own -- without this, a backstop-only mover found
                    # while the market's closed would sit in self.universe
                    # but never actually appear in any view.
                    await self._merge_backstop_into_fallback(new_symbols)

            # Runs even when markets are closed (can_poll False) -- otherwise
            # the closed-market fallback view (see is_latest_session_fallback)
            # would show blank float/market cap/short interest columns until
            # the next live poll, instead of the fallback's real fundamentals.
            views = self._build_views()
            if not self.paused:
                await self._record_new_appearances(views)
            await self._refresh_news_feed(views)
            if not self.paused:
                await self._write_periodic_snapshots()

            # History recording above deliberately runs first and only over the
            # four canonical views: scanner_history is what the drift report,
            # benchmark tracker and every backtest read, and letting an
            # arbitrary user screen write into it would corrupt that continuity.
            #
            # Momentum is attached BEFORE screens are evaluated, over every
            # tradable row rather than just the ranked ones.
            #
            # Screens used to be evaluated first, on the reasoning that their
            # matched rows could then be enriched in place. That is circular
            # for a screen that *filters* on an enriched field. Every poll
            # builds fresh ScannerRow objects (see _poll_once), so rvol_1h and
            # volume_surge start as None each tick; a screen evaluated before
            # enrichment therefore saw None, and apply_filters treats missing
            # as no-match. The Volume Accelerating preset showed its rows once
            # -- the immediate reply on subscribe, against rows still enriched
            # from the previous tick -- and then emptied on the first poll and
            # stayed empty, because matching nothing meant nothing was enriched
            # to match next time.
            #
            # Only momentum is widened, and only to the tradable set the screen
            # already runs against, so this cannot exceed the universe. News and
            # fundamentals stay scoped to ranked plus matched rows: those cost
            # FMP requests, and that quota is already the tightest budget here.
            #
            # Fundamentals are FETCHED here, before momentum and screens, and
            # merely attached later. row.tape_coverage_pct is set during
            # _attach_momentum and read off the row by any screen filtering on
            # it, so both need this tick's cache rather than last tick's --
            # without the prefetch it is null on a symbol's first appearance
            # and one tick stale forever after.
            #
            # Scoped to the ranked views only: those are exactly the symbols
            # _attach_fundamentals would fetch for anyway, so pulling the call
            # forward costs no additional FMP requests. Screen-matched rows
            # are not known yet and stay where they were, fetched by
            # _attach_fundamentals once _evaluate_screens has named them.
            await self.fundamentals.ensure_fresh(
                {r.symbol for rows in views.values() for r in rows}
            )

            momentum_scope = dict(views)
            if self.screen_subscriptions is not None and self.screen_subscriptions.any_active():
                momentum_scope["_screen_candidates"] = _tradable(
                    list(self.rows.values()) or list((self._latest_session_rows or {}).values()),
                    self.settings.scanner_min_dollar_volume,
                )
            await self._attach_momentum(momentum_scope)
            # After momentum: both read the same MomentumCache, and this
            # needs the bars that refresh just populated.
            await self._attach_strategies(views)

            screen_results = self._evaluate_screens()
            enriched = dict(views)
            if screen_results:
                # Rows are shared objects, so attaching to them here is what
                # populates the same rows in the screen payloads below --
                # which is how a custom screen gets company name, market cap
                # and short interest without widening those fetches to the
                # whole universe. Bounded by the screens' own limits.
                enriched["_screened"] = [
                    row for _, rows, _ in screen_results for row in rows
                ]

            await self._attach_fundamentals(enriched)
            await self._attach_news(enriched)
            await self._broadcast_screens(screen_results)
            is_fallback = self.is_latest_session_fallback
            for name, rows in views.items():
                await self.manager.broadcast(
                    f"scanner:{name}",
                    self.scanner_update_payload(name, rows, is_latest_session=is_fallback),
                )

            await asyncio.sleep(interval)
