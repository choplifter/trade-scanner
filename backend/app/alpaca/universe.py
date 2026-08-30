import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from alpaca.data.requests import MarketMoversRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.enums import AssetClass, AssetStatus, CorporateActionType
from alpaca.trading.requests import GetAssetsRequest, GetCorporateAnnouncementsRequest

from app.alpaca.client import AlpacaClients
from app.core.config import Settings

logger = logging.getLogger(__name__)

# Major listing venues worth scanning. Alpaca reports OTC/other thin venues
# too, which we deliberately exclude -- they're illiquid and not what "stocks
# in play" scanning is about.
_ALLOWED_EXCHANGES = {"NASDAQ", "NYSE", "ARCA", "NYSEARCA", "AMEX", "BATS"}

# Plain equity tickers only (1-5 uppercase letters). Filters out warrants,
# units, and rights, which trade under suffixed symbols like "ABCW"/"ABC.WS"
# style identifiers and aren't relevant to day-trading scanners.
_PLAIN_TICKER_RE = re.compile(r"^[A-Z]{1,5}$")

# Alpaca's asset_class doesn't distinguish ETFs from individual stocks --
# both are just "us_equity" -- so ETFs are filtered out by a best-effort
# check on the asset's legal name instead. Verified against live Alpaca data
# for a sample of common leveraged/sector ETFs: "ETF" alone catches most
# (e.g. "iShares Russell 2000 ETF", "Direxion Daily ... 3X ETF"), but misses
# ProShares' Ultra/UltraPro line ("ProShares UltraPro QQQ" has no "ETF" in
# the name) and Invesco's QQQ ("Invesco QQQ Trust, Series 1") -- hence the
# extra markers. A day-trading stock scanner is about single-name catalysts
# (earnings, news); a leveraged/inverse ETF moves because its underlying
# index does, which is a different signal and just crowds out real setups.
_ETF_NAME_MARKERS = ("ETF", "PROSHARES", "TRUST, SERIES")


def _looks_like_etf(name: str | None) -> bool:
    if not name:
        return False
    upper = name.upper()
    return any(marker in upper for marker in _ETF_NAME_MARKERS)


# Alpaca's movers screener isn't filtered for security type -- warrants and
# rights show up alongside common stock (e.g. "ANSCW" as a top gainer), and
# their all-letter symbols pass _PLAIN_TICKER_RE, so they need a name check
# same as ETFs do above.
_WARRANT_NAME_MARKERS = ("WARRANT", "RIGHTS", " UNITS")


def _looks_like_warrant(name: str | None) -> bool:
    if not name:
        return False
    upper = name.upper()
    return any(marker in upper for marker in _WARRANT_NAME_MARKERS)


_BATCH_SIZE = 500
_MOVERS_TOP = 50


@dataclass(frozen=True)
class UniverseSymbol:
    symbol: str
    exchange: str
    prev_close: float
    avg_vol_20d: float
    avg_dollar_vol_20d: float
    # Whether the broker will let this be sold short at all. Roughly half the
    # scannable universe cannot be (measured: 4014 of 7229 candidates are
    # shortable), so it is worth knowing before building a short thesis on a
    # name that can only be bought.
    #
    # Alpaca also reports easy_to_borrow, deliberately not carried: measured
    # against the full active-asset list the two agree on all but a single
    # symbol (5276 shortable vs 5277 easy-to-borrow, and nothing shortable
    # that is not also borrowable), so a second field would say the same
    # thing twice. Note this is the broker's *static* flag, not a live
    # locate -- borrow can still fail at order time.
    shortable: bool = False


def _chunk(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


@dataclass(frozen=True)
class SymbolSuggestion:
    symbol: str
    # None only via the narrower build_universe fallback (see
    # routers.symbols.search_symbols) -- UniverseSymbol carries no name.
    name: str | None
    exchange: str


async def list_active_equity_symbols(clients: AlpacaClients) -> list[SymbolSuggestion]:
    """All plain-ticker, major-exchange active symbols -- including ETFs and
    anything outside the scanner's price/volume band -- for the watchlist's
    add-symbol autocomplete.

    build_universe's own result deliberately excludes both an ETF (see
    _looks_like_etf) and anything outside settings.universe_min/max_price:
    neither belongs in a day-trading scanner, but either is a completely
    normal thing to want on a personal watchlist (SPY, AAPL). A second
    get_all_assets call rather than reusing build_universe's own -- this
    runs once at startup same as that does, and keeps autocomplete from
    being coupled to the scanner's filtering pipeline.

    Carries name/exchange (not just the bare symbol) so the suggestion
    dropdown can show "Apple Inc. -- NASDAQ" next to "AAPL" -- disambiguating
    a ticker from memory alone is exactly what a search-as-you-type box is
    for.
    """
    assets = await asyncio.to_thread(
        clients.trading.get_all_assets,
        GetAssetsRequest(asset_class=AssetClass.US_EQUITY, status=AssetStatus.ACTIVE),
    )
    return sorted(
        (
            SymbolSuggestion(symbol=a.symbol, name=a.name, exchange=a.exchange.value)
            for a in assets
            if a.tradable and a.exchange.value in _ALLOWED_EXCHANGES and _PLAIN_TICKER_RE.match(a.symbol)
        ),
        key=lambda s: s.symbol,
    )


async def build_universe(
    clients: AlpacaClients, settings: Settings
) -> dict[str, UniverseSymbol]:
    """Build the pool of symbols scanners are allowed to consider.

    Two passes: (1) list all tradable US equities and cheaply filter by
    exchange/ticker shape, (2) pull 20 daily bars per candidate to compute
    average volume/dollar-volume, then filter by price range and liquidity
    and cap the result so the scanner engine's snapshot-polling loop stays
    within Alpaca's rate limits.
    """
    # alpaca-py is a blocking/requests-based SDK -- offload to a thread so
    # this startup call doesn't stall the event loop for other requests.
    assets = await asyncio.to_thread(
        clients.trading.get_all_assets,
        GetAssetsRequest(asset_class=AssetClass.US_EQUITY, status=AssetStatus.ACTIVE),
    )

    candidates = [
        a.symbol
        for a in assets
        if a.tradable
        and a.exchange.value in _ALLOWED_EXCHANGES
        and _PLAIN_TICKER_RE.match(a.symbol)
        and not _looks_like_etf(a.name)
    ]
    exchange_by_symbol = {a.symbol: a.exchange.value for a in assets}
    shortable_by_symbol = {a.symbol: bool(a.shortable) for a in assets}
    logger.info("Universe candidates after exchange/ticker filter: %d", len(candidates))

    start = datetime.now(timezone.utc) - timedelta(days=40)
    universe: dict[str, UniverseSymbol] = {}

    for batch in _chunk(candidates, _BATCH_SIZE):
        try:
            bar_set = await asyncio.to_thread(
                clients.data.get_stock_bars,
                StockBarsRequest(
                    symbol_or_symbols=batch,
                    timeframe=TimeFrame.Day,
                    start=start,
                    limit=20 * len(batch),
                    feed=clients.feed,
                ),
            )
        except Exception:
            logger.exception("Failed fetching daily bars for a universe batch, skipping it")
            continue

        for symbol, bars in bar_set.data.items():
            if not bars:
                continue
            prev_close = bars[-1].close
            if prev_close <= 0:
                continue
            avg_vol_20d = sum(b.volume for b in bars) / len(bars)
            avg_dollar_vol_20d = avg_vol_20d * prev_close

            if not (settings.universe_min_price <= prev_close <= settings.universe_max_price):
                continue
            if avg_vol_20d < settings.universe_min_avg_volume:
                continue

            universe[symbol] = UniverseSymbol(
                symbol=symbol,
                exchange=exchange_by_symbol.get(symbol, ""),
                prev_close=prev_close,
                avg_vol_20d=avg_vol_20d,
                avg_dollar_vol_20d=avg_dollar_vol_20d,
                # Missing from the asset list means "not known to be
                # shortable", and the conservative reading of that is False:
                # the marker this drives must never claim a borrow that was
                # never confirmed.
                shortable=shortable_by_symbol.get(symbol, False),
            )

    ranked = sorted(universe.values(), key=lambda u: u.avg_dollar_vol_20d, reverse=True)
    ranked = ranked[: settings.max_universe_size]
    result = {u.symbol: u for u in ranked}

    logger.info("Universe built: %d symbols (capped at %d)", len(result), settings.max_universe_size)
    return result


async def fetch_movers_backstop(
    clients: AlpacaClients, settings: Settings, existing: dict[str, UniverseSymbol]
) -> dict[str, UniverseSymbol]:
    """Catch today's runners that build_universe()'s trailing-volume filter
    structurally can't see.

    build_universe() only admits symbols with strong *trailing* 20-day
    average volume, so a normally-quiet stock that explodes today on a
    catalyst -- the classic day-trading setup -- never enters the polled
    universe, no matter how big it moves. Alpaca's movers screener reflects
    the live tape instead of trailing history, so calling this periodically
    (see ScannerEngine.run_loop) surfaces those names mid-day. Candidates go
    through the same price/exchange/ticker-shape/ETF filters as
    build_universe(), plus a warrant/rights name check the screener doesn't
    apply itself.
    """
    try:
        movers = await asyncio.to_thread(
            clients.screener.get_market_movers, MarketMoversRequest(top=_MOVERS_TOP)
        )
    except Exception:
        logger.exception("Movers backstop request failed")
        return {}

    candidates = {
        m.symbol: m
        for m in (*movers.gainers, *movers.losers)
        if m.symbol not in existing
        and _PLAIN_TICKER_RE.match(m.symbol)
        and settings.universe_min_price <= m.price <= settings.universe_max_price
    }
    if not candidates:
        return {}

    # symbol -> (exchange, shortable), both read off the same asset lookup.
    qualified: dict[str, tuple[str, bool]] = {}
    for symbol in candidates:
        try:
            asset = await asyncio.to_thread(clients.trading.get_asset, symbol)
        except Exception:
            continue
        if (
            asset.tradable
            and asset.exchange.value in _ALLOWED_EXCHANGES
            and not _looks_like_etf(asset.name)
            and not _looks_like_warrant(asset.name)
        ):
            qualified[symbol] = (asset.exchange.value, bool(asset.shortable))

    if not qualified:
        return {}

    start = datetime.now(timezone.utc) - timedelta(days=40)
    try:
        bar_set = await asyncio.to_thread(
            clients.data.get_stock_bars,
            StockBarsRequest(
                symbol_or_symbols=list(qualified),
                timeframe=TimeFrame.Day,
                start=start,
                limit=20 * len(qualified),
                feed=clients.feed,
            ),
        )
    except Exception:
        logger.exception("Movers backstop bar fetch failed")
        return {}

    result: dict[str, UniverseSymbol] = {}
    for symbol, (exchange, shortable) in qualified.items():
        mover = candidates[symbol]
        bars = bar_set.data.get(symbol, [])
        # New listings may have little or no daily-bar history yet -- fall
        # back to 0 rather than skipping, since the mover itself is still a
        # legitimate signal even without an rvol baseline (rvol just reads
        # as 0 downstream, see formulas.rvol).
        avg_vol_20d = sum(b.volume for b in bars) / len(bars) if bars else 0.0
        # Prefer the real daily-bar close over mover.price - mover.change:
        # the screener's own price/change fields can be stale for the thin,
        # normally-quiet names this backstop specifically exists to catch
        # (see this function's docstring), and a stale value here silently
        # becomes ScannerRow.last_price via formulas.resolve_last_price's
        # last-resort fallback to prev_close whenever the live snapshot
        # hasn't printed a trade or daily bar for the symbol yet -- observed
        # in practice as implausible 10-40x "moves" once a real trade
        # finally came through on a later poll. Only fall back to the
        # screener's own math when there's no bar history at all (a listing
        # too new for build_universe's 40-day window either).
        prev_close = bars[-1].close if bars else (mover.price - mover.change)
        result[symbol] = UniverseSymbol(
            symbol=symbol,
            exchange=exchange,
            prev_close=prev_close,
            avg_vol_20d=avg_vol_20d,
            avg_dollar_vol_20d=avg_vol_20d * mover.price,
            shortable=shortable,
        )

    logger.info("Movers backstop qualified %d new symbol(s): %s", len(result), sorted(result))
    return result


async def fetch_split_ratios(clients: AlpacaClients, lookback_days: int = 7) -> dict[str, tuple[float, date]]:
    """Symbol -> (multiplier, ex_date) to rescale a stale prev_close so
    it's comparable to today's price, for every stock split in the last
    `lookback_days` days.

    Alpaca's snapshot endpoint (StockSnapshotRequest, what the live poll
    loop uses for previous_daily_bar.close) has no split-adjustment option
    -- unlike the historical bars endpoint's `adjustment` param, there's no
    way to ask it for an already-adjusted prev_close. Observed in practice:
    a stock that reverse-splits shows a nonsensical multi-thousand-percent
    "gap" (e.g. TNON's 2026-08-10 1-for-35 reverse split: prev_close
    $0.1316 vs. a real price near $6 -- a +4600% "move" that's actually
    close to flat once rescaled: $0.1316 * 35 = ~$4.61) for as long as
    prev_close still comes from a session before the split.

    The caller (ScannerEngine._compute_rows) is the one that decides
    *whether* a given ratio actually applies to a given prev_close, by
    comparing the returned ex_date against that snapshot's own
    previous_daily_bar date -- not this function, and deliberately not
    keyed to "today": the market can be closed for hours (evenings,
    weekends) while the calendar date has already rolled over, during
    which previous_daily_bar still lags the split by more than a single
    calendar day. A week-wide lookback comfortably covers that gap
    (weekends, holidays, and this cache's own refresh cadence) without the
    caller needing to special-case any of it -- it just needs to know each
    split's ex_date, in a window definitely wider than any believable
    gap between two consecutive sessions.

    old_rate/new_rate is the correct multiplier for both directions: a
    2-for-1 forward split (old_rate=1, new_rate=2) halves prev_close
    (1/2 = 0.5); a 1-for-35 reverse split (old_rate=35, new_rate=1)
    multiplies it by 35.

    One market-wide query (not per-symbol) -- splits are rare enough
    across the whole market (order of ~10/day) that this is cheap
    regardless of universe size, unlike a per-symbol lookup would be.
    """
    today = datetime.now(timezone.utc).date()
    try:
        request = GetCorporateAnnouncementsRequest(
            ca_types=[CorporateActionType.SPLIT],
            since=today - timedelta(days=lookback_days),
            until=today,
        )
        announcements = await asyncio.to_thread(clients.trading.get_corporate_announcements, request)
    except Exception:
        logger.exception("Corporate announcements (split) fetch failed")
        return {}

    ratios: dict[str, tuple[float, date]] = {}
    for a in announcements:
        if not a.target_symbol or not a.ex_date or not a.old_rate or not a.new_rate:
            continue
        ratios[a.target_symbol] = (a.old_rate / a.new_rate, a.ex_date)

    if ratios:
        logger.info("Split adjustments (last %d days): %s", lookback_days, ratios)
    return ratios
