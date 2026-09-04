"""Where the options service gets its prices from.

OptionsService touches market data in exactly four places -- the
underlying's spot, the expiry strip, one expiry's chain and fresh quotes
for specific contracts -- plus a clock (the dte on a preview, the "today"
curve of the risk chart). Those five things are a QuoteSource. The live
one wraps the ChainCache and the snapshot fetch the service always used;
the replay one (app.replay.options_engine) answers the same questions
from historical minute bars at the replay clock, with synthetic bid/ask
and an implied volatility solved from the bar close. Everything above
that seam -- leg resolution, net prices, risk, payoff, warnings -- runs
unchanged over either.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Awaitable, Callable, Protocol

from app.alpaca.client import AlpacaClients
from app.options.chain import Chain, ExpiryInfo, LegQuote
from app.options.chain_fetch import ChainCache, fetch_leg_quotes
from app.services.market_clock import ET

if TYPE_CHECKING:
    from app.replay.options_engine import ReplayOptionsEngine

SpotFn = Callable[[str], Awaitable[float | None]]


class QuoteSource(Protocol):
    """`feed` names the source on the chain and the account ("opra",
    "indicative", "replay"); `now()` is the moment prices are as of."""

    feed: str

    def now(self) -> datetime: ...

    async def spot(self, underlying: str) -> float | None: ...

    async def expiries(self, underlying: str) -> tuple[float, list[ExpiryInfo]]:
        """(spot, expiries). Raises LookupError when the underlying has no
        price or no contracts."""
        ...

    async def chain(self, underlying: str, expiry: date) -> Chain:
        """Raises LookupError for an expiry the strip does not offer."""
        ...

    async def leg_quotes(self, symbols: list[str]) -> dict[str, LegQuote]: ...


class LiveQuoteSource:
    """Alpaca now: the ChainCache for the strip and the chain, one
    snapshot call for specific contracts, the wall clock."""

    def __init__(self, clients: AlpacaClients, chain_cache: ChainCache, spot_fn: SpotFn | None = None) -> None:
        self._clients = clients
        self._chain_cache = chain_cache
        self._spot_fn = spot_fn or chain_cache.spot
        feed = getattr(clients, "options_feed", None)
        self.feed = str(getattr(feed, "value", feed)) if feed is not None else "opra"

    def now(self) -> datetime:
        return datetime.now(ET)

    async def spot(self, underlying: str) -> float | None:
        return await self._spot_fn(underlying.upper())

    async def expiries(self, underlying: str) -> tuple[float, list[ExpiryInfo]]:
        spot, _contracts, expiries = await self._chain_cache.contracts(underlying)
        return spot, expiries

    async def chain(self, underlying: str, expiry: date) -> Chain:
        return await self._chain_cache.chain(underlying, expiry)

    async def leg_quotes(self, symbols: list[str]) -> dict[str, LegQuote]:
        return await fetch_leg_quotes(self._clients, symbols)


class ReplayQuoteSource:
    """A past moment: the replay options engine's contracts and minute
    bars, read as of the replay clock. Thin -- the engine builds the
    synthetic quotes, this just fixes the moment."""

    feed = "replay"

    def __init__(self, engine: "ReplayOptionsEngine", as_of: datetime) -> None:
        self._engine = engine
        self._as_of = as_of

    @property
    def as_of(self) -> datetime:
        return self._as_of

    def now(self) -> datetime:
        return self._as_of.astimezone(ET)

    async def spot(self, underlying: str) -> float | None:
        return await self._engine.spot(underlying.upper(), self._as_of)

    async def expiries(self, underlying: str) -> tuple[float, list[ExpiryInfo]]:
        return await self._engine.expiries(underlying.upper(), self._as_of)

    async def chain(self, underlying: str, expiry: date) -> Chain:
        return await self._engine.chain(underlying.upper(), expiry, self._as_of)

    async def leg_quotes(self, symbols: list[str]) -> dict[str, LegQuote]:
        return await self._engine.leg_quotes(symbols, self._as_of)
