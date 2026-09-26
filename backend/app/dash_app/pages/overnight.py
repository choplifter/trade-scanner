"""Overnight liquidity: how tradable SPY/QQQ are in the European morning,
around the DAX open. The measuring is in app.overnight.liquidity."""

from __future__ import annotations

import logging

import dash
from dash import Input, Output, State, callback, dash_table, dcc, html
from dash.dash_table.Format import Format, Scheme, Sign

from app.dash_app.async_bridge import run_async
from app.dash_app.pages.backtest import _TABLE_STYLE
from app.dash_app.state import backend_state
from app.dash_app.theme import TEXT_MUTED
from app.overnight.liquidity import WINDOWS, measure, summarize

dash.register_page(__name__, path="/overnight", name="Overnight")

logger = logging.getLogger(__name__)

_INT = Format(precision=0, scheme=Scheme.fixed, group=True)
_TWO = Format(precision=2, scheme=Scheme.fixed)
_ONE = Format(precision=1, scheme=Scheme.fixed)
_PCT0 = Format(precision=0, scheme=Scheme.percentage)
_PCT2 = Format(precision=2, scheme=Scheme.fixed, sign=Sign.positive)

_SUMMARY_COLUMNS = [
    {"name": "Symbol", "id": "symbol"},
    {"name": "Window (German time)", "id": "window"},
    {"name": "Days", "id": "days"},
    {"name": "Minutes traded", "id": "coverage", "type": "numeric", "format": _PCT0},
    {"name": "Volume", "id": "volume", "type": "numeric", "format": _INT},
    {"name": "$ volume", "id": "dollar_volume", "type": "numeric", "format": _INT},
    {"name": "Spread ¢", "id": "spread_cents", "type": "numeric", "format": _ONE},
    {"name": "Spread bps", "id": "spread_bps", "type": "numeric", "format": _TWO},
    {"name": "Range %", "id": "range_pct", "type": "numeric", "format": _TWO},
    {"name": "|Move| %", "id": "abs_move_pct", "type": "numeric", "format": _TWO},
    {"name": "Range in spreads", "id": "range_in_spreads", "type": "numeric", "format": _ONE},
]

_DAY_COLUMNS = [
    {"name": "Day", "id": "day"},
    {"name": "Symbol", "id": "symbol"},
    {"name": "Window", "id": "window"},
    {"name": "Minutes traded", "id": "traded"},
    {"name": "Volume", "id": "volume", "type": "numeric", "format": _INT},
    {"name": "Spread ¢", "id": "spread_cents", "type": "numeric", "format": _ONE},
    {"name": "Spread bps", "id": "spread_bps", "type": "numeric", "format": _TWO},
    {"name": "Range %", "id": "range_pct", "type": "numeric", "format": _TWO},
    {"name": "Move %", "id": "move_pct", "type": "numeric", "format": _PCT2},
    {"name": "Spread samples", "id": "samples", "type": "numeric", "format": _INT},
]

_LABELS = {w.key: w.label for w in WINDOWS}


def layout(**_kwargs):
    return html.Div(
        [
            html.H2("Overnight liquidity"),
            html.P(
                "Alpaca's 24/5 trading opens US stocks and ETFs overnight, 02:00-10:00 German time -- the DAX opens "
                "at 09:00, inside it. This measures what that market looks like for each of the last trading days: "
                "how many minutes traded at all, volume, the quoted spread (the median of a sample every five "
                "minutes), and how far the price travels -- 'range in spreads' is the hour's range "
                "divided by the spread, i.e. how many round-trip costs the typical move is worth. Overnight windows "
                "read Alpaca's Blue Ocean feed; the US premarket and regular hour are there for comparison.",
                className="benchmark-disclaimer",
            ),
            html.Div(
                [
                    html.Div(
                        [
                            html.Label("Symbols", style={"fontSize": "11px", "color": TEXT_MUTED, "display": "block"}),
                            dcc.Checklist(
                                id="ovn-symbols",
                                options=["SPY", "QQQ", "IWM", "EWG"],
                                value=["SPY", "QQQ"],
                                inline=True,
                                inputStyle={"marginLeft": "8px"},
                            ),
                        ]
                    ),
                    html.Div(
                        [
                            html.Label("Trading days", style={"fontSize": "11px", "color": TEXT_MUTED, "display": "block"}),
                            dcc.Input(id="ovn-days", type="number", value=10, min=1, max=30, step=1, style={"width": "70px"}),
                        ]
                    ),
                    html.Button("Run", id="ovn-run", n_clicks=0),
                ],
                style={"display": "flex", "gap": "16px", "alignItems": "flex-end", "marginBottom": "10px", "flexWrap": "wrap"},
            ),
            dcc.Loading(html.Div(id="ovn-notes"), type="circle"),
            html.H4("Median day", style={"marginTop": "16px"}),
            dash_table.DataTable(
                id="ovn-summary",
                columns=_SUMMARY_COLUMNS,
                data=[],
                **{**_TABLE_STYLE, "style_cell_conditional": [{"if": {"column_id": "window"}, "textAlign": "left"}]},
            ),
            html.H4("Every day", style={"marginTop": "16px"}),
            dash_table.DataTable(
                id="ovn-days-table",
                columns=_DAY_COLUMNS,
                data=[],
                sort_action="native",
                page_size=40,
                **{**_TABLE_STYLE, "style_cell_conditional": [{"if": {"column_id": "window"}, "textAlign": "left"}]},
            ),
        ]
    )


def _round(value, digits=4):
    return None if value is None else round(value, digits)


@callback(
    Output("ovn-notes", "children"),
    Output("ovn-summary", "data"),
    Output("ovn-days-table", "data"),
    Input("ovn-run", "n_clicks"),
    State("ovn-symbols", "value"),
    State("ovn-days", "value"),
    prevent_initial_call=True,
)
def run_measurement(_n, symbols, days):
    clients = backend_state.alpaca_clients
    if clients is None:
        return html.P("Backend not ready yet -- try again in a moment.", className="benchmark-disclaimer"), [], []
    if not symbols:
        return html.P("Pick at least one symbol.", className="benchmark-disclaimer"), [], []
    try:
        result = run_async(measure(clients, list(symbols), int(days or 10)))
    except Exception as exc:
        logger.exception("Overnight measurement failed")
        return html.P(f"Failed: {exc}", className="benchmark-disclaimer"), [], []

    summary = [{k: _round(v) if isinstance(v, float) else v for k, v in row.items()} for row in summarize(result.rows)]
    per_day = [
        {
            "day": r.day.isoformat(),
            "symbol": r.symbol,
            "window": _LABELS[r.window],
            "traded": f"{r.minutes_traded} / {r.minutes}",
            "volume": r.volume,
            "spread_cents": _round(r.spread_cents, 2),
            "spread_bps": _round(r.spread_bps, 3),
            "range_pct": _round(r.range_pct),
            "move_pct": _round(r.move_pct),
            "samples": r.samples,
        }
        for r in result.rows
    ]
    notes = html.Div([html.P(n, className="benchmark-disclaimer", style={"margin": "4px 0"}) for n in result.notes])
    return notes, summary, per_day
