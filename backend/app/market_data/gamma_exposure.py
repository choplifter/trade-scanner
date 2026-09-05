"""Net dealer gamma exposure (GEX) for SPY/QQQ plus a handful of the most
actively-optioned single names (TSLA, NVDA, PLTR) -- SPY/QQQ read as a
market-wide risk gauge that complements app.market_data.market_conditions'
VIX/breadth read, while the single names are a per-symbol version of the
same idea for tickers whose own options chain is liquid enough to matter.
Strongly negative GEX means dealers are net short gamma and tend to amplify
moves (sell into drops, buy into rallies); strongly positive GEX means
dealers are net long gamma and dampen volatility. Shown only on the Dash
market-conditions detail page -- it does not feed the React badge's
green/yellow/red level.

Verified live against this project's own Alpaca account before writing this
module (not assumed from docs): TradingClient.get_option_contracts() returns
real open_interest, and OptionHistoricalDataClient.get_option_chain() (the configured options
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
import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

from alpaca.data.requests import OptionChainRequest, StockLatestTradeRequest
from alpaca.trading.enums import ContractType
from alpaca.trading.requests import GetOptionContractsRequest

from app.alpaca.client import AlpacaClients
from app.options.payoff import bs_greeks, implied_vol, years_between
from app.services.market_clock import ET

logger = logging.getLogger(__name__)

# SPY/QQQ -- the two most liquid, most-tracked index ETFs -- plus TSLA,
# NVDA, and PLTR, three single names whose own options chains are liquid
# enough for a per-symbol GEX read to mean something. Not generalized to
# every scanner symbol: most have no liquid options chain at all.
SYMBOLS = ("SPY", "QQQ", "TSLA", "NVDA", "PLTR")

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
    # Total open interest behind the number. contracts_used says how many
    # strikes carried usable greeks; this says how much is actually held on
    # them -- together they are what tells a real gamma wall from three
    # contracts on an illiquid name. Defaulted so the older callers that
    # build a reading by hand (tests) keep working.
    open_interest_used: int = 0
    # The nearest expiry on its own (today's while it trades) and the
    # straddle-implied expected move to it -- see compute_near_expiry_gex /
    # compute_expected_move below. None when the chain gave nothing usable.
    near: "NearExpiryGex | None" = None
    expected_move: "ExpectedMove | None" = None


@dataclass
class OptionRow:
    """One contract as fetched: listing (strike, side, expiry, open
    interest) joined with its snapshot (Alpaca's gamma when it has one,
    the latest quote). What the near-expiry and expected-move steps read;
    the 45-day aggregate keeps using the (gamma, oi, is_call, strike)
    tuples compute_gex always took."""

    symbol: str
    expiry: date
    strike: float
    is_call: bool
    open_interest: int
    gamma: float | None
    bid: float | None
    ask: float | None

    @property
    def mid(self) -> float | None:
        if self.bid is None or self.ask is None or self.ask <= 0:
            return None
        if self.bid <= 0:
            # A one-sided quote: half the ask is the best guess the ticket
            # itself would make for a contract with no bid.
            return self.ask / 2
        return (self.bid + self.ask) / 2


@dataclass
class NearExpiryGex:
    """The gamma profile of one expiry -- the nearest one, today's while it
    still trades. The 45-day profile is dominated by long-dated open
    interest; intraday the contracts expiring today or tomorrow carry a
    gamma many times sharper per contract, and their walls move during
    the day. Alpaca computes no greeks for a contract expiring today
    (Black-Scholes divides by time to expiry), so for 0DTE the gamma is
    solved here from the contract's own quote -- the same solver the
    replayed chain uses -- and `source` says so."""

    expiry: date
    dte: int
    is_today: bool
    source: str  # "alpaca" (feed greeks) | "solved" (gamma from quotes, at least in part)
    net_gex: float
    contracts_used: int
    open_interest_used: int
    by_strike: list[StrikeGex] = field(default_factory=list)


@dataclass
class ExpectedMove:
    """What the option market prices as the underlying's move to `expiry`,
    read off the at-the-money straddle. Under Black-Scholes the ATM
    straddle's price *is* the expected absolute move (E|X| of a normal is
    sigma * sqrt(2/pi), and the straddle prices exactly that), so `move` is
    the straddle mid itself and `one_sigma` = move * sqrt(pi/2) is the 68%
    band. The band is symmetric around spot; skew is ignored, as every
    straddle-based expected move ignores it."""

    expiry: date
    dte: int
    strike: float
    straddle_mid: float
    move: float
    one_sigma: float
    low: float
    high: float


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
        open_interest_used=sum(open_interest for _, open_interest, _, _ in contracts),
    )


def top_walls(by_strike: list[StrikeGex], n: int = 3) -> list[StrikeGex]:
    """The n strikes with the largest |net_gex|, returned strike-ascending
    for stable left-to-right display -- selection and display order are
    deliberately different sorts. Shared by both the Dash page's callout and
    the /api/meta/gex REST endpoint so "biggest by magnitude" is defined in
    exactly one place.
    """
    biggest = sorted(by_strike, key=lambda row: abs(row.net_gex), reverse=True)[:n]
    return sorted(biggest, key=lambda row: row.strike)


def call_wall(by_strike: list[StrikeGex]) -> StrikeGex | None:
    """The strike with the largest call_gex -- the concentration of positive
    (call-side) dealer gamma, conventionally read as the nearest overhead
    resistance/profit-take level. `None` for an empty profile."""
    return max(by_strike, key=lambda row: row.call_gex, default=None)


def put_wall(by_strike: list[StrikeGex]) -> StrikeGex | None:
    """The strike with the most negative put_gex (largest |put_gex|) -- the
    concentration of put-side dealer gamma, conventionally read as the
    nearest support level. `None` for an empty profile."""
    return min(by_strike, key=lambda row: row.put_gex, default=None)


def gamma_flip_strike(by_strike: list[StrikeGex]) -> float | None:
    """Approximate "zero gamma" level: the strike where the running
    cumulative net_gex -- summed ascending by strike -- crosses zero,
    linearly interpolated between the two bracketing strikes.

    This is a proxy, not the rigorous zero-gamma level the well-known GEX
    trackers (SqueezeMetrics/GammaLab-style) compute -- that requires
    repricing the whole chain's gamma at a range of hypothetical spot
    prices, which needs a vol surface this app doesn't have. All this app
    has is real greeks at the *actual* spot (see the module docstring on why
    0DTE is excluded), so this cumulative-sum crossing is a cheaper stand-in
    for "where dealer gamma flips sign," not a claim of precision.

    Returns `None` when there are fewer than 2 strikes or the cumulative
    total never crosses zero across the profile.
    """
    if len(by_strike) < 2:
        return None
    cumulative = 0.0
    prev_strike: float | None = None
    prev_cumulative: float | None = None
    for row in by_strike:  # already ascending by strike
        cumulative += row.net_gex
        if prev_cumulative is not None and (prev_cumulative < 0) != (cumulative < 0):
            span = cumulative - prev_cumulative
            if span != 0:
                assert prev_strike is not None
                t = -prev_cumulative / span
                return prev_strike + t * (row.strike - prev_strike)
        prev_strike, prev_cumulative = row.strike, cumulative
    return None


_SQRT_HALF_PI = math.sqrt(math.pi / 2)
_CLOSE_ET = time(16, 0)


def nearest_expiry(expiries: "set[date] | list[date]", now: datetime) -> date | None:
    """Today's expiry while the session still trades (before 16:00 New
    York), else the first expiry after today. `now` may be any zone."""
    if not expiries:
        return None
    now_et = now.astimezone(ET)
    today = now_et.date()
    candidates = sorted(set(expiries))
    if today in candidates and now_et.time() < _CLOSE_ET:
        return today
    for expiry in candidates:
        if expiry > today:
            return expiry
    return None


def solve_gamma(is_call: bool, mid: float | None, spot: float, strike: float, years: float) -> float | None:
    """Gamma from a quote: solve the implied volatility that reprices the
    mid, then Black-Scholes gamma at it. None when the quote does not pin
    a volatility (no price, no time, deep in the money with no time value)."""
    if mid is None or mid <= 0 or years <= 0 or spot <= 0:
        return None
    kind = "call" if is_call else "put"
    sigma = implied_vol(kind, mid, spot, strike, years)
    if sigma is None or sigma <= 0:
        return None
    return bs_greeks(kind, spot, strike, years, sigma)[1]


def compute_near_expiry_gex(spot: float, expiry: date, rows: list[OptionRow], now: datetime) -> NearExpiryGex | None:
    """The gamma profile of `expiry` alone. Feed gammas where the feed has
    them; solved from the quote where it does not (0DTE, or a strike whose
    solver Alpaca gave up on). Same dollar-gamma convention as compute_gex,
    so a 0DTE wall and a 45-day wall are in the same units."""
    years = years_between(now, expiry)
    matched: list[tuple[float, int, bool, float]] = []
    solved = False
    for row in rows:
        if row.expiry != expiry:
            continue
        gamma = row.gamma
        if gamma is None:
            gamma = solve_gamma(row.is_call, row.mid, spot, row.strike, years)
            if gamma is None:
                continue
            solved = True
        matched.append((gamma, row.open_interest, row.is_call, row.strike))
    if not matched:
        return None
    reading = compute_gex("", spot, matched, now)
    today_et = now.astimezone(ET).date()
    return NearExpiryGex(
        expiry=expiry,
        dte=max(0, (expiry - today_et).days),
        is_today=expiry == today_et,
        source="solved" if solved else "alpaca",
        net_gex=reading.net_gex,
        contracts_used=reading.contracts_used,
        open_interest_used=reading.open_interest_used,
        by_strike=reading.by_strike,
    )


def compute_expected_move(spot: float, expiry: date, rows: list[OptionRow], now: datetime) -> ExpectedMove | None:
    """The straddle-implied move to `expiry`: the strike nearest spot that
    has both a call and a put quote, call mid plus put mid. None when no
    strike is quoted on both sides."""
    calls: dict[float, float] = {}
    puts: dict[float, float] = {}
    for row in rows:
        if row.expiry != expiry:
            continue
        mid = row.mid
        if mid is None:
            continue
        (calls if row.is_call else puts)[row.strike] = mid
    both = sorted(set(calls) & set(puts), key=lambda k: (abs(k - spot), k))
    if not both:
        return None
    strike = both[0]
    straddle = calls[strike] + puts[strike]
    if straddle <= 0:
        return None
    today_et = now.astimezone(ET).date()
    return ExpectedMove(
        expiry=expiry,
        dte=max(0, (expiry - today_et).days),
        strike=strike,
        straddle_mid=round(straddle, 4),
        move=round(straddle, 4),
        one_sigma=round(straddle * _SQRT_HALF_PI, 4),
        low=round(spot - straddle, 2),
        high=round(spot + straddle, 2),
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
) -> dict[str, tuple[int, bool, float, date]]:
    """Contract symbol -> (open_interest, is_call, strike_price, expiry).

    open_interest reads as 0 when Alpaca reports None for a contract -- a
    real "no open interest on this strike" answer, observed live to appear
    interleaved with populated values within the very same chain, not a
    signal that data is unavailable.
    """
    contracts: dict[str, tuple[int, bool, float, date]] = {}
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
                _as_date(contract.expiration_date),
            )
        page_token = page.next_page_token
        if not page_token:
            break
    return contracts


def _as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


async def _fetch_gammas(
    clients: AlpacaClients,
    symbol: str,
    expiration_gte: date,
    expiration_lte: date,
    strike_gte: float,
    strike_lte: float,
) -> dict[str, tuple[float | None, float | None, float | None]]:
    """Contract symbol -> (gamma, bid, ask). gamma is None where the
    snapshot has no greeks: every contract expiring today (Alpaca computes
    none for 0DTE), and the "implied-volatility solver didn't converge"
    case its docs describe for deep OTM or near-expiry contracts. The
    45-day aggregate skips those; the near-expiry profile solves its own
    gamma from the quote instead (compute_near_expiry_gex). Best-effort,
    same "fewer data points this cycle, not a failed call" posture as every
    other market-data fetch in this app.
    """
    request = OptionChainRequest(
        underlying_symbol=symbol,
        feed=clients.options_feed,
        expiration_date_gte=expiration_gte.isoformat(),
        expiration_date_lte=expiration_lte.isoformat(),
        strike_price_gte=strike_gte,
        strike_price_lte=strike_lte,
    )
    chain = await asyncio.to_thread(clients.options.get_option_chain, request)
    out: dict[str, tuple[float | None, float | None, float | None]] = {}
    for sym, snap in chain.items():
        gamma = snap.greeks.gamma if getattr(snap, "greeks", None) is not None else None
        quote = getattr(snap, "latest_quote", None)
        bid = float(quote.bid_price) if quote is not None and quote.bid_price is not None else None
        ask = float(quote.ask_price) if quote is not None and quote.ask_price is not None else None
        out[sym] = (gamma, bid, ask)
    return out


async def fetch_gex(clients: AlpacaClients, symbol: str) -> GexReading | None:
    """Best-effort -- returns None (logged) rather than raising, same
    contract as every other periodic refresh this feeds into
    (ScannerEngine._refresh_gex)."""
    try:
        spot_price = await _spot_price(clients, symbol)
        if spot_price is None:
            return None

        now = datetime.now(timezone.utc)
        today = now.astimezone(ET).date()
        # The window starts today so the nearest expiry (0DTE while it
        # trades) is in hand for the near-expiry profile and the expected
        # move; the 45-day aggregate below still leaves today's expiry out,
        # as it always has (see module docstring), so its numbers do not
        # move with this addition.
        expiration_gte = today
        expiration_lte = today + timedelta(days=_EXPIRATION_DAYS_AHEAD)
        strike_gte = spot_price * (1 - _STRIKE_PCT_RANGE)
        strike_lte = spot_price * (1 + _STRIKE_PCT_RANGE)

        contracts, snapshots = await asyncio.gather(
            _fetch_contracts(clients, symbol, expiration_gte, expiration_lte, strike_gte, strike_lte),
            _fetch_gammas(clients, symbol, expiration_gte, expiration_lte, strike_gte, strike_lte),
        )

        rows: list[OptionRow] = []
        for sym, (open_interest, is_call, strike, expiry) in contracts.items():
            gamma, bid, ask = snapshots.get(sym, (None, None, None))
            rows.append(OptionRow(sym, expiry, strike, is_call, open_interest, gamma, bid, ask))

        matched = [
            (row.gamma, row.open_interest, row.is_call, row.strike)
            for row in rows
            if row.gamma is not None and row.expiry > today
        ]
        reading = compute_gex(symbol, spot_price, matched, now)

        near_expiry = nearest_expiry([row.expiry for row in rows], now)
        if near_expiry is not None:
            reading.near = compute_near_expiry_gex(spot_price, near_expiry, rows, now)
            reading.expected_move = compute_expected_move(spot_price, near_expiry, rows, now)
        return reading
    except Exception:
        logger.exception("GEX fetch failed for %s", symbol)
        return None
