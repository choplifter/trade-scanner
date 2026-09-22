"""The scheduled US releases that move the whole market -- FOMC, CPI,
payrolls, PCE, GDP (app.market_data.macro_calendar's list) -- marked on
the candle they landed in, with a faint column through the whole chart.

Most outsized SPY/QQQ candles sit on one of these, and the column makes
that visible at a glance: the move starts at the line. Placed at the
release minute (FMP's time, UTC), so on an intraday chart it is the 08:30
or 14:00 candle and on a daily chart the day.

Up to the daily chart only: on a weekly candle every week has one, and a
column on every candle says nothing.
"""

NAME = "Macro Releases"
KIND = "marker"
MAX_TIMEFRAME = "1Day"
COLORS = {"Release": "#8250df"}
BAND = "rgba(130, 80, 223, 0.16)"


def compute(ctx) -> dict:
    chart = ctx.chart_bars
    first = chart["timestamp"].iloc[0] if chart is not None and not chart.empty else None
    # Two releases in one minute (CPI with retail sales, say) share a
    # candle, so they share a marker.
    by_moment: dict = {}
    for event in ctx.macro_events:
        at = event.at
        if at is None or at > ctx.as_of or (first is not None and at < first):
            continue
        labels = by_moment.setdefault(at, [])
        if event.label not in labels:
            labels.append(event.label)
    markers = [
        {"time": int(at.timestamp()), "position": "aboveBar", "shape": "square", "text": " · ".join(labels)}
        for at, labels in sorted(by_moment.items())
    ]
    return {"Release": markers} if markers else {}
