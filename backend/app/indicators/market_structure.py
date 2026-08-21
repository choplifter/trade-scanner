"""Horizontal levels price keeps returning to -- the ones it has "kissed"
at least three times.

The idea is the ordinary market-structure one: a price that repeatedly
stopped a move matters more than one that stopped a move once. So candidate
levels are taken from swing pivots (bars that were a local extreme, i.e.
where price actually turned), nearby pivots are merged into one level, and
each surviving level is scored by how many *separate* times price came back
to it.

Counting separate visits rather than touching bars is the load-bearing part.
Price hugging a level for forty minutes is one test of that level, not
forty; without the edge-detection below, any price the stock simply traded
around for a while would outrank the levels it actually reversed at.

Always measured on hourly bars, whatever chart you are looking at. That is
the point rather than an implementation detail: the workflow these levels
serve is to find structure on the hourly chart and then drop to 15m or 5m to
time the entry, which only works if the line stays exactly where it was.

Re-measuring per timeframe breaks that, and not subtly -- measured on MARA,
the minute chart produced levels at 9.03/9.57/9.85/10.18/10.43/10.64 and the
hourly chart 11.39/11.96/12.65/13.28/14.00/14.87. Two disjoint sets: every
line a trader marked on the hourly chart vanished on the way down to the
execution timeframe.

Premarket and afterhours are included, on purpose: this dashboard is built
around gappers, where the overnight range is often exactly where the session
later stalls. The >= 3 visit floor is what keeps a single thin wick from
becoming a level.
"""

import numpy as np
import pandas as pd

NAME = "Market Structure"
KIND = "level"
# Drawn only up to the anchor itself -- the charts these levels are actually
# traded from. Above it they would be sixty days of hourly structure pinned
# to the right-hand edge of a multi-year chart, which is the failure this
# indicator already had once.
MAX_TIMEFRAME = "1Hour"

# Jessica Ramos's "at least 3 kisses", as the user described it: two touches
# are a line through any two points, three is the first count that says price
# is actually respecting the level.
MIN_KISSES = 3

# How many levels to draw at most. A chart carrying every qualifying level is
# a chart with no levels on it -- past roughly half a dozen the reader cannot
# tell which one is worth acting on, and the existing premarket/daily/weekly
# lines are competing for the same space.
MAX_LEVELS = 6

# A bar is a local extreme over this many bars either side. On the hourly
# anchor that is a turn which held for roughly three sessions either way --
# long enough that it was not one busy afternoon, short enough to still be
# this week's structure rather than the year's.
_PIVOT_WINDOW = 10

# How wide the touch band is, as a percentage of the price span on screen.
#
# Two earlier units both failed, and it is worth recording why. A percentage
# of *price* measured volatility rather than structure: AAPL's one-minute ATR
# is 0.039% of price and WETO's is 1.45%, a 37x spread, so one setting gave
# 3-8 kisses on the first and 43-61 on the second. Switching to a multiple of
# ATR fixed that within a timeframe but broke across them -- on daily bars a
# single bar's range is already a large part of the whole chart, so the band
# and its departure threshold became unreachable and the indicator returned
# nothing at all (measured top counts: MARA daily 2/2/2, AAPL weekly 1/1/1).
#
# A fraction of the visible span is the unit that survives both, because it is
# the one thing that means the same on every chart. Measured top/3rd/6th
# counts at this setting: MARA 11/9/9 intraday, 11/10/10 daily, 6/6/5 weekly;
# AAPL 10/8/8, 6/6/5, 6/6/4. It also reproduces the ATR calibration on minute
# bars, where the two land within ~15% of each other -- so this generalises
# the setting that already worked rather than replacing it with a guess.
_RANGE_FRACTION_PCT = 3.5

# Floor for the band, as a percentage of price, for the degenerate case of a
# halted or barely-traded name whose bars are all but identical -- its span is
# nearly zero, and so would its band be, touching nothing.
_MIN_TOLERANCE_PCT = 0.05

# How far price must pull away before returning counts as a new visit, as a
# multiple of the touch band. Without this, price chopping inside a
# consolidation re-enters the band dozens of times without ever leaving the
# area -- which measures where the stock traded, the opposite of what a level
# is for.
_DEPARTURE_MULTIPLE = 3.0

# How close two levels have to be before they are even suspected of
# describing the same zone, as a multiple of the touch band. Distance alone
# does not settle it -- see _is_separated for what does.
_SEPARATION_MULTIPLE = 2.0

# Below this, two levels are never both drawn however deep the trough
# between them, as a multiple of the touch band. A trough can be genuine and
# the pair still undrawable: lines closer together than the band they are
# measured with overlap on screen, and a dip of one visit out of seven is
# noise rather than structure. Observed once the trough test went in --
# MARA offered 13.28 and 13.02, 0.96 bands apart, as separate levels.
_MIN_SEPARATION_MULTIPLE = 1.0

# How far the visit count has to sag between two nearby levels before they
# count as genuinely separate rather than one zone.
#
# Distance alone got this wrong in a way worth recording. On MARA's hourly
# bars the counts read 6 at 11.00, 4 through 11.10-11.20, and 6 again at
# 11.40: two levels with a real trough between them. Suppressing purely on
# distance threw the lower one away and drew a single line 0.40 above where
# price had been turning -- on an $11 stock, a 3.5% error in exactly the
# number a stop is placed against.
_MIN_TROUGH_DROP = 1

# Steps per band when scanning for the best price inside a zone, and when
# sampling the gap between two levels. Finer buys precision in where the line
# lands; each step is one vectorised pass over the bars.
_SCAN_STEPS_PER_BAND = 8

# Below this there is not enough history for "repeatedly" to mean anything.
# The hourly window runs to sixty days, so a normally-listed symbol clears
# this comfortably; a fresh listing does not, and gets no levels rather than
# levels drawn from three days of trade.
_MIN_BARS = 60

# More visits, stronger colour. A family not used by the other level
# indicators (blue premarket, purple weekly, orange monthly, teal daily) so
# these do not read as one of them.
#
# The ramp runs to 9 rather than stopping at 6 because measured counts reach
# 12: with a shorter ramp most of a chart's levels saturate at the darkest
# shade and the colour stops encoding anything -- IREN came back 12x/11x/
# 10x/10x/7x all in one colour, which is five lines claiming equal weight.
_SHADES = {
    3: "#cbb0c6",
    4: "#b98ab0",
    5: "#a86f9f",
    6: "#97548e",
    7: "#86397d",
    8: "#75216c",
}
# Anything past the ramp. Saturating here is fine -- by this point the level
# has been tested far more than the method's threshold and the exact count
# matters less than that it is the strongest thing on the chart.
_STRONGEST = "#5d0f56"


def _shade(kisses: int) -> str:
    return _SHADES.get(kisses, _STRONGEST)


# How these lines are drawn. Width in pixels, dash one of solid / dotted /
# dashed / large-dashed / sparse-dotted. Omit either key for the default.
#
# Heavier than the range levels rather than a differently dashed: these are
# derived from a count of visits rather than read off one completed period,
# and the extra weight says "more evidence behind this line" without adding a
# fourth pattern to tell apart at a glance.
STYLE = {"width": 3, "dash": "dashed"}


def _key(kisses: int, ordinal: int) -> str:
    """Label for one level: how many times it was kissed, plus a
    disambiguator when two levels tie -- the dict the loader returns is keyed
    by these, so they have to be unique."""
    return f"{kisses}x" if ordinal == 1 else f"{kisses}x #{ordinal}"


# Built rather than written out, so every key compute() can emit has a colour
# and none fall through to the loader's grey default.
COLORS = {
    _key(kisses, ordinal): _shade(kisses)
    for kisses in range(MIN_KISSES, 40)
    for ordinal in range(1, MAX_LEVELS + 1)
}


def _tolerance(df: pd.DataFrame) -> float:
    """Half-width of the band that counts as "at this level".

    Scaled to the span the chart is showing, so the same setting behaves the
    same on a $2 gapper and a $300 large cap, and on a minute chart and a
    weekly one -- see _RANGE_FRACTION_PCT for the two units this replaced.
    """
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    span = float(high.max() - low.min())
    floor = float(np.median(close)) * _MIN_TOLERANCE_PCT / 100.0
    return max(span * _RANGE_FRACTION_PCT / 100.0, floor)


def _pivot_prices(df: pd.DataFrame) -> np.ndarray:
    """Prices at which the move actually turned.

    Centred rolling extremes: a bar qualifies when its high is the highest
    (or its low the lowest) across the window on both sides of it. Plateaus
    yield several adjacent pivots at the same price, which is harmless --
    clustering collapses them.
    """
    span = 2 * _PIVOT_WINDOW + 1
    highs = df["high"].astype(float)
    lows = df["low"].astype(float)

    pivot_highs = highs[highs >= highs.rolling(span, center=True).max()]
    pivot_lows = lows[lows <= lows.rolling(span, center=True).min()]

    prices = pd.concat([pivot_highs, pivot_lows]).to_numpy(dtype=float)
    return prices[np.isfinite(prices) & (prices > 0)]


def _cluster(prices: np.ndarray, tolerance: float) -> list[float]:
    """Pivots within a tolerance of each other describe one level, not several.

    Walks the sorted prices and starts a new group as soon as one sits more
    than a tolerance above the group's first member, then takes each group's
    median as its representative -- the median rather than the mean so one
    outlying pivot at the edge of a band cannot drag the line off the prices
    the rest of the group agrees on.
    """
    if prices.size == 0:
        return []
    ordered = np.sort(prices)
    levels: list[float] = []
    group = [ordered[0]]
    for price in ordered[1:]:
        if price - group[0] > tolerance:
            levels.append(float(np.median(group)))
            group = [price]
        else:
            group.append(price)
    levels.append(float(np.median(group)))
    return levels


def _count_kisses(highs: np.ndarray, lows: np.ndarray, level: float, tolerance: float) -> int:
    """How many separate times price came back to this level.

    Two bands, not one. Price entering the inner band is a kiss; it does not
    become eligible to score another until it has left the outer band
    entirely. That hysteresis is what makes this count *returns* rather than
    band crossings -- a run of bars on the level, or price chopping across it
    inside a consolidation, is one test of the level however many individual
    bars touch it.

    The state looks sequential -- whether a touch counts depends on whether
    price has been away since the last one -- but only the *order* of the two
    kinds of event matters, and bars that are neither touching nor clear
    leave the state alone. So the bars reduce to a sequence of +1 (touching)
    and -1 (clear of the level), and a visit is a +1 whose predecessor was a
    -1, plus the first event if it is a +1. That is a vectorised pass, which
    matters because the scans below evaluate it at many candidate prices.
    """
    departure = tolerance * _DEPARTURE_MULTIPLE

    touching = (lows <= level + tolerance) & (highs >= level - tolerance)
    clear = (lows > level + departure) | (highs < level - departure)

    events = np.where(touching, 1, np.where(clear, -1, 0))
    events = events[events != 0]
    if events.size == 0:
        return 0
    return int((events[0] == 1) + np.count_nonzero((events[1:] == 1) & (events[:-1] == -1)))


def _best_in_zone(highs: np.ndarray, lows: np.ndarray, level: float, tolerance: float) -> float:
    """The price inside one band that the most separate visits actually hit.

    A cluster's median says where its pivots sit; it does not say where price
    kept turning. Scanning the band puts the drawn line on the second, which
    is the number a trader reads off the chart. Ties break towards the
    cluster's own centre rather than towards an edge.
    """
    step = tolerance / _SCAN_STEPS_PER_BAND
    candidates = np.arange(level - tolerance, level + tolerance + step, step)
    scored = [
        (_count_kisses(highs, lows, price, tolerance), -abs(price - level), price)
        for price in candidates
    ]
    return float(max(scored)[2])


def _is_separated(
    highs: np.ndarray, lows: np.ndarray, a: float, b: float, tolerance: float
) -> bool:
    """Whether two nearby levels are two levels, or one zone drawn twice.

    Decided by what happens *between* them rather than by how far apart they
    are: if the visit count sags on the way across, price was turning at two
    distinct prices and both are worth drawing. A flat stretch means one
    zone, and only the stronger of the pair survives.
    """
    low, high = (a, b) if a < b else (b, a)
    step = tolerance / _SCAN_STEPS_PER_BAND
    between = np.arange(low + step, high, step)
    if between.size == 0:
        return False
    trough = min(_count_kisses(highs, lows, price, tolerance) for price in between)
    weaker = min(
        _count_kisses(highs, lows, a, tolerance), _count_kisses(highs, lows, b, tolerance)
    )
    return trough <= weaker - _MIN_TROUGH_DROP


def compute(ctx) -> dict:
    df = ctx.hourly_bars
    if df.empty or len(df) < _MIN_BARS:
        return {}

    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)

    tolerance = _tolerance(df)
    if tolerance <= 0:
        return {}

    # Each cluster nominates a zone; the scan then moves the line to the price
    # inside that zone which the most separate visits actually hit, rather
    # than to wherever its pivots happened to average out.
    refined = {
        round(_best_in_zone(highs, lows, level, tolerance), 4)
        for level in _cluster(_pivot_prices(df), tolerance)
    }
    scored = [(level, _count_kisses(highs, lows, level, tolerance)) for level in refined]
    qualifying = [(level, kisses) for level, kisses in scored if kisses >= MIN_KISSES]

    # Most-kissed first, and on a tie the higher price, purely so the order is
    # deterministic -- two runs over the same bars must not relabel the lines.
    qualifying.sort(key=lambda item: (-item[1], -item[0]))

    result: dict[str, float] = {}
    kept: list[float] = []
    counts: dict[int, int] = {}
    for level, kisses in qualifying:
        # Near a level already drawn, the question is whether these are two
        # levels or one zone described twice -- and that is settled by the
        # trough between them, not by the distance. See _is_separated.
        gaps = [(other, abs(level - other)) for other in kept]
        if any(gap <= tolerance * _MIN_SEPARATION_MULTIPLE for _, gap in gaps):
            continue
        crowding = [
            other for other, gap in gaps if gap <= tolerance * _SEPARATION_MULTIPLE
        ]
        if any(not _is_separated(highs, lows, level, other, tolerance) for other in crowding):
            continue
        counts[kisses] = counts.get(kisses, 0) + 1
        result[_key(kisses, counts[kisses])] = level
        kept.append(level)
        if len(kept) >= MAX_LEVELS:
            break
    return result
