"""Wilder's RSI over 14 periods of the chart's own closes (ctx.chart_bars).

An oscillator, not a price: it lives between 0 and 100, so it cannot share
the chart's price scale the way an EMA or a Bollinger band can -- drawn
there it would be a flat line at the bottom of a 760-dollar chart. Hence
KIND "oscillator", which the renderer gives a pane of its own below the
candles, with the 30 and 70 guides every reading of it refers to.

Those guides are convention, not signals: in a strong trend RSI sits above
70 for hours and every "overbought" reading is a bad short. What it is good
for is the comparison -- price making a new high while this does not.

Offered at every timeframe: fourteen bars of whatever the chart shows, so
a daily RSI is the fourteen-day one everyone quotes.
"""

import pandas_ta as ta

NAME = "RSI"
KIND = "oscillator"
LENGTH = 14
COLORS = {"RSI 14": "#c08a2e"}
STYLE = {"width": 1, "dash": "solid"}
# See STUDY in app.indicators.loader.
STUDY = {"RSI 14": {"type": "rsi", "length": LENGTH}}
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
    df = ctx.chart_bars
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
