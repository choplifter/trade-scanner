"""Marubozu-style candle detection -- a bar whose body (|close-open|) makes
up nearly the full high-low range, i.e. almost no wick either side.

Used alongside a fast trailing-window price move (see
app.market_data.momentum) as a momentum-alarm signal: a big move *and* a
candle showing essentially no pullback in either direction is a stronger
"this is happening right now" signal than either alone -- see
app.scanners.formulas.is_momentum_alert.
"""

# Combined upper+lower wick below this fraction of the bar's high-low range
# counts as "no wicks." A starting heuristic, not backtested -- see
# is_momentum_alert's own docstring for the same caveat on the paired
# pct-change threshold.
_DEFAULT_MAX_WICK_RATIO = 0.1


def is_marubozu(
    open_: float, high: float, low: float, close: float, max_wick_ratio: float = _DEFAULT_MAX_WICK_RATIO
) -> bool:
    """True when the candle's combined wick is small relative to its full
    range -- price opened near one extreme and closed near the other, with
    little pullback in between. False for a zero-range bar (no movement at
    all isn't "no wicks," it's no bar).

    upper_wick + lower_wick simplifies to range - body: upper_wick is
    high - max(open, close), lower_wick is min(open, close) - low, and
    body is max(open, close) - min(open, close) by definition.
    """
    range_ = high - low
    if range_ <= 0:
        return False
    body = abs(close - open_)
    wick = range_ - body
    return (wick / range_) <= max_wick_ratio
