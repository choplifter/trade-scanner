"""Day-of-week / OpEx-week seasonality view. Manual load only -- daily bars
barely move a 2-year weekday average minute to minute, so polling would
burn API quota for a number that's visually static.
"""

import dash
import plotly.graph_objects as go
from dash import Input, Output, State, callback, dcc, html

from app.dash_app.async_bridge import run_async
from app.dash_app.state import backend_state
from app.dash_app.theme import ACCENT, TEXT_MUTED
from app.scanners.seasonality import SeasonalityBucket, compute_seasonality

dash.register_page(__name__, path="/seasonality", name="Seasonality")


def layout(**_kwargs):
    return html.Div(
        [
            html.H2("Seasonality"),
            html.Div(
                [
                    dcc.Input(id="seasonality-symbol", type="text", placeholder="Symbol, e.g. AAPL"),
                    html.Button("Load", id="seasonality-load", n_clicks=0),
                ],
                style={"display": "flex", "gap": "10px", "alignItems": "center"},
            ),
            dcc.Graph(id="seasonality-weekday-graph"),
            dcc.Graph(id="seasonality-opex-graph"),
        ]
    )


def _bar_figure(title: str, buckets: list[SeasonalityBucket]) -> go.Figure:
    labels = [b.label for b in buckets]
    values = [b.mean_return_pct if b.mean_return_pct is not None else 0.0 for b in buckets]
    text = [f"n={b.n}" if b.n > 0 else "no data" for b in buckets]
    colors = [ACCENT if b.n > 0 else TEXT_MUTED for b in buckets]

    fig = go.Figure(
        go.Bar(x=labels, y=values, text=text, textposition="outside", marker_color=colors)
    )
    fig.update_layout(
        title=title,
        yaxis_title="Avg daily return %",
        margin=dict(t=40, l=40, r=10, b=10),
    )
    return fig


@callback(
    Output("seasonality-weekday-graph", "figure"),
    Output("seasonality-opex-graph", "figure"),
    Input("seasonality-load", "n_clicks"),
    State("seasonality-symbol", "value"),
    prevent_initial_call=True,
)
def update_seasonality(_n_clicks, symbol):
    empty = go.Figure()
    empty.add_annotation(text="Enter a symbol and click Load", showarrow=False)
    empty.update_layout(xaxis={"visible": False}, yaxis={"visible": False})

    if not symbol:
        return empty, empty

    clients = backend_state.alpaca_clients
    if clients is None:
        return empty, empty
    result = run_async(compute_seasonality(clients, symbol))

    weekday_fig = _bar_figure(f"{result.symbol} -- Avg Return by Day of Week", result.by_weekday)
    opex_fig = _bar_figure(f"{result.symbol} -- Avg Return, OpEx vs Non-OpEx Week", result.by_opex)
    return weekday_fig, opex_fig
