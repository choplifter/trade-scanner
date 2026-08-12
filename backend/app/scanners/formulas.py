"""Pure scanner math. Kept free of Alpaca SDK types so it's easy to unit test."""

from datetime import datetime


# How far outside the day's own recorded [day_low, day_high] range a single
# latest_trade print is allowed to sit before it's treated as a bad/stale
# tick rather than a real move -- a genuine trade prices into that same
# day's aggregated high/low by definition, so anything wildly outside it
# (seen in practice: a thin backstop-admitted penny name reporting a single
# ~20-40x-off print on IEX, then correcting on the next poll) almost
# certainly isn't real. 2x is generous on purpose: real penny-stock
# intraday moves of 50-100% happen and shouldn't get discarded, only the
# implausible multi-hundred-percent single-tick discontinuities should.
_LAST_TRADE_SANITY_MULTIPLE = 2.0


def resolve_last_price(
    latest_trade_price: float | None,
    daily_bar_close: float | None,
    previous_daily_bar_close: float | None,
    day_low: float | None = None,
    day_high: float | None = None,
) -> float | None:
    """Best available "current" price, in order of freshness.

    latest_trade covers premarket/afterhours ticks that the daily bar may not
    reflect yet; daily_bar.close covers regular-session symbols with no very
    recent print; previous_daily_bar.close is the last resort for a symbol
    that hasn't traded at all today (e.g. still closed/illiquid).

    latest_trade_price is discarded in favor of daily_bar_close if it falls
    outside day_low/day_high by more than _LAST_TRADE_SANITY_MULTIPLE -- see
    that constant's docstring. day_low/day_high are only meaningful once the
    regular session has started (Alpaca's daily bar doesn't aggregate
    premarket), so the check is skipped whenever either is missing rather
    than rejecting legitimate premarket/afterhours prints.
    """
    if (
        latest_trade_price is not None
        and latest_trade_price > 0
        and day_low is not None
        and day_high is not None
        and day_low > 0
        and (
            latest_trade_price > day_high * _LAST_TRADE_SANITY_MULTIPLE
            or latest_trade_price < day_low / _LAST_TRADE_SANITY_MULTIPLE
        )
    ):
        latest_trade_price = None

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


def is_stale(last_trade_at: datetime | None, now: datetime, threshold_seconds: float) -> bool:
    """True when a row's price isn't backed by a recently-confirmed trade
    or daily bar (or has no such backing at all).

    The engine recomputes every row on every poll tick regardless of
    whether the feed actually reported anything new, so a symbol whose
    feed has stopped seeing prints (e.g. it's genuinely trading elsewhere
    on the tape, outside single-exchange IEX coverage) keeps producing the
    same price/pct_change every tick, indistinguishable from a symbol that
    printed a fresh trade seconds ago. Without this check that stuck value
    can sit at the top of a ranked view (e.g. "gainers") indefinitely,
    looking exactly as current as rows the feed is actually still
    confirming.
    """
    if last_trade_at is None:
        return True
    return (now - last_trade_at).total_seconds() > threshold_seconds


# A magnitude multiplier for symbols with a recent news headline attached --
# a catalyst behind a move makes it more likely to persist/continue than an
# unexplained one, per this app's own scanner_history.sqlite3 win-rate
# analysis (news-catalyst picks ran ~8.5pp higher win rate across all three
# ranked views).
_CATALYST_BOOST = 1.15

# Above this RVOL, the same history analysis found win rate *drops*
# (25.6% win rate, -10.38% avg return vs. the overall average) rather than
# rising further -- consistent with "gap and crap": an extreme, sudden
# volume spike is as often exhaustion/a blow-off top as it is genuine
# continuation. So past this point extreme RVOL is treated as a fade-risk
# warning, not an extra bullish signal.
_FADE_RISK_RVOL = 15.0
_FADE_RISK_DISCOUNT = 0.7


def is_fade_risk(rvol: float | None) -> bool:
    return rvol is not None and rvol > _FADE_RISK_RVOL


def rank_score(magnitude: float, has_headline: bool, rvol: float | None) -> float:
    """Ranking magnitude adjusted for the two data-backed signals above.

    `magnitude` is whatever the view already ranks by (pct_change for
    gainers/losers, dollar volume for most-active) -- this only rescales it,
    it never changes sign or reorders which symbols even qualify for a view.
    """
    score = magnitude
    if has_headline:
        score *= _CATALYST_BOOST
    if is_fade_risk(rvol):
        score *= _FADE_RISK_DISCOUNT
    return score


def spread_pct(bid: float | None, ask: float | None) -> float | None:
    """Bid-ask spread as a % of the midpoint -- a liquidity/fill-quality
    proxy independent of price level. A stock can have a huge gap and rvol
    and still be an awkward, expensive fill if the spread is wide.
    """
    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
        return None
    mid = (ask + bid) / 2.0
    return (ask - bid) / mid * 100.0
