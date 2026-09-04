"""Option data for a history replay: the contracts that existed on the
replayed day and their 1-minute bars, answerable at any as_of moment --
the counterpart to app.replay.engine.ReplayEngine for the option chain.

What Alpaca keeps of the past for options is trades and bars (from
February 2024), for expired contracts too. What it does not keep is the
NBBO, implied volatility, greeks and the day's open interest. So a
replayed chain is built from the last minute bar at or before as_of:

- the price is the bar's close (the last print), with a synthetic bid/ask
  a slippage either side (see option_slippage) so the same "natural"
  arithmetic the live ticket uses gives the replay fill price;
- implied volatility is solved from that close (app.options.payoff
  .implied_vol), and delta/gamma/theta follow from it;
- open interest is 0 (the contracts endpoint carries only the final
  figure, which would be look-ahead), the bar's own time travels with the
  quote as `last_at` so a stale print on an illiquid strike is visible.

Bars are loaded lazily per (underlying, day, expiry) -- one multi-symbol
request per expiry the user opens -- and cached on disk by
app.scanners.bar_cache.get_cached_option_minute_bars, since a finished
session's bars never change. Held in memory per user for the life of the
session, exactly as ReplayEngine's stock bars are.
"""

import asyncio
import logging
from bisect import bisect_right
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable

from alpaca.trading.enums import AssetStatus
from alpaca.trading.requests import GetOptionContractsRequest

from app.alpaca.client import AlpacaClients
from app.options.chain import Chain, ContractMeta, ExpiryInfo, LegQuote, StrikeRow, expiries_from_contracts
from app.options.chain_fetch import (
    _CONTRACTS_PAGE_LIMIT,
    _MAX_CONTRACT_PAGES,
    CHAIN_DAYS_AHEAD,
    STRIKE_PCT_RANGE,
    _contract_meta,
)
from app.options.occ import Kind, try_parse_occ
from app.options.payoff import bs_greeks, implied_vol, years_between
from app.options.pricing import option_slippage
from app.scanners.bar_cache import DEFAULT_CACHE_DIR, get_cached_5m_bars_multi, get_cached_option_minute_bars
from app.services.market_clock import ET

logger = logging.getLogger(__name__)

# A print older than this at as_of is flagged as stale on the chain.
STALE_AFTER = timedelta(minutes=30)

# Options trade 09:30-16:00 ET; the extra quarter hour catches late prints.
_SESSION_OPEN = time(9, 30)
_SESSION_END = time(16, 15)
_SPOT_LOOKBACK_PADDING_DAYS = 3


@dataclass(frozen=True)
class BarQuote:
    """The last minute bar at or before a moment: its close, when it
    printed, and how many contracts traded in it."""

    close: float
    at: datetime
    volume: float


def day_of(as_of: datetime) -> date:
    """The ET trading day a moment belongs to."""
    return as_of.astimezone(ET).date()


def session_window(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, _SESSION_OPEN, tzinfo=ET).astimezone(timezone.utc)
    end = datetime.combine(day, _SESSION_END, tzinfo=ET).astimezone(timezone.utc)
    return start, end


def synthetic_quote(
    symbol: str,
    strike: float,
    kind: Kind,
    expiry: date,
    bar: BarQuote | None,
    spot: float | None,
    as_of: datetime,
) -> LegQuote:
    """A LegQuote from a bar close: bid/ask a slippage either side, mid
    and last the close itself, IV solved from the close against `spot`,
    greeks from that IV. Empty (no market) without a bar."""
    if bar is None or bar.close <= 0:
        return LegQuote(
            symbol=symbol, strike=strike, kind=kind, expiry=expiry,
            bid=None, ask=None, mid=None, last=None, bid_size=None, ask_size=None,
            delta=None, gamma=None, theta=None, iv=None, open_interest=0, tradable=True, last_at=None,
        )
    slip = option_slippage(bar.close)
    iv = delta = gamma = theta = None
    if spot is not None and spot > 0:
        years = years_between(as_of, expiry)
        iv = implied_vol(kind, bar.close, spot, strike, years)
        if iv is not None:
            delta, gamma, theta = bs_greeks(kind, spot, strike, years, iv)
    return LegQuote(
        symbol=symbol,
        strike=strike,
        kind=kind,
        expiry=expiry,
        # In cents, like a real option quote -- so the natural the ticket
        # shows is exactly the price the book fills at.
        bid=round(max(0.01, bar.close - slip), 2),
        ask=round(bar.close + slip, 2),
        mid=round(bar.close, 4),
        last=round(bar.close, 4),
        bid_size=None,
        ask_size=None,
        delta=delta,
        gamma=gamma,
        theta=theta,
        iv=iv,
        open_interest=0,
        tradable=True,
        last_at=bar.at,
    )


ContractsFn = Callable[[str, AssetStatus, date, date, float, float], Awaitable[list[ContractMeta]]]
BarsFn = Callable[[list[str], datetime, datetime], Awaitable[dict[str, list]]]
UnderlyingBarsFn = Callable[[str, int], Awaitable[list]]
SpotFn = Callable[[str, datetime], Awaitable[float | None]]


class ReplayOptionsEngine:
    """One user's replayed option universe. `spot_fn` is the session's own
    stock engine (ReplayEngine.reference_price) when the underlying is part
    of the session; otherwise the engine fetches the underlying's own
    5-minute bars for the day. The *_fn hooks exist so tests can hand in
    synthetic contracts and bars without a client."""

    def __init__(
        self,
        clients: AlpacaClients | None,
        *,
        spot_fn: SpotFn | None = None,
        cache_dir: Path = DEFAULT_CACHE_DIR,
        contracts_fn: ContractsFn | None = None,
        bars_fn: BarsFn | None = None,
        underlying_bars_fn: UnderlyingBarsFn | None = None,
        today_fn: Callable[[], date] | None = None,
    ) -> None:
        self._clients = clients
        self._spot_fn = spot_fn
        self._cache_dir = cache_dir
        self._contracts_fn = contracts_fn or self._fetch_contracts
        self._bars_fn = bars_fn or self._fetch_bars
        self._underlying_bars_fn = underlying_bars_fn or self._fetch_underlying_bars
        self._today = today_fn or (lambda: datetime.now(ET).date())
        self._contracts: dict[tuple[str, date], tuple[dict[str, ContractMeta], list[ExpiryInfo]]] = {}
        self._series: dict[str, tuple[list[datetime], list]] = {}
        self._loaded: set[tuple[str, date]] = set()
        self._underlying: dict[tuple[str, date], tuple[list[datetime], list]] = {}
        self._locks: dict[object, asyncio.Lock] = {}

    def _lock(self, key) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = self._locks[key] = asyncio.Lock()
        return lock

    # --- Alpaca ---------------------------------------------------------------

    async def _fetch_contracts(
        self, underlying: str, status: AssetStatus, gte: date, lte: date, strike_gte: float, strike_lte: float
    ) -> list[ContractMeta]:
        assert self._clients is not None
        out: list[ContractMeta] = []
        page_token: str | None = None
        for _ in range(_MAX_CONTRACT_PAGES):
            request = GetOptionContractsRequest(
                underlying_symbols=[underlying],
                status=status,
                expiration_date_gte=gte.isoformat(),
                expiration_date_lte=lte.isoformat(),
                strike_price_gte=str(strike_gte),
                strike_price_lte=str(strike_lte),
                limit=_CONTRACTS_PAGE_LIMIT,
                page_token=page_token,
            )
            page = await asyncio.to_thread(self._clients.trading.get_option_contracts, request)
            for contract in page.option_contracts or []:
                meta = _contract_meta(contract)
                if meta is not None:
                    out.append(meta)
            page_token = page.next_page_token
            if not page_token:
                break
        return out

    async def _fetch_bars(self, symbols: list[str], start: datetime, end: datetime) -> dict[str, list]:
        assert self._clients is not None
        return await get_cached_option_minute_bars(self._clients, symbols, start, end, cache_dir=self._cache_dir)

    async def _fetch_underlying_bars(self, underlying: str, lookback_days: int) -> list:
        assert self._clients is not None
        bars = await get_cached_5m_bars_multi(self._clients, [underlying], lookback_days, cache_dir=self._cache_dir)
        return bars.get(underlying) or []

    # --- the underlying ---------------------------------------------------------

    async def _underlying_bars(self, underlying: str, day: date) -> tuple[list[datetime], list]:
        key = (underlying, day)
        async with self._lock(("underlying", key)):
            cached = self._underlying.get(key)
            if cached is not None:
                return cached
            lookback = max((self._today() - day).days + _SPOT_LOOKBACK_PADDING_DAYS, _SPOT_LOOKBACK_PADDING_DAYS)
            bars = sorted(await self._underlying_bars_fn(underlying, lookback), key=lambda b: b.timestamp)
            value = ([b.timestamp for b in bars], bars)
            self._underlying[key] = value
            return value

    async def spot(self, underlying: str, as_of: datetime) -> float | None:
        """The underlying's price at `as_of`: the session's stock engine
        when it has the symbol, the underlying's own bars otherwise."""
        if self._spot_fn is not None:
            try:
                price = await self._spot_fn(underlying, as_of)
            except Exception:
                logger.debug("Replay spot lookup failed for %s", underlying, exc_info=True)
                price = None
            if price:
                return float(price)
        times, bars = await self._underlying_bars(underlying, day_of(as_of))
        idx = bisect_right(times, as_of) - 1
        if idx < 0:
            return None
        return float(bars[idx].close)

    async def day_range(self, underlying: str, day: date) -> tuple[float, float] | None:
        """(low, high) of the underlying over the replayed day -- the strike
        band the contracts are fetched for."""
        _times, bars = await self._underlying_bars(underlying, day)
        lows = [float(b.low) for b in bars if day_of(b.timestamp) == day]
        highs = [float(b.high) for b in bars if day_of(b.timestamp) == day]
        if not lows:
            return None
        return min(lows), max(highs)

    # --- contracts --------------------------------------------------------------

    async def contracts(self, underlying: str, day: date) -> tuple[dict[str, ContractMeta], list[ExpiryInfo]]:
        """The contracts that existed on `day` within the picker's window
        (CHAIN_DAYS_AHEAD from that day, strikes around the day's range),
        and the expiry strip as it read that day. Expiries already past
        today are fetched as inactive contracts, the rest as active."""
        underlying = underlying.upper()
        key = (underlying, day)
        async with self._lock(("contracts", key)):
            cached = self._contracts.get(key)
            if cached is not None:
                return cached
            rng = await self.day_range(underlying, day)
            if rng is None:
                raise LookupError(f"No price history for {underlying} on {day.isoformat()}")
            low, high = rng
            strike_lo = round(low * (1 - STRIKE_PCT_RANGE), 2)
            strike_hi = round(high * (1 + STRIKE_PCT_RANGE), 2)
            today = self._today()
            exp_hi = day + timedelta(days=CHAIN_DAYS_AHEAD)
            metas: dict[str, ContractMeta] = {}
            if day <= today - timedelta(days=1):
                for meta in await self._contracts_fn(
                    underlying, AssetStatus.INACTIVE, day, min(exp_hi, today - timedelta(days=1)), strike_lo, strike_hi
                ):
                    metas[meta.symbol] = meta
            if exp_hi >= today:
                for meta in await self._contracts_fn(
                    underlying, AssetStatus.ACTIVE, max(day, today), exp_hi, strike_lo, strike_hi
                ):
                    metas[meta.symbol] = meta
            # The final open interest of an expired contract is look-ahead;
            # nothing about the replayed day is known from it.
            metas = {symbol: replace(meta, open_interest=0, tradable=True) for symbol, meta in metas.items()}
            expiries = expiries_from_contracts(metas.values(), today=day)
            value = (metas, expiries)
            self._contracts[key] = value
            return value

    async def expiries(self, underlying: str, as_of: datetime) -> tuple[float, list[ExpiryInfo]]:
        underlying = underlying.upper()
        _contracts, expiries = await self.contracts(underlying, day_of(as_of))
        spot = await self.spot(underlying, as_of)
        if spot is None or spot <= 0:
            raise LookupError(f"No price for {underlying} at {as_of.isoformat()}")
        return spot, expiries

    # --- bars -----------------------------------------------------------------

    async def ensure_symbols(self, symbols: list[str], day: date) -> None:
        """Load the day's minute bars for these contracts if not held yet --
        one request for all of them."""
        missing = sorted({s for s in symbols if (s, day) not in self._loaded})
        if not missing:
            return
        async with self._lock(("bars", day)):
            missing = [s for s in missing if (s, day) not in self._loaded]
            if not missing:
                return
            start, end = session_window(day)
            fetched = await self._bars_fn(missing, start, end)
            for symbol in missing:
                self._loaded.add((symbol, day))
                new_bars = fetched.get(symbol) or []
                if not new_bars:
                    continue
                _times, existing = self._series.get(symbol, ([], []))
                by_time = {b.timestamp: b for b in existing}
                for bar in new_bars:
                    by_time[bar.timestamp] = bar
                bars = [by_time[t] for t in sorted(by_time)]
                self._series[symbol] = ([b.timestamp for b in bars], bars)

    async def ensure_bars(self, underlying: str, day: date, expiry: date) -> list[str]:
        """The day's bars for every contract of one expiry; returns their
        symbols. LookupError for an expiry the strip does not offer."""
        contracts, _expiries = await self.contracts(underlying, day)
        symbols = [meta.symbol for meta in contracts.values() if meta.expiry == expiry]
        if not symbols:
            raise LookupError(
                f"{underlying.upper()} has no {expiry.isoformat()} expiry within {CHAIN_DAYS_AHEAD} days of {day.isoformat()}"
            )
        await self.ensure_symbols(symbols, day)
        return symbols

    def quote_at(self, symbol: str, as_of: datetime) -> BarQuote | None:
        """The last minute bar at or before `as_of` (same rule as
        ReplayEngine.bars_up_to: a bar stamped at as_of counts)."""
        series = self._series.get(symbol)
        if series is None:
            return None
        times, bars = series
        idx = bisect_right(times, as_of) - 1
        if idx < 0:
            return None
        bar = bars[idx]
        return BarQuote(close=float(bar.close), at=bar.timestamp, volume=float(bar.volume or 0))

    def bars_up_to(self, symbol: str, as_of: datetime) -> list:
        series = self._series.get(symbol)
        if series is None:
            return []
        times, bars = series
        return bars[: bisect_right(times, as_of)]

    # --- the chain ------------------------------------------------------------

    async def chain(self, underlying: str, expiry: date, as_of: datetime) -> Chain:
        underlying = underlying.upper()
        day = day_of(as_of)
        contracts, _expiries = await self.contracts(underlying, day)
        await self.ensure_bars(underlying, day, expiry)
        spot = await self.spot(underlying, as_of)
        if spot is None or spot <= 0:
            raise LookupError(f"No price for {underlying} at {as_of.isoformat()}")
        by_strike: dict[float, dict[str, LegQuote]] = {}
        for meta in contracts.values():
            if meta.expiry != expiry:
                continue
            quote = synthetic_quote(
                meta.symbol, meta.strike, meta.kind, meta.expiry, self.quote_at(meta.symbol, as_of), spot, as_of
            )
            by_strike.setdefault(meta.strike, {})[meta.kind] = quote
        rows = [
            StrikeRow(strike=strike, call=sides.get("call"), put=sides.get("put"))
            for strike, sides in sorted(by_strike.items())
        ]
        return Chain(underlying=underlying, expiry=expiry, spot=spot, feed="replay", as_of=as_of, rows=rows)

    async def leg_quotes(self, symbols: list[str], as_of: datetime) -> dict[str, LegQuote]:
        """Quotes for specific contracts at `as_of`, loading their day's bars
        on demand -- a held position outside the picker's strike band still
        prices."""
        day = day_of(as_of)
        parsed = {p.symbol: p for symbol in symbols if (p := try_parse_occ(symbol)) is not None}
        if not parsed:
            return {}
        await self.ensure_symbols(list(parsed), day)
        spots: dict[str, float | None] = {}
        out: dict[str, LegQuote] = {}
        for symbol, occ in parsed.items():
            if occ.underlying not in spots:
                spots[occ.underlying] = await self.spot(occ.underlying, as_of)
            out[symbol] = synthetic_quote(
                symbol, occ.strike, occ.kind, occ.expiry, self.quote_at(symbol, as_of), spots[occ.underlying], as_of
            )
        return out


class ReplayOptionsEngineCache:
    """Per-user, in-process, like ReplayEngineCache -- discarded with the
    session (routers/replay.py's /stop) and rebuilt lazily after a restart."""

    def __init__(self) -> None:
        self._by_user: dict[int, ReplayOptionsEngine] = {}

    def get(self, user_id: int) -> ReplayOptionsEngine | None:
        return self._by_user.get(user_id)

    def put(self, user_id: int, engine: ReplayOptionsEngine) -> None:
        self._by_user[user_id] = engine

    def discard(self, user_id: int) -> None:
        self._by_user.pop(user_id, None)
