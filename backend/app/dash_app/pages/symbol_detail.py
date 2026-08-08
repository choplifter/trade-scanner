"""Fully interactive per-symbol technical view.

Plotly's request/response-per-callback model can't match lightweight-charts'
canvas-based pan/zoom/crosshair feel (every interaction there is a full
figure rebuild). Rather than approximate that, this page embeds the real
lightweight-charts library -- the same one the React app's ChartWidget
uses -- via a self-contained static page (assets/lightweight_chart.html)
loaded in an iframe. It fetches from the same /api/symbols/{symbol}/bars
REST endpoint React does, so both surfaces render identical data.

Dash's only job here is picking the symbol and updating the iframe's src
(which reloads it) -- everything else (timeframe switching, VWAP overlay,
candle/volume rendering) happens client-side inside the iframe.
"""

import urllib.parse

import dash
from dash import Input, Output, callback, dcc, html

from app.dash_app.state import backend_state

dash.register_page(__name__, path="/symbol", name="Symbol Detail")


def _iframe_src(symbol: str | None) -> str:
    if not symbol:
        return "/analytics/assets/lightweight_chart.html"
    return "/analytics/assets/lightweight_chart.html?symbol=" + urllib.parse.quote(symbol)


def layout(**_kwargs):
    universe = backend_state.universe or {}
    options = sorted(universe.keys())
    default_symbol = options[0] if options else None

    return html.Div(
        [
            html.H2("Symbol Detail"),
            html.Div(
                [
                    dcc.Dropdown(
                        id="symbol-detail-picker",
                        options=[{"label": s, "value": s} for s in options],
                        value=default_symbol,
                        placeholder="Select a symbol",
                        style={"width": "220px", "display": "inline-block"},
                    ),
                    html.Button("Refresh", id="symbol-detail-refresh", n_clicks=0),
                ],
                style={"display": "flex", "gap": "16px", "alignItems": "center"},
            ),
            html.Iframe(
                id="symbol-detail-frame",
                src=_iframe_src(default_symbol),
                style={"width": "100%", "height": "85vh", "border": "none", "marginTop": "8px"},
            ),
        ]
    )


@callback(
    Output("symbol-detail-frame", "src"),
    Input("symbol-detail-picker", "value"),
    Input("symbol-detail-refresh", "n_clicks"),
)
def update_symbol_detail(symbol, _n_clicks):
    return _iframe_src(symbol)
