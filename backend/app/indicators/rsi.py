"""Wilder's RSI over 14 periods of intraday minute closes.

An oscillator, not a price: it lives between 0 and 100, so it cannot share
the chart's price scale the way an EMA or a Bollinger band can -- drawn
there it would be a flat line at the bottom of a 760-dollar chart. Hence
KIND "oscillator", which the renderer gives a pane of its own below the
candles, with the 30 and 70 guides every reading of it refers to.

Those guides are convention, not signals: in a strong trend RSI sits above
70 for hours and every "overbought" reading is a bad short. What it is good
for is the comparison -- price making a new high while this does not.

Intraday only, for the same reason the Bollinger bands are: fourteen weekly
bars are a quarter of a year, and an oscillator over that period says
nothing about the session being traded. The chart draws series indicators
on the minute feed alone, so "1Min" is the honest ceiling.
"""

import pandas_ta as ta

NAME = "RSI"
KIND = "oscillator"
MAX_TIMEFRAME = "1Min"
LENGTH = 14
COLORS = {"RSI 14": "#c08a2e"}
STYLE = {"width": 1, "dash": "solid"}
# The pane's fixed scale: an oscillator autoscaled to its own range would
# make a quiet 45-55 session look like a wild one.
RANGE = {"min": 0.0, "max": 100.0}
# Drawn across the pane, labelled, in the same muted grey the chart uses
# for its own grid.
GUIDES = [
    {"value": 70.0, "label": "70"},
    {"value": 50.0, "label": "50"},
    {"value": 30.0, "label": "30"},
]


def compute(ctx) -> dict:
    df = ctx.minute_bars
    if df.empty or len(df) <= LENGTH:
        # RSI needs one more bar than its window to have a first value.
        return {"RSI 14": []}
    values = ta.rsi(df["close"], length=LENGTH)
    if values is None:
        return {"RSI 14": []}
    return {
        "RSI 14": [
            {"t": ts.isoformat(), "value": None if v != v else float(v)}  # v != v -> NaN
            for ts, v in zip(df["timestamp"], values)
        ]
    }
