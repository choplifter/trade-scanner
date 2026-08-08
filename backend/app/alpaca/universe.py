import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from alpaca.data.requests import MarketMoversRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.enums import AssetClass, AssetStatus
from alpaca.trading.requests import GetAssetsRequest

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


def _chunk(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


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

    qualified: dict[str, str] = {}
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
            qualified[symbol] = asset.exchange.value

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
    for symbol, exchange in qualified.items():
        mover = candidates[symbol]
        bars = bar_set.data.get(symbol, [])
        # New listings may have little or no daily-bar history yet -- fall
        # back to 0 rather than skipping, since the mover itself is still a
        # legitimate signal even without an rvol baseline (rvol just reads
        # as 0 downstream, see formulas.rvol).
        avg_vol_20d = sum(b.volume for b in bars) / len(bars) if bars else 0.0
        result[symbol] = UniverseSymbol(
            symbol=symbol,
            exchange=exchange,
            prev_close=mover.price - mover.change,
            avg_vol_20d=avg_vol_20d,
            avg_dollar_vol_20d=avg_vol_20d * mover.price,
        )

    logger.info("Movers backstop qualified %d new symbol(s): %s", len(result), sorted(result))
    return result
