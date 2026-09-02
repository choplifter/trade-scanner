"""Fetching option chains from Alpaca, and the short-lived cache in front.

Two sources, same as app.market_data.gamma_exposure (which this mirrors):
the trading API's contracts endpoint for identity, open interest and
tradability, and the options data API's chain endpoint for quotes and
greeks. Both always go through the primary (paper) keys -- market data
has no per-account split, only orders do.

The cache exists because the picker polls: every open Options widget
asks for its chain every 15 seconds, and two widgets on the same symbol
must not double the calls. Contracts change once a day at most, so they
are kept longer than quotes.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Awaitable, Callable

from alpaca.data.requests import OptionChainRequest, OptionSnapshotRequest
from alpaca.trading.enums import ContractType
from alpaca.trading.requests import GetOptionContractsRequest

from app.alpaca.client import AlpacaClients
from app.options.chain import (
    Chain,
    ContractMeta,
    ExpiryInfo,
    LegQuote,
    build_chain_rows,
    expiries_from_contracts,
    quote_from_snapshot,
)
from app.options.occ import try_parse_occ

logger = logging.getLogger(__name__)

# How far out the picker offers expiries, and how far either side of spot
# the strike band reaches. ±10% of spot is ~40 strikes a side on SPY, a
# full screen; single names with $1 strikes are bounded the same way.
CHAIN_DAYS_AHEAD = 60
STRIKE_PCT_RANGE = 0.10
_CONTRACTS_PAGE_LIMIT = 1000
_MAX_CONTRACT_PAGES = 20

CHAIN_TTL_SECONDS = 15.0
CONTRACTS_TTL_SECONDS = 300.0


def _contract_meta(contract) -> ContractMeta | None:
    parsed = try_parse_occ(getattr(contract, "symbol", None))
    kind = "call" if contract.type == ContractType.CALL else "put"
    expiry = contract.expiration_date
    if isinstance(expiry, str):
        expiry = date.fromisoformat(expiry)
    strike = float(contract.strike_price)
    close_price = getattr(contract, "close_price", None)
    return ContractMeta(
        symbol=contract.symbol,
        underlying=getattr(contract, "underlying_symbol", None) or (parsed.underlying if parsed else ""),
        root=getattr(contract, "root_symbol", None) or (parsed.root if parsed else ""),
        expiry=expiry,
        kind=kind,
        strike=strike,
        open_interest=int(contract.open_interest or 0),
        tradable=bool(getattr(contract, "tradable", True)),
        close_price=float(close_price) if close_price not in (None, "") else None,
    )


async def fetch_contracts(
    clients: AlpacaClients,
    underlying: str,
    expiration_gte: date,
    expiration_lte: date,
    strike_gte: float,
    strike_lte: float,
) -> dict[str, ContractMeta]:
    """Every contract in the window, keyed by symbol. Strike bounds go as
    strings: GetOptionContractsRequest rejects numbers with a pydantic
    string_type error (unlike OptionChainRequest) -- see gamma_exposure."""
    contracts: dict[str, ContractMeta] = {}
    page_token: str | None = None
    for _ in range(_MAX_CONTRACT_PAGES):
        request = GetOptionContractsRequest(
            underlying_symbols=[underlying],
            expiration_date_gte=expiration_gte.isoformat(),
            expiration_date_lte=expiration_lte.isoformat(),
            strike_price_gte=str(strike_gte),
            strike_price_lte=str(strike_lte),
            limit=_CONTRACTS_PAGE_LIMIT,
            page_token=page_token,
        )
        page = await asyncio.to_thread(clients.trading.get_option_contracts, request)
        for contract in page.option_contracts or []:
            meta = _contract_meta(contract)
            if meta is not None:
                contracts[meta.symbol] = meta
        page_token = page.next_page_token
        if not page_token:
            break
    return contracts


async def fetch_snapshots(
    clients: AlpacaClients, underlying: str, expiry: date, strike_gte: float, strike_lte: float
) -> dict:
    """symbol -> OptionsSnapshot for one expiry. get_option_chain paginates
    internally, so the strike band is what bounds the call."""
    request = OptionChainRequest(
        underlying_symbol=underlying,
        feed=clients.options_feed,
        expiration_date=expiry.isoformat(),
        strike_price_gte=strike_gte,
        strike_price_lte=strike_lte,
    )
    return await asyncio.to_thread(clients.options.get_option_chain, request)


async def fetch_leg_quotes(clients: AlpacaClients, symbols: list[str]) -> dict[str, LegQuote]:
    """Fresh quotes for specific contracts -- what a preview or close prices
    from, so it never depends on the picker's cached chain. Open interest is
    unknown here (the snapshot does not carry it) and reads as 0."""
    if not symbols:
        return {}
    request = OptionSnapshotRequest(symbol_or_symbols=symbols, feed=clients.options_feed)
    snapshots = await asyncio.to_thread(clients.options.get_option_snapshot, request)
    out: dict[str, LegQuote] = {}
    for symbol in symbols:
        parsed = try_parse_occ(symbol)
        if parsed is None:
            continue
        meta = ContractMeta(
            symbol=parsed.symbol,
            underlying=parsed.underlying,
            root=parsed.root,
            expiry=parsed.expiry,
            kind=parsed.kind,
            strike=parsed.strike,
            open_interest=0,
            tradable=True,
            close_price=None,
        )
        out[parsed.symbol] = quote_from_snapshot(meta, snapshots.get(symbol))
    return out


SpotFn = Callable[[str], Awaitable[float | None]]


@dataclass
class _Entry:
    fetched_at: float
    value: object


class ChainCache:
    """TTL cache over the two fetches, one asyncio.Lock per key so a burst
    of identical requests (several widgets, one symbol) fetches once."""

    def __init__(self, clients: AlpacaClients, spot_fn: SpotFn, *, now=time.monotonic) -> None:
        self._clients = clients
        self._spot_fn = spot_fn
        self._now = now
        self._contracts: dict[str, _Entry] = {}
        self._chains: dict[tuple[str, str], _Entry] = {}
        self._locks: dict[object, asyncio.Lock] = {}

    def _lock(self, key) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = self._locks[key] = asyncio.Lock()
        return lock

    def invalidate(self, underlying: str) -> None:
        self._contracts.pop(underlying, None)
        for key in [k for k in self._chains if k[0] == underlying]:
            self._chains.pop(key, None)

    async def contracts(self, underlying: str) -> tuple[float, dict[str, ContractMeta], list[ExpiryInfo]]:
        """(spot, contracts in the window, expiries) -- the picker's expiry
        strip and the strike band the chain fetch is bounded to."""
        underlying = underlying.upper()
        async with self._lock(("contracts", underlying)):
            entry = self._contracts.get(underlying)
            if entry is not None and self._now() - entry.fetched_at < CONTRACTS_TTL_SECONDS:
                return entry.value  # type: ignore[return-value]
            spot = await self._spot_fn(underlying)
            if spot is None or spot <= 0:
                raise LookupError(f"No price for {underlying}")
            today = datetime.now(timezone.utc).date()
            contracts = await fetch_contracts(
                self._clients,
                underlying,
                today,
                today + timedelta(days=CHAIN_DAYS_AHEAD),
                round(spot * (1 - STRIKE_PCT_RANGE), 2),
                round(spot * (1 + STRIKE_PCT_RANGE), 2),
            )
            expiries = expiries_from_contracts(contracts.values(), today)
            value = (spot, contracts, expiries)
            self._contracts[underlying] = _Entry(self._now(), value)
            return value

    async def chain(self, underlying: str, expiry: date) -> Chain:
        underlying = underlying.upper()
        key = (underlying, expiry.isoformat())
        async with self._lock(("chain", key)):
            entry = self._chains.get(key)
            if entry is not None and self._now() - entry.fetched_at < CHAIN_TTL_SECONDS:
                return entry.value  # type: ignore[return-value]
            spot, contracts, expiries = await self.contracts(underlying)
            if not any(e.expiry == expiry for e in expiries):
                raise LookupError(f"{underlying} has no {expiry.isoformat()} expiry within {CHAIN_DAYS_AHEAD} days")
            snapshots = await fetch_snapshots(
                self._clients,
                underlying,
                expiry,
                round(spot * (1 - STRIKE_PCT_RANGE), 2),
                round(spot * (1 + STRIKE_PCT_RANGE), 2),
            )
            chain = Chain(
                underlying=underlying,
                expiry=expiry,
                spot=spot,
                feed=str(getattr(self._clients.options_feed, "value", self._clients.options_feed)),
                as_of=datetime.now(timezone.utc),
                rows=build_chain_rows(contracts, snapshots, expiry),
            )
            self._chains[key] = _Entry(self._now(), chain)
            return chain
