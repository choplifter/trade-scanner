"""Cross-symbol comparison: a correlation matrix and a normalized overlay
chart -- both genuinely impossible in the React app's single-symbol chart.
Manual trigger only, since a single click fans out to N Alpaca bar fetches.
"""

import dash
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dash import Input, Output, callback, dcc, html

from app.dash_app.async_bridge import run_async
from app.dash_app.state import backend_state
from app.market_data.bars import get_daily_bars_multi

dash.register_page(__name__, path="/comparison", name="Comparison")


def layout(**_kwargs):
    engine = backend_state.scanner_engine
    universe = backend_state.universe or {}
    # engine.snapshot_view() itself falls back to the most recently
    # completed session's real gainers when markets are closed (see
    # ScannerEngine.backfill_latest_session_rows), so this default is
    # real data either way, not a placeholder.
    default_symbols = [r.symbol for r in engine.snapshot_view("gainers")[:5]] if engine else []
    options = sorted(set(universe.keys()) | set(default_symbols))

    return html.Div(
        [
            html.H2("Cross-Symbol Comparison"),
            html.Div(
                [
                    dcc.Dropdown(
                        id="comparison-symbols",
                        options=[{"label": s, "value": s} for s in options],
                        value=default_symbols,
                        multi=True,
                        placeholder="Select symbols to compare",
                        style={"width": "480px", "display": "inline-block"},
                    ),
                    html.Button("Compare", id="comparison-run", n_clicks=0),
                ],
                style={"display": "flex", "gap": "10px", "alignItems": "center"},
            ),
            dcc.Graph(id="comparison-correlation-graph"),
            dcc.Graph(id="comparison-overlay-graph"),
        ]
    )


def _empty_pair(message: str) -> tuple[go.Figure, go.Figure]:
    fig = go.Figure()
    fig.add_annotation(text=message, showarrow=False)
    fig.update_layout(xaxis={"visible": False}, yaxis={"visible": False})
    return fig, fig


@callback(
    Output("comparison-correlation-graph", "figure"),
    Output("comparison-overlay-graph", "figure"),
    Input("comparison-run", "n_clicks"),
    Input("comparison-symbols", "value"),
    prevent_initial_call=True,
)
def update_comparison(_n_clicks, symbols):
    if not symbols or len(symbols) < 2:
        return _empty_pair("Select at least 2 symbols and click Compare")

    clients = backend_state.alpaca_clients
    if clients is None:
        return _empty_pair("Backend not ready")
    bars_by_symbol = run_async(get_daily_bars_multi(clients, symbols, lookback_days=180))

    close_series = {}
    for symbol in symbols:
        bars = bars_by_symbol.get(symbol, [])
        if not bars:
            continue
        close_series[symbol] = pd.Series(
            {b.timestamp.date(): b.close for b in bars}
        )

    if len(close_series) < 2:
        return _empty_pair("Not enough data for the selected symbols")

    close_df = pd.DataFrame(close_series).dropna(how="any").sort_index()
    if close_df.shape[0] < 2:
        return _empty_pair("Not enough overlapping trading days for the selected symbols")

    returns_df = close_df.pct_change().dropna(how="all") * 100.0
    corr = returns_df.corr()

    corr_fig = px.imshow(
        corr,
        text_auto=".2f",
        color_continuous_scale="RdBu",
        zmin=-1,
        zmax=1,
        title="Daily Return Correlation",
    )
    corr_fig.update_layout(margin=dict(t=40, l=10, r=10, b=10))

    normalized = (close_df / close_df.iloc[0]) * 100.0
    overlay_fig = go.Figure()
    for symbol in normalized.columns:
        overlay_fig.add_trace(go.Scatter(x=normalized.index, y=normalized[symbol], mode="lines", name=symbol))
    overlay_fig.update_layout(
        title="Normalized Performance (start = 100)",
        yaxis_title="Indexed price",
        margin=dict(t=40, l=40, r=10, b=10),
    )

    return corr_fig, overlay_fig
