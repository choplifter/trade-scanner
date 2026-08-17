"""Scanner-wide benchmark: how every symbol the scanner itself has flagged
(gainers/losers/most active -- not just the 3 AI-curated picks the home
page's "AI Top 3 Trade Ideas" panel tracks) has actually performed since
being flagged, versus SPY. The real self-check on the scanner's own
selection criteria (gap %, RVOL, the movers backstop), not just on
Claude's curation of an already-scanner-selected shortlist.

Reads backend_state directly and calls
app.scanners.benchmark_tracker.compute_performance() -- the same function
GET /api/scanners/benchmark-performance uses -- rather than an HTTP round
trip to its own backend, same pattern as every other Dash page here.
"""

import urllib.parse

import dash
from dash import Input, Output, callback, dash_table, dcc, html

from app.dash_app.state import backend_state
from app.dash_app.theme import DELTA_DOWN, DELTA_UP, TEXT_MUTED
from app.scanners.benchmark_tracker import compute_performance

dash.register_page(__name__, path="/benchmark", name="Scanner Benchmark")

_POLL_MS = 30_000

_VIEW_LABEL = {
    "gainers": "Gainers",
    "losers": "Losers",
    "most_active": "Most Active",
    "moderate_movers": "Moderate 3-8%",
}


def _symbol_link(symbol: str) -> str:
    """Markdown link to the shared /symbol page (see symbol_detail.py's
    `symbol` query-param support) -- opens in a new tab so following it
    doesn't lose this table's current sort/scroll position."""
    return f"[{symbol}](/analytics/symbol?symbol={urllib.parse.quote(symbol)})"


_COLUMNS = [
    {"name": "Symbol", "id": "symbol", "presentation": "markdown"},
    {"name": "Via", "id": "view"},
    {"name": "Flagged", "id": "flagged"},
    {"name": "Entry Gap%", "id": "entry_gap"},
    {"name": "Entry RVol", "id": "entry_rvol"},
    {"name": "Entry $", "id": "entry_price"},
    {"name": "Now $", "id": "now_price"},
    {"name": "Change", "id": "change"},
    {"name": "Benchmark", "id": "benchmark"},
    {"name": "Alpha", "id": "alpha"},
    {"name": "News", "id": "news"},
]

# Sorts the raw picks (numeric fields from compute_performance) rather than
# the formatted display strings -- same lexicographic-vs-numeric mismatch
# fixed in scanner_heatmap.py's table applies here too (e.g. "-8.66%" vs
# "-22.00%" as strings) if left to dash_table's native sort.
_COLUMN_SORT_KEYS = {
    "symbol": lambda p: p["symbol"],
    "flagged": lambda p: p["minutes_since"],
    "entry_gap": lambda p: p["entry_pct_change"],
    "entry_rvol": lambda p: p["entry_rvol"],
    "entry_price": lambda p: p["entry_price"],
    "now_price": lambda p: p["current_price"],
    "change": lambda p: p["pct_change_since_entry"],
    "benchmark": lambda p: p["benchmark_pct_change_since_entry"],
    "alpha": lambda p: p["alpha_vs_benchmark"],
}


def _sorted_picks(picks, sort_by):
    if not sort_by:
        return picks
    key_fn = _COLUMN_SORT_KEYS.get(sort_by[0]["column_id"])
    if key_fn is None:
        return picks
    reverse = sort_by[0].get("direction") == "desc"

    with_value = [p for p in picks if key_fn(p) is not None]
    without_value = [p for p in picks if key_fn(p) is None]
    with_value.sort(key=key_fn, reverse=reverse)
    return with_value + without_value


def _format_pct(value: float | None) -> str:
    if value is None:
        return "—"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


def _table_rows(picks: list[dict]) -> list[dict]:
    return [
        {
            "symbol": _symbol_link(p["symbol"]),
            "view": _VIEW_LABEL.get(p["view"], p["view"]),
            "flagged": "just now" if p["minutes_since"] < 1 else f"{round(p['minutes_since'])}m ago",
            "entry_gap": _format_pct(p["entry_pct_change"]),
            "entry_rvol": f"{p['entry_rvol']:.2f}x",
            "entry_price": f"{p['entry_price']:.2f}",
            "now_price": f"{p['current_price']:.2f}" if p["current_price"] is not None else "—",
            "change": _format_pct(p["pct_change_since_entry"]),
            "change_num": p["pct_change_since_entry"],
            "benchmark": _format_pct(p["benchmark_pct_change_since_entry"]),
            "alpha": _format_pct(p["alpha_vs_benchmark"]),
            "alpha_num": p["alpha_vs_benchmark"],
            "news": p["entry_headline"] or "—",
        }
        for p in picks
    ]


def _current_picks() -> tuple[list[dict], str]:
    engine = backend_state.scanner_engine
    tracker = backend_state.scanner_benchmark_tracker
    if engine is None or tracker is None:
        return [], "SPY"
    picks = compute_performance(
        tracker.all(),
        lambda symbol: (row.last_price if (row := engine.rows.get(symbol)) else None),
        engine.benchmark_price,
        # Today's headline as it stands now, reported *beside* the frozen
        # entry_headline rather than replacing it -- see compute_performance.
        lambda symbol: engine.news_cache.get(symbol) if engine.news_cache else None,
    )
    return picks, engine.benchmark_symbol


def layout(**_kwargs):
    picks, benchmark_symbol = _current_picks()

    return html.Div(
        [
            html.H2(f"Scanner vs {benchmark_symbol}"),
            html.P(
                "Every symbol the scanner itself has flagged (gainers/losers/most active), tracked "
                "from the moment it first appeared -- a self-check on the scanner's own selection "
                "criteria, not a trading record. \"Just now\" entries haven't had time to move yet.",
                className="benchmark-disclaimer",
            ),
            dcc.Interval(id="benchmark-interval", interval=_POLL_MS, n_intervals=0),
            dash_table.DataTable(
                id="benchmark-table",
                columns=_COLUMNS,
                data=_table_rows(picks),
                sort_action="custom",
                sort_mode="single",
                markdown_options={"link_target": "_blank"},
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
                    {"if": {"column_id": "view"}, "textAlign": "left"},
                    {
                        "if": {"column_id": "news"},
                        "textAlign": "left",
                        "maxWidth": "260px",
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
                style_data_conditional=[
                    {
                        "if": {"filter_query": "{change_num} >= 0", "column_id": "change"},
                        "color": DELTA_UP,
                    },
                    {
                        "if": {"filter_query": "{change_num} < 0", "column_id": "change"},
                        "color": DELTA_DOWN,
                    },
                    {
                        "if": {"filter_query": "{alpha_num} >= 0", "column_id": "alpha"},
                        "color": DELTA_UP,
                    },
                    {
                        "if": {"filter_query": "{alpha_num} < 0", "column_id": "alpha"},
                        "color": DELTA_DOWN,
                    },
                ],
            ),
        ]
    )


@callback(
    Output("benchmark-table", "data"),
    Input("benchmark-interval", "n_intervals"),
    Input("benchmark-table", "sort_by"),
)
def update_benchmark_table(_n_intervals, sort_by):
    picks, _benchmark_symbol = _current_picks()
    return _table_rows(_sorted_picks(picks, sort_by))
