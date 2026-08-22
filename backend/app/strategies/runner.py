"""Running the loaded strategies over one symbol's bars.

Three callers want this now -- the chart indicator, the live scanner, and (in
its own form) the backtest walk -- and they want subtly different answers
from the same pass:

  * the chart asks "what was the last setup of this session", so a reader can
    see where it was and check it by eye;
  * the scanner asks "is one firing right now", because a marker on a live
    table means look here, and a setup from two hours ago does not.

Both are readings of one walk, which is why the walk lives here instead of
being written twice. Writing it twice is the failure this whole package is
shaped around: the two copies would diverge on something small -- which bar
counts as the last one, whether VWAP was fed the skipped days -- and the
chart would then disagree with the table for no visible reason.

VWAP is fed every bar including the sessions that are skipped for signalling.
It is a cumulative accumulator and cannot be joined mid-stream; the earlier
days are also what the prior-session and premarket marks are read from.
"""

import logging

from app.market_data.vwap import SessionVwapState
from app.services.market_clock import ET
from app.strategies.context import Signal, build_context
from app.strategies.loader import LoadedStrategy

logger = logging.getLogger(__name__)


def walk_latest_session(
    symbol: str,
    bars: list,
    strategies: list[LoadedStrategy],
    levels: tuple = (),
    swallow_errors: bool = False,
) -> list[tuple[int, Signal]]:
    """(bar index, signal) for every firing in the most recent session.

    `swallow_errors` is the caller's call, not this module's, and the two
    real callers disagree for good reason: a chart must not go blank because
    a strategy file is mid-edit, while a backtest must stop rather than
    quietly report fewer signals than the rule produced. See loader.
    """
    if not bars or not strategies:
        return []

    latest_day = bars[-1].timestamp.astimezone(ET).date()
    found: list[tuple[int, Signal]] = []

    vwaps: list = []
    vwap_state = SessionVwapState(symbol=symbol)
    for i, bar in enumerate(bars):
        vwaps.append(
            vwap_state.update(
                timestamp=bar.timestamp,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                bar_vwap=getattr(bar, "vwap", None),
            )
        )

        if bar.timestamp.astimezone(ET).date() != latest_day:
            continue

        ctx = build_context(symbol, bars[: i + 1], i, vwaps, vwap_state.premarket_anchored_vwap, levels)
        if ctx is None:
            continue

        for strategy in strategies:
            if swallow_errors:
                try:
                    signal = strategy.evaluate(ctx)
                except Exception:
                    # Logged, never silent: a strategy throwing on every bar
                    # produces no signal, which looks exactly like a strategy
                    # finding no setup.
                    logger.exception(
                        "Strategy %s raised on %s -- skipped", strategy.filename, symbol
                    )
                    continue
            else:
                signal = strategy.evaluate(ctx)
            if signal is not None:
                found.append((i, signal))

    return found


def latest_signal(firings: list[tuple[int, Signal]]) -> Signal | None:
    """The last setup of the session -- what a chart draws."""
    return firings[-1][1] if firings else None


def signals_now(firings: list[tuple[int, Signal]], bar_count: int) -> list[Signal]:
    """Only what is firing on the final bar -- what a live table marks.

    Deliberately stricter than latest_signal. A marker on a live row is an
    invitation to act, and a setup that resolved two hours ago is not one.
    That the two can disagree is correct rather than a defect: the chart is
    showing where the session's last setup *was*.
    """
    last = bar_count - 1
    return [signal for index, signal in firings if index == last]
