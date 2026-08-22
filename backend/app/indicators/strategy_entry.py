"""Where the strategy would have entered, marked at the bar it happened on.

Split from strategy_signal for a reason that is not filing: an entry is a
*moment*, and a horizontal price line cannot express one. Drawn as a level,
the entry crosses the whole chart and touches every earlier bar that traded
at that price, so the one thing a reader needs from it -- when -- is the one
thing it does not say. Stop and target are genuinely levels (they are prices
you leave at, whenever price reaches them) and stay in the other file.

The indicator contract allows one KIND per file, which is what makes this a
second file rather than a second field.

Same shared walk as the scanner and the chart's stop/target lines (see
app.strategies.runner), so all three agree about which bar a setup fired on.
"""

import logging

from app.indicators.context import IndicatorContext
from app.indicators.strategy_signal import (
    five_minute_bars,
    levels_for,
    load_strategies,
)
from app.scanners.exit_rules import SIDE_LONG
from app.strategies import runner

logger = logging.getLogger(__name__)

NAME = "Strategy Entry"
KIND = "marker"

# Same ceiling as the lines it belongs with: three prices and a timestamp from
# one intraday setup say nothing pinned to the edge of a multi-year chart.
MAX_TIMEFRAME = "1Hour"

COLORS = {
    "Long": "#1a7f37",
    "Short": "#d1242f",
}


def compute(ctx: IndicatorContext) -> dict:
    bars = five_minute_bars(ctx.minute_bars)
    if not bars:
        return {}

    strategies, _ = load_strategies()
    if not strategies:
        return {}

    firings = runner.walk_latest_session(
        ctx.symbol, bars, strategies, levels_for(ctx, bars), swallow_errors=True
    )
    if not firings:
        return {}

    index, signal = firings[-1]
    long_side = signal.side == SIDE_LONG
    marker = {
        # Seconds since the epoch: the chart's own time unit, so the marker
        # lands on the bar rather than near it.
        "time": int(bars[index].timestamp.timestamp()),
        # Below a long, above a short -- on the side the trade is not going,
        # so the arrow never sits on top of the move it is marking.
        "position": "belowBar" if long_side else "aboveBar",
        "shape": "arrowUp" if long_side else "arrowDown",
        "text": f"{'LONG' if long_side else 'SHORT'} {signal.entry_price:.2f}",
    }
    return {"Long" if long_side else "Short": [marker]}
