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

from app.market_data import levels

NAME = "Market Structure"
KIND = "level"
# Drawn only up to the anchor itself -- the charts these levels are actually
# traded from. Above it they would be sixty days of hourly structure pinned
# to the right-hand edge of a multi-year chart, which is the failure this
# indicator already had once.
MAX_TIMEFRAME = "1Hour"

# Jessica Ramos's "at least 3 kisses", as the user described it: two touches
# are a line through any two points, three is the first count that says price
# is actually respecting the level. The default in app.market_data.levels is
# the same number -- named here as well because it is the method's threshold,
# and a reader of this file should not have to follow an import to find it.
MIN_KISSES = levels.MIN_VISITS

# How many levels to draw at most. A chart carrying every qualifying level is
# a chart with no levels on it -- past roughly half a dozen the reader cannot
# tell which one is worth acting on, and the existing premarket/daily/weekly
# lines are competing for the same space.
MAX_LEVELS = levels.MAX_LEVELS

# How far apart two prices have to be before they are separate levels, as a
# percentage of the visible span. **This is the dial worth turning**, and it
# is here rather than in market_data.levels because this file is a drop-in --
# edit it and the next chart request picks it up, no restart.
#
# Lower draws finer structure and more of it; higher merges nearby prices into
# one line. There is no correct value and the measurements say so: at 2.5 SLS
# reproduces three of four levels a reader named and ALOY none of its one; at
# 2.0 ALOY reproduces its one and SLS falls to one of four. Two symbols, two
# answers, so this is a matter of taste about how fine a chart should read.
BAND_PCT_OF_RANGE = 2.5

# The tuning that decides *where* a level is -- pivot window, band width,
# departure and separation rules -- lives in app.market_data.levels, along
# with the measurements behind each number. It is not restated here: a
# second copy would be edited and change nothing, which is the exact
# failure mode this file's own history warns about.

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


def compute(ctx) -> dict:
    """Find the levels, then label them for the chart.

    The finding lives in app.market_data.levels so the scanner and the
    backtest can reach the same numbers -- see that module on why one
    implementation matters here specifically: the chart is how a signal gets
    checked by eye, and a second copy would drift invisibly.
    """
    df = ctx.hourly_bars
    if df.empty:
        return {}

    found = levels.find_levels(
        df["high"].to_numpy(dtype=float),
        df["low"].to_numpy(dtype=float),
        df["close"].to_numpy(dtype=float),
        min_visits=MIN_KISSES,
        max_levels=MAX_LEVELS,
        range_fraction_pct=BAND_PCT_OF_RANGE,
    )

    result: dict[str, float] = {}
    counts: dict[int, int] = {}
    for level, kisses in found:
        counts[kisses] = counts.get(kisses, 0) + 1
        result[_key(kisses, counts[kisses])] = level
    return result
