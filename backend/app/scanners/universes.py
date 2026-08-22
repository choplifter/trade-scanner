"""Which symbols a backtest runs over, chosen by a named rule.

A strategy's result is not a property of the strategy alone. Measured on five
liquid large caps the opening range breakout returns +0.76R; on fifty of the
scanner's own gainers it returns -0.46R, with the same code and the same
parameters. Until now the only way to say which universe a number came from
was a comma-separated list pasted into a shell, so two runs a week apart were
not comparable and neither was reproducible.

Two universes, because those are the two the app actually points at:

  * **liquid** -- the most-traded names inside the configured price band.
    What the scanner would show you if it ranked by size rather than by
    movement.
  * **gappers** -- names that repeatedly open away from their prior close.
    What the scanner does show you, most days.

**Selection is made from a window that ends before the test window starts.**
That is the whole reason this is a module and not a one-line sort. Picking
"symbols that gapped" out of the period being tested chooses the names *for*
the days being measured, which flatters any strategy that trades movement --
and it would flatter this one hardest on exactly the days that decide the
result. Selecting on earlier history asks the honest question instead: given
what I knew before the window, does this rule pay inside it?
"""

import logging
from itertools import pairwise

from app.alpaca.client import AlpacaClients
from app.alpaca.universe import UniverseSymbol
from app.market_data.bars import get_daily_bars_multi

logger = logging.getLogger(__name__)

LIQUID = "liquid"
GAPPERS = "gappers"
CHOICES = (LIQUID, GAPPERS)

# What counts as a gap for selection purposes: open against the prior close.
# Deliberately the open and not the day's range -- a symbol that opened flat
# and ran 30% by lunchtime was not knowable at 09:30, and the point of this
# universe is to describe what a trader could have had on screen at the bell.
GAP_PCT = 5.0

# How many separate gap days over the selection window a symbol needs. One is
# an event; several is a habit, and a habit is the only thing that survives
# into the *next* window, which is where it will be measured.
MIN_GAP_DAYS = 3

# How far back to look when selecting, as a multiple of the test window. Long
# enough for MIN_GAP_DAYS to mean something, short enough that the habit is
# still the same one.
SELECTION_MULTIPLE = 2


def _gap_days(bars: list) -> int:
    """Sessions that opened more than GAP_PCT away from the prior close."""
    count = 0
    for prev, bar in pairwise(bars):
        if prev.close <= 0:
            continue
        if abs(bar.open - prev.close) / prev.close * 100 >= GAP_PCT:
            count += 1
    return count


async def select(
    clients: AlpacaClients,
    universe: dict[str, UniverseSymbol],
    kind: str,
    count: int,
    lookback_days: int,
) -> list[str]:
    """`count` symbols of the named kind, most representative first.

    `lookback_days` is the *test* window. The selection window sits before it
    and is derived from it, so a caller cannot accidentally select on the days
    it is about to measure.
    """
    if kind not in CHOICES:
        raise ValueError(f"universe must be one of {CHOICES}, not {kind!r}")

    ranked = sorted(universe.values(), key=lambda u: u.avg_dollar_vol_20d, reverse=True)
    if kind == LIQUID:
        return [u.symbol for u in ranked[:count]]

    # Gap behaviour is measured over a candidate pool rather than the whole
    # universe: a daily-bar pull for several thousand symbols is a large
    # request to answer a question a few hundred can answer as well. The pool
    # is taken by liquidity so the result stays tradeable -- a gapper nobody
    # can get filled in is not a useful test subject.
    pool = [u.symbol for u in ranked[: max(count * 10, 300)]]
    window = lookback_days * (SELECTION_MULTIPLE + 1)
    bars_by_symbol = await get_daily_bars_multi(clients, pool, lookback_days=window)

    scored: list[tuple[int, float, str]] = []
    for symbol, bars in bars_by_symbol.items():
        if len(bars) < 2:
            continue
        # Only the older part of the window -- the newer part is what the
        # backtest is about to walk.
        selection_bars = bars[: max(2, len(bars) - _trading_days(lookback_days))]
        gaps = _gap_days(selection_bars)
        if gaps >= MIN_GAP_DAYS:
            liquidity = next(
                (u.avg_dollar_vol_20d for u in ranked if u.symbol == symbol), 0.0
            )
            scored.append((gaps, liquidity, symbol))

    scored.sort(reverse=True)
    if len(scored) < count:
        logger.info(
            "Only %d of %d candidates gapped at least %d times in the selection window",
            len(scored),
            len(bars_by_symbol),
            MIN_GAP_DAYS,
        )
    return [symbol for _, _, symbol in scored[:count]]


def _trading_days(calendar_days: int) -> int:
    """Roughly how many sessions fit in a span of calendar days.

    Approximate on purpose: it decides where to cut a bar list for selection,
    and being a session out shifts which day the boundary falls on rather than
    letting the test window leak into the selection one -- the cut is taken
    from the older end, so an over-estimate is the safe direction.
    """
    return max(1, round(calendar_days * 5 / 7))
