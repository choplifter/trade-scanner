"""Candle wick-shape detection -- how much of a bar's high-low range is
body vs. wick, and on which side.

is_shaved_top/is_shaved_bottom each check only *one* side's wick, used
together as a direction-aware momentum-alarm signal alongside a fast
trailing-window price move (see app.market_data.momentum): a big move
*up* is more convincing when the candle closed at/near its high (shaved
top -- still pushing higher, whatever it did on the way there), and a big
move *down* is more convincing when it closed at/near its low (shaved
bottom) -- see app.scanners.formulas.is_momentum_alert, which picks
whichever one matches the move's direction.
"""

# Wick below this fraction of the bar's high-low range counts as "no
# wick." A starting heuristic, not backtested -- see is_momentum_alert's
# own docstring for the same caveat on the paired pct-change threshold.
_DEFAULT_MAX_WICK_RATIO = 0.1


def is_shaved_top(
    open_: float, high: float, low: float, close: float, max_wick_ratio: float = _DEFAULT_MAX_WICK_RATIO
) -> bool:
    """True when the candle's upper wick alone is small relative to its
    full range -- price closed at or very near the day's high, regardless
    of what it did on the low side (a candle that gapped up, sold off hard
    intraday, then rallied back to close at a new high still counts).
    False for a zero-range bar (no movement at all isn't "no wick," it's
    no bar).
    """
    range_ = high - low
    if range_ <= 0:
        return False
    upper_wick = high - max(open_, close)
    return (upper_wick / range_) <= max_wick_ratio


def is_shaved_bottom(
    open_: float, high: float, low: float, close: float, max_wick_ratio: float = _DEFAULT_MAX_WICK_RATIO
) -> bool:
    """True when the candle's lower wick alone is small relative to its
    full range -- price closed at or very near the day's low, the down-
    move mirror of is_shaved_top. False for a zero-range bar.
    """
    range_ = high - low
    if range_ <= 0:
        return False
    lower_wick = min(open_, close) - low
    return (lower_wick / range_) <= max_wick_ratio
