"""Scanner-wide gainers heatmap/treemap.

engine.snapshot_view() is synchronous and reads an in-memory dict -- no
Alpaca I/O -- so this page can poll on the same cadence as the live
scanner engine itself without adding real request load. When markets are
closed, snapshot_view() itself falls back to the most recently completed
session's real gainers (see ScannerEngine.backfill_latest_session_gainers)
rather than returning nothing -- this page just labels that state.
"""

import dash
import pandas as pd
import plotly.express as px
from dash import Input, Output, callback, dcc, html

from app.dash_app.state import backend_state

dash.register_page(__name__, path="/", name="Scanner Heatmap")

_DEFAULT_POLL_MS = 5000

_SCANNER_OPTIONS = [
    {"label": "Market Gainers", "value": "gainers"},
    {"label": "Premarket Gainers", "value": "premarket_gainers"},
]


def layout(**_kwargs):
    poll_ms = _DEFAULT_POLL_MS
    if backend_state.settings is not None:
        poll_ms = int(backend_state.settings.scanner_poll_interval_regular * 1000)

    return html.Div(
        [
            html.H2("Scanner Heatmap"),
            dcc.RadioItems(
                id="heatmap-scanner-view",
                options=_SCANNER_OPTIONS,
                value="gainers",
                inline=True,
            ),
            dcc.Interval(id="heatmap-interval", interval=poll_ms, n_intervals=0),
            dcc.Graph(id="heatmap-graph", style={"height": "80vh"}),
        ]
    )


@callback(
    Output("heatmap-graph", "figure"),
    Input("heatmap-interval", "n_intervals"),
    Input("heatmap-scanner-view", "value"),
)
def update_heatmap(_n_intervals, view_name):
    engine = backend_state.scanner_engine
    rows = engine.snapshot_view(view_name or "gainers") if engine is not None else []
    is_latest_session = engine.is_latest_session_fallback if engine is not None else False

    if not rows:
        fig = px.treemap(names=["No symbols matching this scanner right now"], parents=[""])
        fig.update_layout(margin=dict(t=30, l=10, r=10, b=10))
        return fig

    df = pd.DataFrame([r.model_dump() for r in rows])
    df["root"] = "Gainers"

    fig = px.treemap(
        df,
        path=["root", "symbol"],
        values="dollar_volume_today",
        color="pct_change",
        color_continuous_scale="RdYlGn",
        color_continuous_midpoint=0,
        hover_data={
            "last_price": ":.2f",
            "pct_change": ":.2f",
            "volume_today": ":,.0f",
            "rvol": ":.2f",
            "root": False,
        },
    )
    suffix = " (Last Session)" if is_latest_session else ""
    fig.update_layout(
        title=(view_name or "gainers") + suffix,
        margin=dict(t=30, l=10, r=10, b=10),
    )
    return fig
