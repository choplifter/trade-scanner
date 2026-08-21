"""Is this name up because its sector is up, or is it running on its own?

Splits every mover's day into three parts that add back up to the move --
market, sector-over-market, and what is left over for the stock itself -- and
puts a second, independent signal beside it: how over-represented the sector
is among the movers. The two are deliberately not merged into one label,
because the informative case is the one where they disagree (a name that looks
independent against a flat sector ETF while its sector is visibly crowded).

The arithmetic lives in app.scanners.sector_attribution; this file only reads
engine state and renders. engine.snapshot_view() is synchronous and reads an
in-memory dict, and sector ETF prices are polled by the engine on the same
can_poll gate as the row prices -- so nothing here does any Alpaca I/O, and
the two sides of every subtraction are always from the same session.
"""

import urllib.parse

import dash
import plotly.graph_objects as go
from dash import Input, Output, callback, dash_table, dcc, html

from app.dash_app.state import backend_state
from app.dash_app.theme import ACCENT, DELTA_DOWN, DELTA_UP, TEXT_MUTED
from app.scanners.sector_attribution import (
    UNKNOWN_SECTOR,
    VERDICT_RANK,
    attribute,
    sector_rollup,
)

dash.register_page(__name__, path="/sector-attribution", name="Sector Attribution")

_POLL_MS = 15_000

# The scanner refreshes rows every few seconds and sector ETFs every minute;
# re-rendering a figure faster than this buys nothing.
_VIEW_OPTIONS = [
    {"label": "Gainers", "value": "gainers"},
    {"label": "Moderate 3-8%", "value": "moderate_movers"},
    {"label": "Most Active", "value": "most_active"},
    {"label": "Losers", "value": "losers"},
]

# Gainers is the default because it is what gets asked about, but the verdict
# does most of its work on moderate_movers: a name up 40% against a sector up
# 1% is arithmetically always independent, whereas +3.2% inside a +1.4% sector
# is a genuine judgement call.
_DEFAULT_VIEW = "gainers"

_FIGURE_TOP_N = 15


def _symbol_link(symbol: str) -> str:
    return f"[{symbol}](/analytics/symbol?symbol={urllib.parse.quote(symbol)})"


def _format_pct(value: float | None) -> str:
    if value is None:
        return "—"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


def _format_share(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.0f}%"


def _format_ratio(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}x"


def _sorted_rows(rows, sort_by, sort_keys):
    """Sort on the parallel numeric field, never on the formatted string --
    dash_table's own sort would compare "-8.66%" against "-22.00%"
    lexicographically. Same helper as scanner_history.py.
    """
    if not sort_by:
        return rows
    key_fn = sort_keys.get(sort_by[0]["column_id"])
    if key_fn is None:
        return rows
    reverse = sort_by[0].get("direction") == "desc"
    with_value = [r for r in rows if key_fn(r) is not None]
    without_value = [r for r in rows if key_fn(r) is None]
    with_value.sort(key=key_fn, reverse=reverse)
    return with_value + without_value


# --- data ---------------------------------------------------------------


def _collect(view: str) -> dict:
    """Everything the page needs, computed once per refresh.

    Returns a `state` string the renderers branch on, so each empty case can
    say what is actually wrong rather than all of them rendering a blank
    table.
    """
    engine = backend_state.scanner_engine
    if engine is None:
        return {"state": "no_engine", "rows": [], "rollup": []}

    rows = engine.snapshot_view(view)
    if not rows:
        return {"state": "no_rows", "rows": [], "rollup": []}

    # The closed-market fallback is last session's close-to-close move, while
    # the ETF prices are today's. Subtracting one from the other is not a
    # stale number, it is a wrong one, so the attribution is withheld
    # entirely rather than shown with a caveat.
    if engine.is_latest_session_fallback:
        return {"state": "fallback", "rows": [], "rollup": []}

    etf_pct = dict(engine.sector_etf_pct_change or {})
    market_pct = etf_pct.get(engine.benchmark_symbol)

    # Breadth is measured against every ranked symbol, not against this view
    # alone -- "14 of 50" is unreadable without knowing whether that sector is
    # 6% or 30% of everything currently ranked.
    ranked_sectors = [
        row.sector
        for name in ("gainers", "losers", "most_active", "moderate_movers")
        for row in engine.snapshot_view(name)
    ]

    attributed = []
    for row in rows:
        result = attribute(row.pct_change, row.sector, etf_pct, market_pct)
        result.update(
            {
                "symbol": row.symbol,
                "company_name": row.company_name or "—",
                "pct_change": row.pct_change,
                "recent_headline": row.recent_headline,
            }
        )
        attributed.append(result)

    return {
        "state": "ok" if etf_pct else "no_etf",
        "rows": attributed,
        "rollup": sector_rollup(attributed, ranked_sectors),
    }


# --- rows ---------------------------------------------------------------

_SECTOR_COLUMNS = [
    {"name": "Sector", "id": "sector"},
    {"name": "ETF", "id": "etf"},
    {"name": "Sector %", "id": "sector_pct"},
    {"name": "Names", "id": "count"},
    {"name": "Share", "id": "share"},
    {"name": "Concentration", "id": "concentration"},
    {"name": "Avg Move", "id": "avg_pct"},
    {"name": "Avg Stock-Specific", "id": "avg_stock_specific"},
    {"name": "Verdicts", "id": "verdicts"},
]

_SECTOR_SORT_KEYS = {
    "sector": lambda r: r["sector"],
    "sector_pct": lambda r: r["sector_pct_num"],
    "count": lambda r: r["count"],
    "share": lambda r: r["share_num"],
    "concentration": lambda r: r["concentration_num"],
    "avg_pct": lambda r: r["avg_pct_num"],
    "avg_stock_specific": lambda r: r["avg_stock_specific_num"],
}


def _sector_rows(rollup: list[dict]) -> list[dict]:
    return [
        {
            "sector": r["sector"],
            "etf": r["etf"] or "—",
            "sector_pct": _format_pct(r["sector_pct"]),
            "sector_pct_num": r["sector_pct"],
            "count": r["count"],
            "share": _format_share(r["share"]),
            "share_num": r["share"],
            "concentration": _format_ratio(r["concentration"]),
            "concentration_num": r["concentration"],
            "avg_pct": _format_pct(r["avg_pct"]),
            "avg_pct_num": r["avg_pct"],
            "avg_stock_specific": _format_pct(r["avg_stock_specific_pct"]),
            "avg_stock_specific_num": r["avg_stock_specific_pct"],
            "verdicts": " / ".join(f"{n} {v}" for v, n in sorted(r["verdicts"].items())),
        }
        for r in rollup
    ]


_SYMBOL_COLUMNS = [
    {"name": "Symbol", "id": "symbol", "presentation": "markdown"},
    {"name": "Company", "id": "company_name"},
    {"name": "Sector", "id": "sector"},
    {"name": "ETF", "id": "etf"},
    {"name": "Move", "id": "pct_change"},
    {"name": "Market", "id": "market_pct"},
    {"name": "Sector", "id": "sector_excess_pct"},
    {"name": "Stock-Specific", "id": "stock_specific_pct"},
    {"name": "Sector Share", "id": "sector_share"},
    {"name": "Verdict", "id": "verdict"},
    {"name": "News", "id": "news"},
]

_SYMBOL_SORT_KEYS = {
    "symbol": lambda r: r["symbol_plain"],
    "sector": lambda r: r["sector"],
    "pct_change": lambda r: r["pct_change_num"],
    "market_pct": lambda r: r["market_pct_num"],
    "sector_excess_pct": lambda r: r["sector_excess_num"],
    "stock_specific_pct": lambda r: r["stock_specific_num"],
    "sector_share": lambda r: r["sector_share_num"],
    "verdict": lambda r: r["verdict_rank"],
}


def _symbol_rows(rows: list[dict]) -> list[dict]:
    return [
        {
            "symbol": _symbol_link(r["symbol"]),
            "symbol_plain": r["symbol"],
            "company_name": r["company_name"],
            "sector": r["sector"] or UNKNOWN_SECTOR,
            "etf": r["etf"] or "—",
            "pct_change": _format_pct(r["pct_change"]),
            "pct_change_num": r["pct_change"],
            "market_pct": _format_pct(r["market_pct"]),
            "market_pct_num": r["market_pct"],
            "sector_excess_pct": _format_pct(r["sector_excess_pct"]),
            "sector_excess_num": r["sector_excess_pct"],
            "stock_specific_pct": _format_pct(r["stock_specific_pct"]),
            "stock_specific_num": r["stock_specific_pct"],
            "sector_share": _format_share(r["sector_share"]),
            "sector_share_num": r["sector_share"],
            "verdict": r["verdict"],
            # Colour and sort off the number, not the label: filter_query
            # compares more reliably against numbers than against strings
            # containing spaces and hyphens.
            "verdict_rank": VERDICT_RANK.get(r["verdict"], 99),
            "news": r["recent_headline"] or "—",
        }
        for r in rows
    ]


# --- figure -------------------------------------------------------------


def _figure(rows: list[dict]) -> go.Figure:
    """The decomposition drawn as it is defined: three parts stacked to the
    total move.

    barmode="relative" rather than "stack" because the parts are frequently
    mixed-sign -- a stock up while its sector is down -- and "stack" would
    draw the negative part on the wrong side of zero.
    """
    plotted = [r for r in rows if r["stock_specific_pct"] is not None]
    plotted = sorted(plotted, key=lambda r: r["pct_change"], reverse=True)[:_FIGURE_TOP_N]
    plotted.reverse()

    if not plotted:
        figure = go.Figure()
        figure.add_annotation(
            text="No attributable rows yet", showarrow=False, font={"color": TEXT_MUTED}
        )
        figure.update_layout(height=320, xaxis={"visible": False}, yaxis={"visible": False})
        return figure

    symbols = [r["symbol"] for r in plotted]
    figure = go.Figure(
        [
            go.Bar(
                name="Market", y=symbols, x=[r["market_pct"] for r in plotted],
                orientation="h", marker_color=ACCENT,
            ),
            go.Bar(
                name="Sector over market", y=symbols,
                x=[r["sector_excess_pct"] for r in plotted],
                orientation="h", marker_color="#eda100",
            ),
            go.Bar(
                name="Stock-specific", y=symbols,
                x=[r["stock_specific_pct"] for r in plotted],
                orientation="h", marker_color=DELTA_UP,
            ),
        ]
    )
    figure.update_layout(
        barmode="relative",
        height=max(320, 26 * len(plotted) + 120),
        margin={"l": 60, "r": 20, "t": 30, "b": 40},
        xaxis_title="% of the day's move",
        legend={"orientation": "h", "y": -0.15},
    )
    return figure


# --- layout -------------------------------------------------------------

_TABLE_STYLE = dict(
    style_table={"overflowX": "auto"},
    style_as_list_view=True,
    style_cell={
        "fontFamily": "system-ui, -apple-system, 'Segoe UI', sans-serif",
        "fontSize": "12px",
        "padding": "6px 10px",
        "textAlign": "right",
        "border": "none",
        "borderBottom": "1px solid #e1e0d9",
    },
    style_cell_conditional=[
        {"if": {"column_id": "symbol"}, "textAlign": "left", "fontWeight": "600"},
        {"if": {"column_id": "sector"}, "textAlign": "left"},
        {"if": {"column_id": "etf"}, "textAlign": "left"},
        {"if": {"column_id": "verdict"}, "textAlign": "left"},
        {"if": {"column_id": "verdicts"}, "textAlign": "left"},
        {
            "if": {"column_id": "company_name"},
            "textAlign": "left",
            "maxWidth": "180px",
            "overflow": "hidden",
            "textOverflow": "ellipsis",
            "whiteSpace": "nowrap",
        },
        {
            "if": {"column_id": "news"},
            "textAlign": "left",
            "maxWidth": "240px",
            "overflow": "hidden",
            "textOverflow": "ellipsis",
            "whiteSpace": "nowrap",
        },
    ],
    style_header={
        "backgroundColor": "#fcfcfb",
        "color": TEXT_MUTED,
        "fontWeight": "600",
        "textTransform": "uppercase",
        "fontSize": "11px",
        "border": "none",
        "borderBottom": "1px solid #e1e0d9",
    },
    style_data={"backgroundColor": "#fcfcfb"},
)


def _delta_conditional(column_id: str, numeric_id: str) -> list[dict]:
    return [
        {"if": {"filter_query": f"{{{numeric_id}}} >= 0", "column_id": column_id}, "color": DELTA_UP},
        {"if": {"filter_query": f"{{{numeric_id}}} < 0", "column_id": column_id}, "color": DELTA_DOWN},
    ]


_BANNERS = {
    "no_engine": "Backend is still starting — no scanner engine yet.",
    "no_rows": "No symbols in this view right now.",
    "fallback": (
        "Markets are closed. The scanner is showing the last completed session, "
        "but the sector ETF prices are today's — subtracting one from the other "
        "would compare two different days, so the attribution is hidden."
    ),
    "no_etf": "Sector ETF prices have not been polled yet — attribution will fill in shortly.",
}


def _banner(state: str):
    message = _BANNERS.get(state)
    if message is None:
        return None
    return html.P(message, style={"color": TEXT_MUTED, "fontSize": "13px"})


def layout(**_kwargs):
    data = _collect(_DEFAULT_VIEW)

    return html.Div(
        [
            html.H2("Sector Attribution"),
            html.P(
                "How much of each mover's day belongs to the market, to its sector, "
                "and to the name itself. The three add up to the move exactly.",
                style={"color": TEXT_MUTED, "fontSize": "13px"},
            ),
            dcc.Interval(id="sector-interval", interval=_POLL_MS, n_intervals=0),
            dcc.Store(id="sector-data", data=data),
            dcc.RadioItems(
                id="sector-view",
                options=_VIEW_OPTIONS,
                value=_DEFAULT_VIEW,
                inline=True,
                style={"marginBottom": "12px"},
            ),
            html.Div(id="sector-banner"),
            html.H3("By sector"),
            dash_table.DataTable(
                id="sector-rollup-table",
                columns=_SECTOR_COLUMNS,
                data=_sector_rows(data["rollup"]),
                sort_action="custom",
                sort_mode="single",
                style_data_conditional=[
                    *_delta_conditional("sector_pct", "sector_pct_num"),
                    *_delta_conditional("avg_pct", "avg_pct_num"),
                    *_delta_conditional("avg_stock_specific", "avg_stock_specific_num"),
                ],
                **_TABLE_STYLE,
            ),
            html.H3("By symbol"),
            dash_table.DataTable(
                id="sector-symbol-table",
                columns=_SYMBOL_COLUMNS,
                data=_symbol_rows(data["rows"]),
                sort_action="custom",
                sort_mode="single",
                style_data_conditional=[
                    *_delta_conditional("pct_change", "pct_change_num"),
                    *_delta_conditional("market_pct", "market_pct_num"),
                    *_delta_conditional("sector_excess_pct", "sector_excess_num"),
                    *_delta_conditional("stock_specific_pct", "stock_specific_num"),
                ],
                **_TABLE_STYLE,
            ),
            html.H3("Decomposition"),
            dcc.Graph(id="sector-figure", figure=_figure(data["rows"])),
            html.P(
                "The sector ETFs are cap-weighted — XLK is Apple and Microsoft, while this "
                "scanner's universe is $1–$50 names. Treat the ETF as a read on sector "
                "sentiment, not as a factor model for these stocks; the concentration column "
                "is there because a small-cap sector run can leave the ETF flat. The verdict "
                "thresholds are judgement calls, not measured — the numbers beside them are "
                "the evidence.",
                style={"color": TEXT_MUTED, "fontSize": "12px", "fontStyle": "italic"},
            ),
        ],
        style={"padding": "16px 20px"},
    )


@callback(
    Output("sector-data", "data"),
    Input("sector-interval", "n_intervals"),
    Input("sector-view", "value"),
)
def _refresh(_n, view):
    return _collect(view or _DEFAULT_VIEW)


@callback(Output("sector-banner", "children"), Input("sector-data", "data"))
def _update_banner(data):
    return _banner((data or {}).get("state", "no_engine"))


@callback(
    Output("sector-rollup-table", "data"),
    Input("sector-data", "data"),
    Input("sector-rollup-table", "sort_by"),
)
def _update_rollup(data, sort_by):
    return _sorted_rows(_sector_rows((data or {}).get("rollup", [])), sort_by, _SECTOR_SORT_KEYS)


@callback(
    Output("sector-symbol-table", "data"),
    Input("sector-data", "data"),
    Input("sector-symbol-table", "sort_by"),
)
def _update_symbols(data, sort_by):
    return _sorted_rows(_symbol_rows((data or {}).get("rows", [])), sort_by, _SYMBOL_SORT_KEYS)


@callback(Output("sector-figure", "figure"), Input("sector-data", "data"))
def _update_figure(data):
    return _figure((data or {}).get("rows", []))
