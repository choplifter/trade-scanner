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

import logging
import urllib.parse

import dash
from dash import Input, Output, callback, dcc, html

from app.dash_app.async_bridge import run_async
from app.dash_app.state import backend_state
from app.symbols.info import SymbolInfoResponse, get_symbol_info

logger = logging.getLogger(__name__)

dash.register_page(__name__, path="/symbol", name="Symbol Detail")


def _iframe_src(symbol: str | None) -> str:
    if not symbol:
        return "/analytics/assets/lightweight_chart.html"
    return "/analytics/assets/lightweight_chart.html?symbol=" + urllib.parse.quote(symbol)


def _render_symbol_info(info: SymbolInfoResponse | None) -> list:
    if info is None:
        return [html.P("Select a symbol to see company info and news.", className="symbol-info-empty")]

    header_bits = [b for b in (info.company_name, info.sector, info.industry) if b]
    children = []
    if header_bits:
        children.append(html.P(" · ".join(header_bits), className="symbol-info-header"))
    if info.description:
        children.append(html.P(info.description, className="symbol-info-description"))
    if info.website:
        children.append(
            html.A(info.website, href=info.website, target="_blank", className="symbol-info-website")
        )

    if info.news:
        children.append(html.H4("Recent News", className="symbol-news-title"))
        for item in info.news:
            headline = (
                html.A(item.headline, href=item.url, target="_blank", className="symbol-news-headline")
                if item.url
                else html.Span(item.headline, className="symbol-news-headline")
            )
            children.append(
                html.Div(
                    [headline, html.Span(f" — {item.source}", className="symbol-news-source")],
                    className="symbol-news-item",
                )
            )
    elif not header_bits and not info.description:
        # FMP_API_KEY unset, or FMP's daily quota exhausted, and no recent
        # Alpaca news either -- distinct from "nothing selected" above.
        children.append(html.P("No company info or news available right now.", className="symbol-info-empty"))

    return children


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
                style={"width": "100%", "height": "70vh", "border": "none", "marginTop": "8px"},
            ),
            html.Div(
                _render_symbol_info(None),
                id="symbol-detail-info",
                style={"marginTop": "12px"},
            ),
        ]
    )


@callback(
    Output("symbol-detail-frame", "src"),
    Output("symbol-detail-info", "children"),
    Input("symbol-detail-picker", "value"),
    Input("symbol-detail-refresh", "n_clicks"),
)
def update_symbol_detail(symbol, _n_clicks):
    if not symbol:
        return _iframe_src(symbol), _render_symbol_info(None)

    fundamentals = backend_state.fundamentals
    alpaca = backend_state.alpaca_clients
    if fundamentals is None or alpaca is None or not alpaca.settings.has_credentials:
        return _iframe_src(symbol), _render_symbol_info(None)

    try:
        info = run_async(get_symbol_info(fundamentals, alpaca, symbol))
    except Exception:
        logger.exception("Symbol info fetch failed for %s", symbol)
        info = None

    return _iframe_src(symbol), _render_symbol_info(info)
