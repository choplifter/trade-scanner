"""Net dealer gamma exposure (GEX) for SPY/QQQ -- a market-wide risk gauge
that complements app.market_data.market_conditions' VIX/breadth read.
Strongly negative GEX means dealers are net short gamma and tend to amplify
moves (sell into drops, buy into rallies); strongly positive GEX means
dealers are net long gamma and dampen volatility. Shown only on the Dash
market-conditions detail page -- it does not feed the React badge's
green/yellow/red level.

Verified live against this project's own Alpaca account before writing this
module (not assumed from docs): TradingClient.get_option_contracts() returns
real open_interest, and OptionHistoricalDataClient.get_option_chain() (OPRA
feed) returns real greeks (including gamma) -- but only for contracts that
are not 0DTE. Alpaca computes greeks itself via Black-Scholes and structurally
cannot for a contract expiring today (division by zero in the time-to-expiry
term, confirmed in Alpaca's own docs) -- fetch_gex excludes today's
expiration for exactly this reason, not as a defensive guess.

Deliberately bounded to a near-dated expiration window and a band of strikes
around spot (see the module constants below) rather than every listed
contract: very long-dated LEAPS strikes carry real open interest too and
would otherwise dominate/distort the number while contributing little to the
market's *current* gamma profile, and an unbounded chain means an unbounded
number of API calls for two of the most actively-listed underlyings on the
exchange.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from alpaca.data.enums import OptionsFeed
from alpaca.data.requests import OptionChainRequest, StockLatestTradeRequest
from alpaca.trading.enums import ContractType
from alpaca.trading.requests import GetOptionContractsRequest

from app.alpaca.client import AlpacaClients

logger = logging.getLogger(__name__)

# SPY and QQQ only -- the two most liquid, most-tracked index ETFs. Most
# scanner symbols have no liquid options chain at all, so this isn't
# generalized per-symbol the way e.g. merger actions are.
SYMBOLS = ("SPY", "QQQ")

# Near-dated window: excludes 0DTE (see module docstring) through 45 days
# out. A handful of far-dated LEAPS strikes are deliberately left out -- see
# module docstring.
_EXPIRATION_DAYS_AHEAD = 45
# +/-15% of spot -- deep ITM/OTM strikes carry negligible gamma and would
# otherwise multiply the number of contracts (and API calls) fetched for
# little effect on the result.
_STRIKE_PCT_RANGE = 0.15
_CONTRACTS_PAGE_LIMIT = 1000
# Safety cap on pagination loops -- defensive, not expected to be hit at the
# strike/expiration bounds above.
_MAX_CONTRACT_PAGES = 20


@dataclass
class StrikeGex:
    strike: float
    net_gex: float
    call_gex: float
    put_gex: float


@dataclass
class GexReading:
    symbol: str
    spot_price: float
    net_gex: float  # signed dollar gamma exposure: calls positive, puts negative
    call_gex: float
    put_gex: float
    contracts_used: int
    as_of: datetime
    by_strike: list[StrikeGex]  # ascending by strike -- the "gamma wall" profile


def compute_gex(
    symbol: str,
    spot_price: float,
    contracts: list[tuple[float, int, bool, float]],
    as_of: datetime,
) -> GexReading:
    """Pure combinator -- no I/O, so it's directly unit-testable.

    `contracts` is (gamma, open_interest, is_call, strike) per contract,
    already fetched and matched by the caller. Dollar gamma exposure per
    contract is gamma * open_interest * 100 (shares/contract) *
    spot_price**2 * 0.01 -- calls positive, puts negative. This is the
    common convention used by the popular free GEX trackers, not an
    unambiguous industry standard: the community genuinely disagrees on
    which side of the trade dealers are assumed to hold, so treat the sign
    as one widely-used convention, not a settled fact.

    Accumulated both as one net total (net_gex/call_gex/put_gex) and per
    strike (by_strike) -- the same per-contract dollar_gamma value just
    lands in two places, so the two views can never disagree with each
    other.
    """
    call_gex = 0.0
    put_gex = 0.0
    by_strike: dict[float, list[float]] = {}  # strike -> [call_gex, put_gex]
    for gamma, open_interest, is_call, strike in contracts:
        dollar_gamma = gamma * open_interest * 100 * spot_price**2 * 0.01
        bucket = by_strike.setdefault(strike, [0.0, 0.0])
        if is_call:
            call_gex += dollar_gamma
            bucket[0] += dollar_gamma
        else:
            put_gex -= dollar_gamma
            bucket[1] -= dollar_gamma
    strike_rows = [
        StrikeGex(strike=strike, net_gex=call + put, call_gex=call, put_gex=put)
        for strike, (call, put) in sorted(by_strike.items())
    ]
    return GexReading(
        symbol=symbol,
        spot_price=spot_price,
        net_gex=call_gex + put_gex,
        call_gex=call_gex,
        put_gex=put_gex,
        contracts_used=len(contracts),
        as_of=as_of,
        by_strike=strike_rows,
    )


async def _spot_price(clients: AlpacaClients, symbol: str) -> float | None:
    try:
        request = StockLatestTradeRequest(symbol_or_symbols=symbol, feed=clients.feed)
        latest = await asyncio.to_thread(clients.data.get_stock_latest_trade, request)
        trade = latest.get(symbol) if isinstance(latest, dict) else None
        return float(trade.price) if trade and trade.price else None
    except Exception:
        logger.exception("GEX spot price fetch failed for %s", symbol)
        return None


async def _fetch_contracts(
    clients: AlpacaClients,
    symbol: str,
    expiration_gte: date,
    expiration_lte: date,
    strike_gte: float,
    strike_lte: float,
) -> dict[str, tuple[int, bool, float]]:
    """Contract symbol -> (open_interest, is_call, strike_price).

    open_interest reads as 0 when Alpaca reports None for a contract -- a
    real "no open interest on this strike" answer, observed live to appear
    interleaved with populated values within the very same chain, not a
    signal that data is unavailable.
    """
    contracts: dict[str, tuple[int, bool, float]] = {}
    page_token: str | None = None
    for _ in range(_MAX_CONTRACT_PAGES):
        request = GetOptionContractsRequest(
            underlying_symbols=[symbol],
            expiration_date_gte=expiration_gte.isoformat(),
            expiration_date_lte=expiration_lte.isoformat(),
            # Strike bounds must be strings here -- confirmed live:
            # GetOptionContractsRequest rejects numeric strike bounds with a
            # pydantic string_type error, unlike OptionChainRequest below.
            strike_price_gte=str(strike_gte),
            strike_price_lte=str(strike_lte),
            limit=_CONTRACTS_PAGE_LIMIT,
            page_token=page_token,
        )
        page = await asyncio.to_thread(clients.trading.get_option_contracts, request)
        for contract in page.option_contracts:
            contracts[contract.symbol] = (
                int(contract.open_interest or 0),
                contract.type == ContractType.CALL,
                float(contract.strike_price),
            )
        page_token = page.next_page_token
        if not page_token:
            break
    return contracts


async def _fetch_gammas(
    clients: AlpacaClients,
    symbol: str,
    expiration_gte: date,
    expiration_lte: date,
    strike_gte: float,
    strike_lte: float,
) -> dict[str, float]:
    """Contract symbol -> gamma. Skips any contract whose snapshot has no
    greeks -- 0DTE is already excluded by the date bounds, but this also
    covers the "implied-volatility solver didn't converge" case Alpaca's
    docs describe for deep OTM or near-expiry contracts; best-effort, same
    "fewer data points this cycle, not a failed call" posture as every other
    market-data fetch in this app (e.g. news_feed.py's per-batch handling).
    """
    request = OptionChainRequest(
        underlying_symbol=symbol,
        feed=OptionsFeed.OPRA,
        expiration_date_gte=expiration_gte.isoformat(),
        expiration_date_lte=expiration_lte.isoformat(),
        strike_price_gte=strike_gte,
        strike_price_lte=strike_lte,
    )
    chain = await asyncio.to_thread(clients.options.get_option_chain, request)
    return {sym: snap.greeks.gamma for sym, snap in chain.items() if snap.greeks is not None}


async def fetch_gex(clients: AlpacaClients, symbol: str) -> GexReading | None:
    """Best-effort -- returns None (logged) rather than raising, same
    contract as every other periodic refresh this feeds into
    (ScannerEngine._refresh_gex)."""
    try:
        spot_price = await _spot_price(clients, symbol)
        if spot_price is None:
            return None

        today = date.today()
        expiration_gte = today + timedelta(days=1)  # excludes 0DTE -- see module docstring
        expiration_lte = today + timedelta(days=_EXPIRATION_DAYS_AHEAD)
        strike_gte = spot_price * (1 - _STRIKE_PCT_RANGE)
        strike_lte = spot_price * (1 + _STRIKE_PCT_RANGE)

        contracts, gammas = await asyncio.gather(
            _fetch_contracts(clients, symbol, expiration_gte, expiration_lte, strike_gte, strike_lte),
            _fetch_gammas(clients, symbol, expiration_gte, expiration_lte, strike_gte, strike_lte),
        )

        matched = [
            (gammas[sym], open_interest, is_call, strike)
            for sym, (open_interest, is_call, strike) in contracts.items()
            if sym in gammas
        ]
        return compute_gex(symbol, spot_price, matched, datetime.now(timezone.utc))
    except Exception:
        logger.exception("GEX fetch failed for %s", symbol)
        return None
