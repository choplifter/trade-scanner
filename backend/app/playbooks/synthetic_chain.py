"""A chain that never existed: Black-Scholes prices on a strike grid for a
day in the past, so a playbook can be walked over months of price history.

The app has no historical option prices. What it has is the daily close,
from which a volatility can be estimated (the standard deviation of log
returns over the trailing sessions, annualised) and scaled by a premium
factor to stand in for the implied volatility the market would have
charged. Every quote here is that: one flat sigma per day, no skew, no
term structure, a bid/ask spread of a fixed fraction around the mid,
European exercise, no dividends. It shows how a playbook *behaves* --
when it sells, rolls, gets assigned -- not what it would have earned.
Everything downstream says "synthetic" for that reason.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

from app.options.chain import Chain, LegQuote, StrikeRow
from app.options.occ import format_occ
from app.options.payoff import _norm_cdf, bs_price

TRADING_DAYS = 252
VOL_WINDOW = 20
VOL_FLOOR = 0.10
# Strikes within this fraction of the spot: the wheel sells near 0.30
# delta (a few percent out) and lifts a call to the cost basis; a fifth
# either way covers both without pricing a board nobody reads.
STRIKES_PCT = 0.20


def strike_step(spot: float) -> float:
    """The listed strike spacing at a price level, roughly as the exchanges
    set it."""
    if spot < 25:
        return 0.5
    if spot < 100:
        return 1.0
    if spot < 200:
        return 2.5
    if spot < 500:
        return 5.0
    return 10.0


def _fridays(start: date, count: int) -> list[date]:
    out: list[date] = []
    d = start + timedelta(days=(4 - start.weekday()) % 7 or 7)  # the next Friday strictly after start
    while len(out) < count:
        out.append(d)
        d += timedelta(days=7)
    return out


def _third_friday(year: int, month: int) -> date:
    first = date(year, month, 1)
    offset = (4 - first.weekday()) % 7
    return first + timedelta(days=offset + 14)


def synthetic_expiries(today: date, *, weeks: int = 8, monthlies_to_days: int = 180) -> list[date]:
    """Weekly Fridays for the next `weeks`, then the third Fridays out to
    `monthlies_to_days` -- the shape of a liquid name's board."""
    out = set(_fridays(today, weeks))
    y, m = today.year, today.month
    for _ in range(8):
        third = _third_friday(y, m)
        if today < third <= today + timedelta(days=monthlies_to_days):
            out.add(third)
        m += 1
        if m > 12:
            m = 1
            y += 1
    return sorted(out)


def realized_vol(closes: list[float], window: int = VOL_WINDOW) -> float | None:
    """Annualised standard deviation of daily log returns over the last
    `window` closes; None with too few."""
    if len(closes) < window + 1:
        return None
    tail = closes[-(window + 1) :]
    rets = [math.log(b / a) for a, b in zip(tail, tail[1:]) if a > 0 and b > 0]
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var * TRADING_DAYS)


def build_chain(
    symbol: str,
    today: date,
    spot: float,
    sigma: float,
    expiry: date,
    *,
    strikes_pct: float = STRIKES_PCT,
    spread_frac: float = 0.02,
    rate: float = 0.0,
) -> Chain:
    """One expiry's synthetic chain: strikes on the exchange grid within
    ±`strikes_pct` of the spot, each priced by Black-Scholes at `sigma`,
    with a bid/ask of `spread_frac` of the mid around it."""
    years = max((expiry - today).days, 0) / 365.0
    step = strike_step(spot)
    low = math.floor(spot * (1 - strikes_pct) / step) * step
    high = math.ceil(spot * (1 + strikes_pct) / step) * step
    rows: list[StrikeRow] = []
    count = int(round((high - low) / step)) + 1
    for i in range(count):
        k = round(low + i * step, 2)
        if k > 0:
            quotes = {}
            # One d1 serves both kinds: call delta N(d1), put delta N(d1) - 1,
            # and the put from put-call parity -- a third of the arithmetic
            # of pricing each side and its greeks separately, over the
            # hundred thousand quotes a year's walk builds.
            if years > 0:
                vol_t = sigma * math.sqrt(years)
                d1 = (math.log(spot / k) + 0.5 * sigma * sigma * years) / vol_t
                call_delta = _norm_cdf(d1)
                call_mid = bs_price("call", spot, k, years, sigma, rate)
                put_mid = call_mid - spot + k * math.exp(-rate * years)
            else:
                call_delta = 1.0 if spot > k else 0.0
                call_mid = max(spot - k, 0.0)
                put_mid = max(k - spot, 0.0)
            for kind, mid, delta in (("call", call_mid, call_delta), ("put", put_mid, call_delta - 1.0)):
                mid = round(max(mid, 0.01), 4)
                half = mid * spread_frac / 2
                quotes[kind] = LegQuote(
                    symbol=format_occ(symbol, expiry, kind, k),
                    strike=k,
                    kind=kind,
                    expiry=expiry,
                    bid=round(max(mid - half, 0.01), 4),
                    ask=round(mid + half, 4),
                    mid=mid,
                    last=mid,
                    bid_size=10,
                    ask_size=10,
                    delta=round(delta, 4),
                    gamma=None,
                    theta=None,
                    iv=round(sigma, 4),
                    open_interest=0,
                    tradable=True,
                )
            rows.append(StrikeRow(strike=k, call=quotes["call"], put=quotes["put"]))
    from datetime import datetime, time, timezone

    as_of = datetime.combine(today, time(16, 0), tzinfo=timezone.utc)
    return Chain(underlying=symbol, expiry=expiry, spot=spot, feed="synthetic", as_of=as_of, rows=rows)
