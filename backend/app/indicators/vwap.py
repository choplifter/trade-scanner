"""Session-anchored VWAP with 1-standard-deviation bands, reset at the 09:30 ET open.

The VWAP line here is deliberately the same definition as
app.market_data.vwap.SessionVwapState, which the chart already draws from the
/bars `vwap` array: anchored at the regular-session open, premarket excluded,
weighted by Alpaca's own per-bar trade-weighted price where present. Two
implementations of one definition is worth it only because they are checked
against each other -- see tests/test_indicator_vwap.py, which replays the same
bars through both and requires agreement.

What this adds over the hard-wired line is the bands. VWAP alone says where
the average share traded; it says nothing about how tightly. A price 20 cents
above VWAP means something very different on a quiet large cap than on a
name whose whole session has been a 5% range, and the standard deviation of
traded price around VWAP is what separates those. Bands are computed on the
same volume weights as the line, so they describe the distribution of *traded
value*, not of bar closes.

Why not just import SessionVwapState: that class is a stateful accumulator
built to be fed one bar at a time from a live websocket, and it carries the
session-continuation state StreamManager depends on. This is a vectorised
pass over a finished DataFrame. Sharing the code would mean bending one shape
to the other for no gain, so instead they stay independent and a test pins
them together.
"""

from datetime import time

import numpy as np
import pandas as pd

from app.services.market_clock import ET

NAME = "VWAP"
KIND = "series"
COLORS = {
    "VWAP": "#e0a33e",
    "+1 SD": "#8a6d3b",
    "-1 SD": "#8a6d3b",
}

# The regular-session open. Premarket trade is excluded by the same
# convention SessionVwapState follows and every retail platform renders:
# including it would anchor the line to thin, wide-spread prints that the
# session's actual participants never traded against.
_SESSION_OPEN = time(9, 30)


def _typical_price(df: pd.DataFrame) -> pd.Series:
    return (df["high"] + df["low"] + df["close"]) / 3.0


def _points(timestamps: pd.Series, values: pd.Series) -> list[dict]:
    return [
        {"t": ts.isoformat(), "value": None if pd.isna(v) else float(v)}
        for ts, v in zip(timestamps, values)
    ]


def compute(ctx) -> dict:
    df = ctx.minute_bars
    if df.empty:
        return {"VWAP": [], "+1 SD": [], "-1 SD": []}

    timestamps = pd.to_datetime(df["timestamp"], utc=True)
    et = timestamps.dt.tz_convert(ET)

    # Grouped by ET calendar date rather than by a rolling window, so the
    # accumulator restarts every session exactly as SessionVwapState's
    # date-change reset does.
    session = et.dt.date
    regular = et.dt.time >= _SESSION_OPEN

    # Alpaca's per-bar vwap where the feed supplies it, typical price
    # otherwise -- same precedence SessionVwapState applies.
    price = pd.to_numeric(df.get("vwap"), errors="coerce") if "vwap" in df else pd.Series(np.nan, index=df.index)
    price = price.fillna(_typical_price(df))
    volume = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0)

    # Premarket rows contribute zero rather than being dropped, so the output
    # stays row-aligned with the input bars -- the chart pairs these points
    # with bars positionally.
    weighted = (price * volume).where(regular, 0.0)
    weighted_sq = (price * price * volume).where(regular, 0.0)
    weights = volume.where(regular, 0.0)

    cum_pv = weighted.groupby(session).cumsum()
    cum_pv2 = weighted_sq.groupby(session).cumsum()
    cum_vol = weights.groupby(session).cumsum()

    # Before the first regular-session print of a day there is no VWAP yet --
    # None, not zero, and not the prior session's carried forward.
    valid = regular & (cum_vol > 0)
    vwap = (cum_pv / cum_vol).where(valid)

    # Var = E[p²] - E[p]², on the same volume weights. Clipped at zero
    # because floating-point cancellation puts it very slightly negative when
    # every print so far has been at one price -- common in the first minutes
    # of a session, and sqrt of -1e-13 would blank the bands exactly when
    # they should read zero width.
    variance = (cum_pv2 / cum_vol - vwap * vwap).where(valid)
    sigma = np.sqrt(variance.clip(lower=0.0))

    return {
        "VWAP": _points(et, vwap),
        "+1 SD": _points(et, vwap + sigma),
        "-1 SD": _points(et, vwap - sigma),
    }
