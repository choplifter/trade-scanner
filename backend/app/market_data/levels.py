"""Horizontal levels price keeps returning to -- the ones it has "kissed"
repeatedly.

This is the arithmetic behind app.indicators.market_structure, moved here so
it is reachable from the scanner and the backtest as well as from the chart.
That matters more than tidiness: the chart is the only way to check a signal
by eye, and a level a strategy targeted must be the same line the chart drew.
Two implementations would drift, and the drift would be invisible -- the
chart would look right and the trade would be aimed somewhere else.

The indicator keeps everything about *drawing* (name, colours, style, how
levels are labelled). Everything about *finding* is here.

Two properties are load-bearing and easy to lose in a port:

**Always measured on hourly bars.** Not a default -- re-measuring per
timeframe produces disjoint sets. Measured on MARA, minute bars gave levels
at 9.03/9.57/9.85/10.18/10.43/10.64 and hourly bars 11.39/11.96/12.65/
13.28/14.00/14.87: every line marked on the hourly chart vanished on the way
down to the execution timeframe.

**Pivots look forward.** A pivot is a centred rolling extreme, so a bar is
only known to be a turn once _PIVOT_WINDOW more bars exist. On a chart that
is free, because the history is complete. In a backtest it is look-ahead, and
find_levels cannot detect the misuse -- see levels_as_of, which is the only
form a backtest may use.
"""

import numpy as np
import pandas as pd

# Jessica Ramos's "at least 3 kisses": two touches are a line through any two
# points, three is the first count that says price is respecting the level.
MIN_VISITS = 3

# How many levels to keep at most. Raised from 6: with the ranking now by
# rejection strength the extra lines are the next-strongest walls rather than
# the next-busiest shelves, which is what a reader asked for when they could
# point to four levels inside a two-dollar stretch and the chart drew one.
MAX_LEVELS = 8

# A bar is a local extreme over this many bars either side. Three hourly bars
# is a turn that held for most of a session on each side.
#
# Was 10 -- roughly three sessions either way -- which only admitted the very
# largest turns. Measured on SLS, two levels a reader could point to on the
# chart (15.20 and 14.16) had *no* qualifying pivot at all at that setting,
# and five at this one. A level that reversed price for an afternoon is still
# a level; requiring it to hold for a week finds only the extremes of the
# whole range.
_PIVOT_WINDOW = 3

# How far past a turn to look when measuring what that turn was worth, as a
# multiple of the pivot window. The excursion is the distance price travelled
# away before coming back.
_EXCURSION_WINDOW_MULTIPLE = 4

# The smallest excursion that counts as a rejection, as a fraction of the
# visible span. Below it, a turn is chop rather than a level being defended.
#
# This is what separates a support from a shelf. Scoring by how often price
# *visited* a price ranks the consolidation zone first -- it is where price
# spent the most time, by definition -- while the prices a trader marks are
# where price was turned away. Measured on SLS: visits put 12.40/13.03/13.35
# at the top, all inside one range-bound afternoon, while the levels that had
# actually rejected moves sat at 15.x and 13.1 with fewer visits each.
_MIN_EXCURSION_FRACTION = 0.10

# How wide the touch band is, as a percentage of the price span in view.
#
# Two earlier units both failed, and it is worth keeping why. A percentage of
# *price* measured volatility rather than structure: AAPL's one-minute ATR is
# 0.039% of price and WETO's is 1.45%, a 37x spread, so one setting gave 3-8
# kisses on the first and 43-61 on the second. A multiple of ATR fixed that
# within a timeframe but broke across them -- on daily bars one bar's range is
# already a large part of the chart, so the band became unreachable and the
# result was empty (MARA daily 2/2/2, AAPL weekly 1/1/1).
#
# A fraction of the visible span is the one unit that means the same on every
# chart. Measured top/3rd/6th counts at 3.5: MARA 11/9/9 intraday, 11/10/10
# daily, 6/6/5 weekly; AAPL 10/8/8, 6/6/5, 6/6/4.
#
# Narrowed from 3.5 to 2.5 for resolution, not from a measurement. At 3.5 the
# band on an $8-range stock is 0.29, so two levels 0.20 apart merge and the
# line is drawn between them, at a price neither of them is. This is a choice
# about how fine the answer should be and it is worth being plain that it is
# one: on the symbol that prompted it, 2.5 happens to reproduce all four
# levels a reader named while 2.0 reproduces one. That is a knife edge on a
# single symbol, not evidence for 2.5 -- it is set here because a level a
# trader can point to is worth more than a tidier chart, and the ranking
# change below is what actually fixed the problem.
_RANGE_FRACTION_PCT = 2.5

# Floor for the band, as a percentage of price, for a halted or barely-traded
# name whose bars are all but identical -- its span is nearly zero, and so
# would its band be, touching nothing.
_MIN_TOLERANCE_PCT = 0.05

# How far price must pull away before returning counts as a new visit, as a
# multiple of the band. Without it, price chopping inside a consolidation
# re-enters the band dozens of times without ever leaving the area.
_DEPARTURE_MULTIPLE = 3.0

# How close two levels have to be before they are suspected of describing the
# same zone. Distance alone does not settle it -- see is_separated.
_SEPARATION_MULTIPLE = 2.0

# Below this, two levels are never both kept however deep the trough between
# them: lines closer than the band they are measured with overlap, and a dip
# of one visit out of seven is noise. Observed on MARA, which offered 13.28
# and 13.02 -- 0.96 bands apart -- as separate levels.
_MIN_SEPARATION_MULTIPLE = 1.0

# How far the visit count must sag between two nearby levels before they are
# genuinely separate. Distance alone got this wrong: on MARA's hourly bars the
# counts read 6 at 11.00, 4 through 11.10-11.20 and 6 again at 11.40 -- two
# levels with a real trough. Suppressing on distance threw the lower one away
# and drew one line 0.40 above where price had been turning, a 3.5% error in
# exactly the number a stop is placed against.
_MIN_TROUGH_DROP = 1

# Steps per band when scanning inside a zone or across a gap. Each step is one
# vectorised pass over the bars.
_SCAN_STEPS_PER_BAND = 8

# Below this there is not enough history for "repeatedly" to mean anything. A
# fresh listing gets no levels rather than levels drawn from three days.
MIN_BARS = 60


def touch_band(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray) -> float:
    """Half-width of the band that counts as "at this level"."""
    span = float(highs.max() - lows.min())
    floor = float(np.median(closes)) * _MIN_TOLERANCE_PCT / 100.0
    return max(span * _RANGE_FRACTION_PCT / 100.0, floor)


def pivot_prices(highs: np.ndarray, lows: np.ndarray) -> np.ndarray:
    """Prices at which the move actually turned.

    Centred rolling extremes: a bar qualifies when its high is the highest,
    or its low the lowest, across the window on *both* sides of it. That
    two-sidedness is what makes it a turn rather than a new extreme, and it is
    also why the result is only knowable in hindsight (see the module
    docstring). Plateaus yield several adjacent pivots at one price, which is
    harmless -- clustering collapses them.
    """
    span = 2 * _PIVOT_WINDOW + 1
    high_series = pd.Series(highs, dtype=float)
    low_series = pd.Series(lows, dtype=float)

    pivot_highs = high_series[high_series >= high_series.rolling(span, center=True).max()]
    pivot_lows = low_series[low_series <= low_series.rolling(span, center=True).min()]

    prices = pd.concat([pivot_highs, pivot_lows]).to_numpy(dtype=float)
    return prices[np.isfinite(prices) & (prices > 0)]


def rejections(highs: np.ndarray, lows: np.ndarray, span: float) -> list[tuple[float, float]]:
    """(price, excursion) for every turn worth calling a rejection.

    A pivot says price turned; the excursion says whether the turn mattered.
    A high that gave way to a 6% drop is a level being defended; one followed
    by a 0.3% wobble is noise that happens to be a local extreme, and there
    are far more of the second kind -- which is why counting pivots alone
    still ranks a quiet range above a wall price kept bouncing off.
    """
    window = _PIVOT_WINDOW
    reach = window * _EXCURSION_WINDOW_MULTIPLE
    floor = span * _MIN_EXCURSION_FRACTION
    found: list[tuple[float, float]] = []

    for i in range(window, len(highs) - window):
        if highs[i] >= highs[i - window : i + window + 1].max():
            after = lows[i + 1 : i + 1 + reach]
            if after.size and highs[i] - after.min() >= floor:
                found.append((float(highs[i]), float(highs[i] - after.min())))
        if lows[i] <= lows[i - window : i + window + 1].min():
            after = highs[i + 1 : i + 1 + reach]
            if after.size and after.max() - lows[i] >= floor:
                found.append((float(lows[i]), float(after.max() - lows[i])))

    return found


def rejection_strength(
    turns: list[tuple[float, float]], level: float, band: float
) -> float:
    """Total excursion rejected at this price. The score a level is ranked by."""
    return sum(excursion for price, excursion in turns if abs(price - level) <= band)


def cluster(prices: np.ndarray, band: float) -> list[float]:
    """Pivots within a band of each other describe one level, not several.

    Takes each group's median rather than its mean, so one outlying pivot at
    the edge cannot drag the line off the prices the rest of the group agrees
    on.
    """
    if prices.size == 0:
        return []
    ordered = np.sort(prices)
    levels: list[float] = []
    group = [ordered[0]]
    for price in ordered[1:]:
        if price - group[0] > band:
            levels.append(float(np.median(group)))
            group = [price]
        else:
            group.append(price)
    levels.append(float(np.median(group)))
    return levels


def count_visits(highs: np.ndarray, lows: np.ndarray, level: float, band: float) -> int:
    """How many separate times price came back to this level.

    Two bands, not one. Price entering the inner band is a visit; it does not
    become eligible to score another until it has left the outer band
    entirely. That hysteresis is what counts *returns* rather than band
    crossings -- a run of bars on the level, or price chopping across it
    inside a consolidation, is one test however many bars touch it.

    The state looks sequential, but only the *order* of the two kinds of event
    matters and bars that are neither touching nor clear leave it alone. So
    the bars reduce to a sequence of +1 (touching) and -1 (clear), and a visit
    is a +1 whose predecessor was a -1, plus the first event if it is a +1 --
    a vectorised pass, which matters because the scans below evaluate it at
    many candidate prices.
    """
    departure = band * _DEPARTURE_MULTIPLE

    touching = (lows <= level + band) & (highs >= level - band)
    clear = (lows > level + departure) | (highs < level - departure)

    events = np.where(touching, 1, np.where(clear, -1, 0))
    events = events[events != 0]
    if events.size == 0:
        return 0
    return int((events[0] == 1) + np.count_nonzero((events[1:] == 1) & (events[:-1] == -1)))


def best_in_zone(highs: np.ndarray, lows: np.ndarray, level: float, band: float) -> float:
    """The price inside one band that the most separate visits actually hit.

    A cluster's median says where its pivots sit; it does not say where price
    kept turning. Ties break towards the cluster's own centre rather than
    towards an edge.
    """
    step = band / _SCAN_STEPS_PER_BAND
    candidates = np.arange(level - band, level + band + step, step)
    scored = [
        (count_visits(highs, lows, price, band), -abs(price - level), price)
        for price in candidates
    ]
    return float(max(scored)[2])


def is_separated(highs: np.ndarray, lows: np.ndarray, a: float, b: float, band: float) -> bool:
    """Whether two nearby levels are two levels, or one zone found twice.

    Decided by what happens *between* them rather than by how far apart they
    are: if the visit count sags on the way across, price was turning at two
    distinct prices. A flat stretch means one zone, and only the stronger
    survives.
    """
    low, high = (a, b) if a < b else (b, a)
    step = band / _SCAN_STEPS_PER_BAND
    between = np.arange(low + step, high, step)
    if between.size == 0:
        return False
    trough = min(count_visits(highs, lows, price, band) for price in between)
    weaker = min(count_visits(highs, lows, a, band), count_visits(highs, lows, b, band))
    return trough <= weaker - _MIN_TROUGH_DROP


def find_levels(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    min_visits: int = MIN_VISITS,
    max_levels: int = MAX_LEVELS,
) -> list[tuple[float, int]]:
    """(price, visits) for each surviving level, strongest first.

    Feed this **hourly** bars. Feeding it the execution timeframe is not a
    coarser or finer version of the same answer, it is a different set of
    levels -- see the module docstring for the measurement.

    Callers replaying history must use levels_as_of instead: pivots are
    centred, so a level found over a whole series knows about bars that had
    not printed yet.
    """
    if highs.size < MIN_BARS:
        return []

    band = touch_band(highs, lows, closes)
    if band <= 0:
        return []

    span = float(highs.max() - lows.min())
    turns = rejections(highs, lows, span)
    if not turns:
        return []

    # Each cluster nominates a zone; the scan then moves the line to the price
    # inside that zone which the most separate visits actually hit, rather
    # than to wherever its pivots happened to average out.
    refined = {
        round(best_in_zone(highs, lows, level, band), 4)
        for level in cluster(np.array([p for p, _ in turns], dtype=float), band)
    }
    scored = [
        (level, count_visits(highs, lows, level, band), rejection_strength(turns, level, band))
        for level in refined
    ]
    # Ranked by what the level rejected, filtered by how often it was tested.
    # Two different questions, and using the second for both is what put a
    # quiet consolidation ahead of the prices that actually turned moves --
    # visits measure where price *was*, strength measures what it was stopped
    # from doing.
    qualifying = [
        (level, visits, strength)
        for level, visits, strength in scored
        if visits >= min_visits and strength > 0
    ]

    # Strongest first, and on a tie the higher price, purely so the order is
    # deterministic -- two runs over the same bars must not reorder.
    qualifying.sort(key=lambda item: (-item[2], -item[0]))

    kept: list[tuple[float, int]] = []
    for level, visits, _strength in qualifying:
        gaps = [(other, abs(level - other)) for other, _ in kept]
        if any(gap <= band * _MIN_SEPARATION_MULTIPLE for _, gap in gaps):
            continue
        crowding = [other for other, gap in gaps if gap <= band * _SEPARATION_MULTIPLE]
        if any(not is_separated(highs, lows, level, other, band) for other in crowding):
            continue
        kept.append((level, visits))
        if len(kept) >= max_levels:
            break
    return kept


def to_hourly(bars: list) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Roll intraday bars up into hourly highs, lows and closes.

    The chart feeds find_levels native 1Hour bars from Alpaca; a replay only
    has the 5-minute series it is walking, so it aggregates. The two are not
    identical at session edges -- a native hourly bar is aligned to the
    exchange clock, these buckets to the wall clock -- but they are the same
    resolution, which is the property that matters (see the module docstring
    on what changing resolution does to the answer).
    """
    if not bars:
        empty = np.array([], dtype=float)
        return empty, empty, empty

    buckets: dict = {}
    for bar in bars:
        key = bar.timestamp.replace(minute=0, second=0, microsecond=0)
        current = buckets.get(key)
        if current is None:
            buckets[key] = [bar.high, bar.low, bar.close]
        else:
            current[0] = max(current[0], bar.high)
            current[1] = min(current[1], bar.low)
            current[2] = bar.close

    ordered = [buckets[key] for key in sorted(buckets)]
    return (
        np.array([b[0] for b in ordered], dtype=float),
        np.array([b[1] for b in ordered], dtype=float),
        np.array([b[2] for b in ordered], dtype=float),
    )


def levels_as_of(bars: list, cutoff) -> list[tuple[float, int]]:
    """Levels knowable at `cutoff`, from bars that had already printed.

    The only form a replay may use. find_levels is built on centred pivots,
    so run over a whole series it returns levels that depend on bars which had
    not printed yet -- a backtest using those would be aiming at structure it
    could not have seen, and would look better than the method is.

    Strictly before the cutoff, not up to it: a level is marked from completed
    history, and the bar being traded is not history yet.
    """
    return find_levels(*to_hourly([b for b in bars if b.timestamp < cutoff]))
