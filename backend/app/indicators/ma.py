"""Three moving averages on the chart's own closes: the fast/slow/trend
set most charts carry. Minute closes on an intraday chart, hourly or daily
closes above it (ctx.chart_bars), so a line always belongs to the candles
it is drawn over.

The lengths, types and colours here are the defaults. What is actually
drawn is the reader's own setting (Settings -> Moving averages), which the
chart applies to the candles on screen -- see utils/movingAverages.ts.
The server keeps computing the defaults because a caller that is not the
chart (an export, a future screener) still needs a number, and because the
sub-series names below are what the chart replaces.
"""

import pandas_ta as ta

NAME = "MA"
KIND = "series"

# (length, type) per line, fast to slow. 9/20 EMA are the two this file
# drew before the third was added; 50 SMA is the trend line it was usually
# read against.
DEFAULTS: tuple[tuple[int, str], ...] = ((9, "ema"), (20, "ema"), (50, "sma"))
LINE_COLORS: tuple[str, ...] = ("#2eb872", "#d6455a", "#7c93b8")


def line_name(length: int, kind: str) -> str:
    return f"{kind.upper()} {length}"


NAMES: tuple[str, ...] = tuple(line_name(length, kind) for length, kind in DEFAULTS)
COLORS = dict(zip(NAMES, LINE_COLORS))
# The formula per line, for the chart to recompute from its own candles --
# see STUDY in app.indicators.loader.
STUDY = {
    line_name(length, kind): {"type": kind, "length": length} for length, kind in DEFAULTS
}


def _to_points(df, series) -> list[dict]:
    return [
        {"t": ts.isoformat(), "value": None if v != v else float(v)}  # v != v -> NaN
        for ts, v in zip(df["timestamp"], series)
    ]


def compute(ctx) -> dict:
    df = ctx.chart_bars
    if df.empty:
        return {name: [] for name in NAMES}
    out = {}
    for length, kind in DEFAULTS:
        values = ta.ema(df["close"], length=length) if kind == "ema" else ta.sma(df["close"], length=length)
        # Fewer bars than the length: pandas_ta answers None. The line still
        # carries the chart's timestamps, with no value on any of them --
        # the same shape as its own warm-up, and nothing is drawn either way.
        out[line_name(length, kind)] = (
            [{"t": ts.isoformat(), "value": None} for ts in df["timestamp"]]
            if values is None
            else _to_points(df, values)
        )
    return out
