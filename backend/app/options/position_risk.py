"""What a position is exposed to, beyond its expiry payoff -- three things
Natenberg, Option Volatility and Pricing (2nd ed.), puts at the centre of
managing one and the dashboard did not show:

* The position's greeks (ch. 7 "Risk Measurement I", ch. 21 "Position
  Analysis"): delta, gamma, theta and vega summed over the legs, in the
  units a position is managed in -- delta in shares, theta and vega in
  dollars. Vega above all: a condor is a short-volatility position, and
  until now no number on screen said how much it loses per vol point.
* The breakeven volatility (ch. 13 "How Much Margin for Error?"): the
  volatility at which the model values the package at exactly the price
  it is traded at. Selling a condor is a bet that the stock realises less
  than this; how far the realised volatility sits below it is the margin
  for error.
* Early assignment (ch. 16 "Early Exercise of American Options"): a short
  call in the money is exercised the day before an ex-dividend date when
  the dividend is worth more than what is left of its time value; a short
  put deep in the money once the interest on the strike outweighs its
  time value.

Greeks are computed here from each leg's own IV with the same Black-Scholes
the risk chart uses, rather than taken from the feed: the feed carries no
vega, a replayed or simulated chain carries solved IVs only, and one
formula for all of them keeps the position numbers comparable.
"""

import asyncio
import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from app.options.payoff import _norm_pdf, bs_greeks, bs_price, implied_vol, intrinsic, years_between

logger = logging.getLogger(__name__)

CONTRACT_MULTIPLIER = 100
# What cash earns while a put holder waits instead of exercising: the
# carry Natenberg's put condition weighs against the time value. The app
# prices with a zero rate everywhere else (the payoff's own caveat); here a
# rate is the whole question, so it is named rather than assumed away.
CARRY_RATE = 0.04
# How far ahead ex-dividend dates are looked up. Alpaca answers a window
# of at most 90 days.
DIVIDEND_LOOKAHEAD_DAYS = 90


@dataclass(frozen=True)
class RiskLeg:
    """One leg as the risk numbers need it. `qty` is signed contracts per
    position (long positive), already multiplied by how many structures
    are held; a stock leg carries shares in `qty` and no strike."""

    kind: str  # "call" | "put" | "stock"
    strike: float
    expiry: date | None
    qty: float
    iv: float | None = None
    mid: float | None = None


@dataclass(frozen=True)
class PositionGreeks:
    """Summed over the legs, for the whole position. delta: share
    equivalents. gamma: change in that delta per 1 $ of the underlying.
    theta: dollars per calendar day. vega: dollars per volatility point."""

    delta: float
    gamma: float
    theta: float
    vega: float

    def to_dict(self) -> dict:
        return {
            "delta": round(self.delta, 2),
            "gamma": round(self.gamma, 4),
            "theta": round(self.theta, 2),
            "vega": round(self.vega, 2),
        }


def bs_vega(spot: float, strike: float, years: float, sigma: float) -> float:
    """Price change per share for one volatility point (0.01)."""
    if years <= 0 or sigma <= 0 or spot <= 0 or strike <= 0:
        return 0.0
    sqrt_t = math.sqrt(years)
    d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * years) / (sigma * sqrt_t)
    return spot * _norm_pdf(d1) * sqrt_t / 100.0


def _sigma_for(leg: RiskLeg, spot: float, years: float) -> float | None:
    """The leg's IV; failing that, the one its mid implies; failing that,
    zero when the mid is all intrinsic (a deep in-the-money leg the feed
    gives no IV for moves one-for-one) -- None when nothing is known."""
    if leg.iv is not None and leg.iv > 0:
        return leg.iv
    if leg.mid is None:
        return None
    solved = implied_vol(leg.kind, leg.mid, spot, leg.strike, years)
    if solved:
        return solved
    return 0.0 if leg.mid <= intrinsic(leg.kind, spot, leg.strike) + 0.05 else None


def position_greeks(legs: list[RiskLeg], spot: float, now: datetime) -> PositionGreeks | None:
    """None when an option leg has nothing to compute from -- a partial
    sum would read as a hedged position that is not."""
    delta = gamma = theta = vega = 0.0
    for leg in legs:
        if leg.kind == "stock":
            delta += leg.qty
            continue
        years = years_between(now, leg.expiry) if leg.expiry is not None else 0.0
        sigma = _sigma_for(leg, spot, years) if years > 0 else 0.0
        if sigma is None:
            return None
        d, g, t = bs_greeks(leg.kind, spot, leg.strike, years, sigma)
        size = leg.qty * CONTRACT_MULTIPLIER
        delta += d * size
        gamma += g * size
        theta += t * size
        vega += bs_vega(spot, leg.strike, years, sigma) * size
    return PositionGreeks(delta=delta, gamma=gamma, theta=theta, vega=vega)


def _package_value(legs: list[RiskLeg], spot: float, now: datetime, scale: float) -> float:
    total = 0.0
    for leg in legs:
        years = years_between(now, leg.expiry) if leg.expiry is not None else 0.0
        total += leg.qty * bs_price(leg.kind, spot, leg.strike, years, (leg.iv or 0.0) * scale)
    return total


def breakeven_vol(legs: list[RiskLeg], spot: float, now: datetime, price: float, atm_iv: float) -> float | None:
    """The volatility at which the model values the package at `price`
    (per share of one structure, signed like a ticket: positive paid,
    negative received), as an at-the-money figure.

    Every leg's IV is scaled by one factor, so the smile's shape is kept
    and only its level moves -- the level is what a realised volatility
    is compared against. The root nearest today's level is taken; a
    package whose value never reaches the price (a quote the model cannot
    reproduce at any volatility) has none. `legs` carry one structure's
    ratios in `qty`."""
    if atm_iv <= 0 or any(leg.kind == "stock" or not leg.iv or leg.iv <= 0 for leg in legs):
        return None

    def f(k: float) -> float:
        return _package_value(legs, spot, now, k) - price

    grid = [0.05 * i for i in range(1, 81)]  # 0.05x .. 4x today's level
    values = [f(k) for k in grid]
    roots: list[float] = []
    for (a, fa), (b, fb) in zip(zip(grid, values), zip(grid[1:], values[1:])):
        if fa == 0:
            roots.append(a)
        elif fa * fb < 0:
            lo, hi, flo = a, b, fa
            for _ in range(60):
                mid = (lo + hi) / 2
                fm = f(mid)
                if flo * fm <= 0:
                    hi = mid
                else:
                    lo, flo = mid, fm
            roots.append((lo + hi) / 2)
    if not roots:
        return None
    k = min(roots, key=lambda r: abs(r - 1.0))
    return atm_iv * k


@dataclass(frozen=True)
class Dividend:
    ex_date: date
    cash: float


def assignment_risks(
    legs: list[RiskLeg],
    spot: float,
    now: datetime,
    dividends: list[Dividend],
    *,
    rate: float = CARRY_RATE,
) -> list[str]:
    """Plain-language warnings, one per short leg at risk of being
    assigned before expiry. A leg without a mid is judged on its
    intrinsic value alone, which makes its time value zero -- the
    conservative reading."""
    out: list[str] = []
    today = now.date()
    for leg in legs:
        if leg.kind not in ("call", "put") or leg.qty >= 0 or leg.expiry is None:
            continue
        inner = intrinsic(leg.kind, spot, leg.strike)
        time_value = max(0.0, (leg.mid if leg.mid is not None else inner) - inner)
        label = f"short {leg.strike:g} {leg.kind}"
        if leg.kind == "call":
            ahead = [d for d in dividends if today <= d.ex_date <= leg.expiry and d.cash > 0]
            if not ahead:
                continue
            div = min(ahead, key=lambda d: d.ex_date)
            if inner > 0 and div.cash > time_value:
                out.append(
                    f"Early assignment likely on the {label}: in the money, and the {div.cash:.2f} dividend "
                    f"(ex {div.ex_date:%d %b}) is worth more than its {time_value:.2f} of time value. Holders "
                    f"exercise the day before the ex-date to collect it; you would be short the shares and owe "
                    "the dividend. Close or roll it before then."
                )
            elif inner > 0 or spot * 1.03 >= leg.strike:
                out.append(
                    f"Ex-dividend {div.ex_date:%d %b} ({div.cash:.2f}) before expiry: the {label} becomes an "
                    "early-assignment candidate if it is in the money then and its time value has fallen "
                    "below the dividend."
                )
        else:
            if inner <= 0:
                continue
            years = years_between(now, leg.expiry)
            carry = leg.strike * rate * years
            if time_value < carry:
                out.append(
                    f"Early assignment possible on the {label}: deep in the money with {time_value:.2f} of time "
                    f"value left, less than the {carry:.2f} the strike's cash would earn at {rate:.0%} until "
                    "expiry -- a holder gains by exercising now. You would buy the shares at the strike."
                )
    return out


# How close to a short strike the underlying must close on expiry day for
# the outcome to be in doubt: half a percent of spot, or one day's move at
# the leg's own IV if that is wider -- an exercise decision is taken until
# 17:30 ET, on whatever the stock does after the bell.
PIN_MIN_FRACTION = 0.005
TRADING_DAYS_PER_YEAR = 252


def pin_risks(legs: list[RiskLeg], spot: float, now: datetime) -> list[str]:
    """Expiry-day warnings for short legs the underlying sits on
    (Natenberg ch. 23, "Expiration Straddles"): whether such a leg is
    assigned is not known until after the close, so the position on
    Monday may hold shares nobody chose to hold. One line per leg."""
    from app.services.market_clock import ET

    today = now.astimezone(ET).date() if now.tzinfo else now.date()
    out: list[str] = []
    for leg in legs:
        if leg.kind not in ("call", "put") or leg.qty >= 0 or leg.expiry != today:
            continue
        sigma = leg.iv if leg.iv and leg.iv > 0 else 0.0
        band = max(PIN_MIN_FRACTION * spot, spot * sigma / math.sqrt(TRADING_DAYS_PER_YEAR))
        gap = spot - leg.strike
        if abs(gap) > band:
            continue
        side = "above" if gap > 0 else "below" if gap < 0 else "at"
        where = "at" if side == "at" else f"{abs(gap):.2f} {side}"
        out.append(
            f"Pin risk on the short {leg.strike:g} {leg.kind}: the underlying is {where} the strike on expiry "
            f"day. Whether it is assigned is decided after the close (exercise runs until 17:30 ET), so you may "
            f"find {100 * abs(leg.qty):.0f} shares long or short on the next session with the hedging leg gone. "
            "Close it before the bell; Alpaca liquidates what the account cannot cover from 15:30 ET "
            "(15:45 for SPY, QQQ and other broad ETFs)."
        )
    return out


class DividendCalendar:
    """Upcoming ex-dividend dates, market-wide, kept for the day: one query
    for the next DIVIDEND_LOOKAHEAD_DAYS. Cash dividends only; a stock
    dividend changes the contract's deliverable instead of its value.

    Read from Alpaca's corporate-actions market data, which lists declared
    dividends ahead of their ex-date; the trading API's announcements are
    the fallback -- measured 2026-10-04 they held 21 symbols for the next
    90 days, against the several hundred ex-dates a quarter brings. Either
    way only a *declared* dividend is known: a quarterly payer that has not
    announced yet raises no warning, which is "not known", not "none"."""

    def __init__(self, clients) -> None:
        self._clients = clients
        self._by_symbol: dict[str, list[Dividend]] = {}
        self._loaded_on: date | None = None
        self._lock = asyncio.Lock()

    async def upcoming(self, symbol: str) -> list[Dividend]:
        today = datetime.now(timezone.utc).date()
        async with self._lock:
            if self._loaded_on != today:
                loaded = await self._fetch(today)
                if loaded is not None:
                    self._by_symbol, self._loaded_on = loaded, today
        return self._by_symbol.get(symbol.upper(), [])

    async def _fetch(self, today: date) -> dict[str, list[Dividend]] | None:
        until = today + timedelta(days=DIVIDEND_LOOKAHEAD_DAYS - 1)
        out = await self._from_market_data(today, until)
        source = "corporate-actions data"
        if out is None:
            out = await self._from_announcements(today, until)
            source = "announcements"
        if out is None:
            return None
        for divs in out.values():
            divs.sort(key=lambda d: d.ex_date)
        logger.info(
            "Dividend calendar: %d symbols with an ex-date in the next %d days (%s)",
            len(out),
            DIVIDEND_LOOKAHEAD_DAYS,
            source,
        )
        return out

    async def _from_market_data(self, today: date, until: date) -> dict[str, list[Dividend]] | None:
        client = getattr(self._clients, "corporate_actions", None)
        if client is None:
            return None
        from alpaca.data.enums import CorporateActionsType
        from alpaca.data.requests import CorporateActionsRequest

        try:
            request = CorporateActionsRequest(types=[CorporateActionsType.CASH_DIVIDEND], start=today, end=until)
            result = await asyncio.to_thread(client.get_corporate_actions, request)
        except Exception:
            logger.exception("Corporate actions (cash dividends) fetch failed")
            return None
        out: dict[str, list[Dividend]] = {}
        for action in (getattr(result, "data", None) or {}).get("cash_dividends", []):
            ex = getattr(action, "ex_date", None)
            rate = getattr(action, "rate", None)
            symbol = (getattr(action, "symbol", None) or "").upper()
            if not symbol or ex is None or not rate or float(rate) <= 0 or not (today <= ex <= until):
                continue
            out.setdefault(symbol, []).append(Dividend(ex_date=ex, cash=float(rate)))
        return out

    async def _from_announcements(self, today: date, until: date) -> dict[str, list[Dividend]] | None:
        from alpaca.trading.enums import CorporateActionDateType, CorporateActionType
        from alpaca.trading.requests import GetCorporateAnnouncementsRequest

        try:
            request = GetCorporateAnnouncementsRequest(
                ca_types=[CorporateActionType.DIVIDEND],
                since=today,
                until=until,
                date_type=CorporateActionDateType.EX_DATE,
            )
            announcements = await asyncio.to_thread(self._clients.trading.get_corporate_announcements, request)
        except Exception:
            logger.exception("Corporate announcements (dividend) fetch failed")
            return None
        out: dict[str, list[Dividend]] = {}
        for a in announcements:
            symbol = (getattr(a, "target_symbol", None) or getattr(a, "initiating_symbol", None) or "").upper()
            ex = getattr(a, "ex_date", None)
            cash = getattr(a, "cash", None)
            if not symbol or ex is None or not cash or float(cash) <= 0:
                continue
            out.setdefault(symbol, []).append(Dividend(ex_date=ex, cash=float(cash)))
        return out


# Each underlying's daily closes, per day: a ticket previews on every
# change of strike or limit, and the closes behind the forecast change
# once a session.
_CLOSES_CACHE: dict[tuple[str, date], list[float]] = {}
# A year of sessions for the long-run level, with room for holidays.
_CLOSES_LOOKBACK_DAYS = 400


async def vol_forecast_today(clients, symbol: str, dte: int):
    """The underlying's volatility forecast for an option of `dte` days
    (iv_context.forecast_vol): the 20-session reading blended toward the
    past year's, the more so the longer the option runs. None without
    data."""
    if clients is None:
        return None
    key = (symbol.upper(), datetime.now(timezone.utc).date())
    closes = _CLOSES_CACHE.get(key)
    if closes is None:
        from app.market_data.bars import get_daily_bars_multi
        from app.options.events import closes_by_day

        try:
            bars = await get_daily_bars_multi(clients, [key[0]], lookback_days=_CLOSES_LOOKBACK_DAYS)
        except Exception:
            logger.exception("Daily bars for the vol forecast failed for %s", symbol)
            return None
        closes = [close for _, close in sorted(closes_by_day(bars.get(key[0], [])).items())]
        _CLOSES_CACHE[key] = closes
    from app.options.iv_context import forecast_vol

    return forecast_vol(closes, dte) if closes else None


async def spread_risks(source, groups: list, dividends: "DividendCalendar | None") -> tuple[dict[str, dict], dict | None]:
    """Per held structure ({group id: {greeks, warnings}}) and the account's
    sum of the greeks across every structure that has them -- the "position
    analysis" view: what the book as a whole does when the market moves or
    implied volatility changes. Delta adds up across underlyings only as a
    count of share equivalents, so the total is offered for theta and vega,
    which are dollars, and delta is summed per underlying by the caller if
    wanted. Quotes come through the account's own source, so a simulated or
    replayed book reads its own prices; dividends are skipped in a replay."""
    if not groups:
        return {}, {"theta": 0.0, "vega": 0.0, "structures": 0, "of": 0, "collateral": 0.0}
    now = source.now()
    replay = getattr(source, "as_of", None) is not None
    symbols = sorted({leg.symbol for g in groups for leg in g.legs})
    try:
        quotes = await source.leg_quotes(symbols)
    except Exception:
        logger.exception("Leg quotes for the held spreads failed")
        quotes = {}
    spots: dict[str, float | None] = {}
    for underlying in sorted({g.underlying for g in groups}):
        try:
            spots[underlying] = await source.spot(underlying)
        except Exception:
            spots[underlying] = None
    from app.options.positions import collateral_for

    out: dict[str, dict] = {}
    theta = vega = 0.0
    counted = 0
    tied_up = sum(collateral_for(g) for g in groups)
    for g in groups:
        spot = spots.get(g.underlying)
        if not spot:
            out[g.id] = {"greeks": None, "warnings": [], "collateral": collateral_for(g)}
            continue
        legs: list[RiskLeg] = []
        for leg in g.legs:
            q = quotes.get(leg.symbol)
            legs.append(
                RiskLeg(
                    kind=leg.kind,
                    strike=leg.strike,
                    expiry=leg.expiry,
                    qty=leg.qty,
                    iv=q.iv if q is not None else None,
                    mid=(q.mid if q is not None and q.mid is not None else leg.current_price or None),
                )
            )
        greeks = position_greeks(legs + ([RiskLeg("stock", 0.0, None, g.shares)] if g.shares else []), spot, now)
        warnings: list[str] = pin_risks(legs, spot, now)
        if dividends is not None and not replay:
            try:
                warnings += assignment_risks(legs, spot, now, await dividends.upcoming(g.underlying))
            except Exception:
                logger.exception("Assignment check failed for %s", g.underlying)
        out[g.id] = {
            "greeks": greeks.to_dict() if greeks is not None else None,
            "warnings": warnings,
            "collateral": collateral_for(g),
        }
        if greeks is not None:
            theta += greeks.theta
            vega += greeks.vega
            counted += 1
    totals = {
        "theta": round(theta, 2),
        "vega": round(vega, 2),
        "structures": counted,
        "of": len(groups),
        "collateral": round(tied_up, 2),
    }
    return out, totals
