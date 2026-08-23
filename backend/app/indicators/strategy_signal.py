"""What the strategy scripts would have done, drawn on the chart.

The one way to check a signal by eye. A backtest reports that a rule was
worth -0.6R; it cannot tell you the entries were all on the wrong side of the
line, or that the "level" it aimed at was a wick nobody traded. Three lines on
a chart can.

This runs the *real* strategy files (app.strategies.loader) over the bars the
chart is already showing -- not a description of them and not a
reimplementation. So a line here disagreeing with the backtest is a bug in one
of them, which is the entire reason it is worth drawing.

It only became possible once the level arithmetic moved to
app.market_data.levels: while it lived inside an indicator, nothing outside
the chart endpoint could reach it, and a strategy could not have aimed at the
same level the chart drew.

Entry, stop and target are drawn for the most recent signal **of the latest
session in view**, not for every signal in the loaded history. Two separate
reasons, and both matter:

A chart carrying forty entries is a chart with no signal on it. And the
minute frame covers about ten sessions, so "most recent in the history"
could be a setup from four days ago -- drawn at today's right-hand edge it
reads as live when it is not, which is worse than drawing nothing.

Earlier sessions are still walked for VWAP (a cumulative accumulator cannot
be joined mid-stream) and are still read for the prior-day and premarket
marks, which is exactly what they are needed for.
"""

import logging

import pandas as pd

from app.indicators.context import IndicatorContext
from app.market_data import levels as market_levels
from app.market_data import session_marks
from app.services.market_clock import ET
from app.strategies import runner
from app.strategies.loader import load_strategies

logger = logging.getLogger(__name__)

NAME = "Strategy Signal"
KIND = "level"

# The strategies run on five-minute bars, which is the resolution the live
# scanner and every intraday backtest use (see bars.MOMENTUM_BAR_MINUTES).
# Above the hourly chart three prices from one intraday setup would be pinned
# to the right-hand edge of a multi-year view, saying nothing.
MAX_TIMEFRAME = "1Hour"

_BAR_MINUTES = 5

COLORS = {
    "Stop": "#d1242f",
    "Target": "#1a7f37",
}

# Solid and thin: these are three prices for one trade, not structure. The
# heavier dashed family belongs to market_structure, whose lines are backed by
# a count of visits rather than by a single setup.
STYLE = {"width": 2, "dash": "dotted"}


class _Bar:
    """The attribute surface a strategy reads. Built from the chart's own
    frame so the lines drawn come from the bars on screen."""

    __slots__ = ("close", "high", "low", "open", "timestamp", "volume", "vwap")

    def __init__(self, timestamp, open_, high, low, close, volume, vwap=None):
        self.timestamp = timestamp
        self.open = open_
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume
        self.vwap = vwap


def five_minute_bars(df: pd.DataFrame) -> list:
    """Bucket the chart's minute frame into the resolution strategies run at.

    Floor-to-the-bucket rather than a rolling window, so a bar here covers the
    same clock minutes as the 5-minute bars the backtest fetched natively.
    """
    if df.empty:
        return []

    frame = df.copy()
    frame["bucket"] = pd.to_datetime(frame["timestamp"]).dt.floor(f"{_BAR_MINUTES}min")

    bars = []
    for bucket, group in frame.groupby("bucket", sort=True):
        volume = float(group["volume"].sum())
        bars.append(
            _Bar(
                timestamp=bucket.to_pydatetime(),
                open_=float(group["open"].iloc[0]),
                high=float(group["high"].max()),
                low=float(group["low"].min()),
                close=float(group["close"].iloc[-1]),
                volume=volume,
                # Volume-weighted mean of the minute vwaps, which is what the
                # 5-minute bar's own vwap would have been. None when the feed
                # omitted it, and SessionVwapState falls back to typical price.
                vwap=_weighted_vwap(group, volume),
            )
        )
    return bars


def _weighted_vwap(group: pd.DataFrame, volume: float) -> float | None:
    values = group["vwap"]
    if values.isna().any() or volume <= 0:
        return None
    return float((values * group["volume"]).sum() / volume)


def levels_for(ctx: IndicatorContext, bars: list) -> tuple:
    """Every level a strategy may aim at: multi-day hourly structure plus the
    day's own reference prices.

    Shared with strategy_entry so the arrow and the target line can never be
    computed against different level sets -- which would put the marker on one
    setup and the lines on another.
    """
    hourly = ctx.hourly_bars
    structure: list = []
    if not hourly.empty:
        structure = [
            price
            for price, _ in market_levels.find_levels(
                hourly["high"].to_numpy(dtype=float),
                hourly["low"].to_numpy(dtype=float),
                hourly["close"].to_numpy(dtype=float),
            )
        ]

    marks = session_marks.marks_for_session(bars, bars[-1].timestamp.astimezone(ET).date())
    return tuple(sorted({*structure, *marks}))


def shared_firings(ctx: IndicatorContext) -> tuple[list, list]:
    """(five-minute bars, firings) for this request, computed once.

    Memoized on the context object because two indicator files need the
    same walk -- this one for the stop/target lines, strategy_entry for the
    marker -- and the walk is the most expensive thing either does (bucket
    the minute frame, re-load the strategies, scan hourly structure, walk a
    session). Both files are re-executed per request while their imports
    are cached, so a module-level cache would be two caches; the context is
    the one object both copies share, and it lives exactly one request.
    """
    cached = getattr(ctx, "_strategy_walk", None)
    if cached is not None:
        return cached

    bars = five_minute_bars(ctx.minute_bars)
    firings: list = []
    if bars:
        strategies, _ = load_strategies()
        if strategies:
            # One walk, shared with the live scanner (see
            # app.strategies.runner), so the chart and the table cannot
            # disagree about how a strategy is run. Errors are swallowed
            # here and not there: a chart must not go blank because a file
            # is mid-edit.
            firings = runner.walk_latest_session(
                ctx.symbol, bars, strategies, levels_for(ctx, bars), swallow_errors=True
            )

    result = (bars, firings)
    ctx._strategy_walk = result  # type: ignore[attr-defined]
    return result


def compute(ctx: IndicatorContext) -> dict:
    _, firings = shared_firings(ctx)
    latest = runner.latest_signal(firings)

    if latest is None:
        return {}

    # Entry is deliberately absent: it is a moment, not a level, and lives in
    # strategy_entry as a marker on its own bar. A price line through the
    # entry crosses the whole chart and cannot say *when*.
    return {
        "Stop": round(latest.stop_price, 4),
        "Target": round(latest.target_price, 4),
    }

