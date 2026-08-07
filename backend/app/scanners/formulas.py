"""Pure scanner math. Kept free of Alpaca SDK types so it's easy to unit test."""


def resolve_last_price(
    latest_trade_price: float | None,
    daily_bar_close: float | None,
    previous_daily_bar_close: float | None,
) -> float | None:
    """Best available "current" price, in order of freshness.

    latest_trade covers premarket/afterhours ticks that the daily bar may not
    reflect yet; daily_bar.close covers regular-session symbols with no very
    recent print; previous_daily_bar.close is the last resort for a symbol
    that hasn't traded at all today (e.g. still closed/illiquid).
    """
    for price in (latest_trade_price, daily_bar_close, previous_daily_bar_close):
        if price is not None and price > 0:
            return price
    return None


def pct_change(last: float, prev_close: float) -> float | None:
    if prev_close <= 0:
        return None
    return (last - prev_close) / prev_close * 100.0


def rvol(volume_today: float, avg_vol_20d: float) -> float | None:
    """Simplified relative volume: today's cumulative volume over the 20-day
    average *full-day* volume. Most meaningful later in the session -- early
    in the day it understates RVOL since avg_vol_20d is an end-of-day figure.
    A time-of-day-normalized version is a v3 follow-up.
    """
    if avg_vol_20d <= 0:
        return None
    return volume_today / avg_vol_20d


def dollar_volume(volume_today: float, last: float) -> float:
    return volume_today * last


def is_hod(last: float, day_high: float | None) -> bool:
    return day_high is not None and last >= day_high


def is_lod(last: float, day_low: float | None) -> bool:
    return day_low is not None and last <= day_low


def spread_pct(bid: float | None, ask: float | None) -> float | None:
    """Bid-ask spread as a % of the midpoint -- a liquidity/fill-quality
    proxy independent of price level. A stock can have a huge gap and rvol
    and still be an awkward, expensive fill if the spread is wide.
    """
    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
        return None
    mid = (ask + bid) / 2.0
    return (ask - bid) / mid * 100.0
