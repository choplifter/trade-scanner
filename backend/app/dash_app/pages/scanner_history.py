"""Persistent (SQLite-backed) view of how every scanner match has performed
since being flagged -- unlike benchmark.py's live "Scanner vs SPY" page,
which only sees whatever's still resident in ScannerBenchmarkTracker's
in-memory, this-process-only cache, this reads from
app.scanners.history_store.ScannerHistoryStore and survives backend
restarts, covering every match logged over the last several trading days.

Uses app.dash_app.async_bridge.run_async to call the store's async
compute_performance() from Dash's synchronous callbacks -- same bridge
pattern used elsewhere in this app for calling async backend helpers from a
Dash callback (see async_bridge.py's docstring).
"""

import urllib.parse

import dash
from dash import Input, Output, callback, dash_table, dcc, html

from app.dash_app.async_bridge import run_async
from app.dash_app.state import backend_state
from app.dash_app.theme import DELTA_DOWN, DELTA_UP, TEXT_MUTED

dash.register_page(__name__, path="/scanner-history", name="Scanner Match History")

_POLL_MS = 60_000
_DAYS = 7

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
_HORIZON_OPTIONS = [
    {"label": "30m", "value": "30m"},
    {"label": "60m", "value": "60m"},
    {"label": "Latest", "value": "latest"},
]

_SUMMARY_COLUMNS = [
    {"name": "View", "id": "view"},
    {"name": "Sample", "id": "sample_size"},
    {"name": "Win Rate", "id": "win_rate"},
    {"name": "Avg Return", "id": "avg_return"},
    {"name": "Avg Alpha", "id": "avg_alpha"},
]

_PICK_COLUMNS = [
    {"name": "Symbol", "id": "symbol", "presentation": "markdown"},
    {"name": "Via", "id": "view"},
    {"name": "Date", "id": "trading_date"},
    {"name": "Entry Gap%", "id": "entry_gap"},
    {"name": "Entry RVol", "id": "entry_rvol"},
    {"name": "Change", "id": "change"},
    {"name": "Alpha", "id": "alpha"},
    {"name": "News", "id": "news"},
]

_SUMMARY_SORT_KEYS = {
    "view": lambda r: r["view"],
    "sample_size": lambda r: r["sample_size"],
    "win_rate": lambda r: r["win_rate_num"],
    "avg_return": lambda r: r["avg_return_num"],
    "avg_alpha": lambda r: r["avg_alpha_num"],
}

_PICK_SORT_KEYS = {
    "symbol": lambda p: p["symbol"],
    "view": lambda p: p["view"],
    "trading_date": lambda p: p["trading_date"],
    "entry_gap": lambda p: p["entry_pct_change"],
    "entry_rvol": lambda p: p["entry_rvol"],
    "change": lambda p: p["pct_change_since_entry"],
    "alpha": lambda p: p["alpha_vs_benchmark"],
}

_BUCKET_COLUMNS = [
    {"name": "View", "id": "view"},
    {"name": "Bucket", "id": "bucket"},
    {"name": "Sample", "id": "sample_size"},
    {"name": "Win Rate", "id": "win_rate"},
    {"name": "Avg Return", "id": "avg_return"},
]

_BUCKET_SORT_KEYS = {
    "view": lambda r: r["view"],
    "bucket": lambda r: r["bucket"],
    "sample_size": lambda r: r["sample_size"],
    "win_rate": lambda r: r["win_rate_num"],
    "avg_return": lambda r: r["avg_return_num"],
}


def _sorted_rows(rows, sort_by, sort_keys):
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


def _format_pct(value: float | None) -> str:
    if value is None:
        return "—"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


def _fetch_history(days: int = _DAYS, view: str | None = None) -> dict:
    store = backend_state.scanner_history_store
    if store is None:
        return {
            "summary": [],
            "leaderboard_best": [],
            "leaderboard_worst": [],
            "gap_buckets": [],
            "rvol_buckets": [],
        }
    return run_async(store.compute_performance(days=days, view=view))


def _summary_rows(summary: list[dict], horizon: str) -> list[dict]:
    return [
        {
            "view": _VIEW_LABEL.get(s["view"], s["view"]),
            "sample_size": s["sample_size"],
            "win_rate": f"{s['win_rate']:.1f}%" if s["win_rate"] is not None else "—",
            "win_rate_num": s["win_rate"],
            "avg_return": _format_pct(s["avg_return"]),
            "avg_return_num": s["avg_return"],
            "avg_alpha": _format_pct(s["avg_alpha"]),
            "avg_alpha_num": s["avg_alpha"],
        }
        for s in summary
        if s["horizon"] == horizon
    ]


def _bucket_rows(buckets: list[dict]) -> list[dict]:
    return [
        {
            "view": _VIEW_LABEL.get(b["view"], b["view"]),
            "bucket": b["bucket"],
            "sample_size": b["sample_size"],
            "win_rate": f"{b['win_rate']:.1f}%",
            "win_rate_num": b["win_rate"],
            "avg_return": _format_pct(b["avg_return"]),
            "avg_return_num": b["avg_return"],
        }
        for b in buckets
    ]


def _pick_rows(picks: list[dict]) -> list[dict]:
    return [
        {
            "symbol": _symbol_link(p["symbol"]),
            "view": _VIEW_LABEL.get(p["view"], p["view"]),
            "trading_date": p["trading_date"],
            "entry_gap": _format_pct(p["entry_pct_change"]),
            "entry_pct_change": p["entry_pct_change"],
            "entry_rvol": f"{p['entry_rvol']:.2f}x",
            "change": _format_pct(p["pct_change_since_entry"]),
            "pct_change_since_entry": p["pct_change_since_entry"],
            "alpha": _format_pct(p["alpha_vs_benchmark"]),
            "alpha_vs_benchmark": p["alpha_vs_benchmark"],
            "news": p["entry_headline"] or "—",
        }
        for p in picks
    ]


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
        {"if": {"column_id": "view"}, "textAlign": "left"},
        {"if": {"column_id": "trading_date"}, "textAlign": "left"},
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
)


def _delta_conditional(column_id: str, numeric_id: str) -> list[dict]:
    return [
        {"if": {"filter_query": f"{{{numeric_id}}} >= 0", "column_id": column_id}, "color": DELTA_UP},
        {"if": {"filter_query": f"{{{numeric_id}}} < 0", "column_id": column_id}, "color": DELTA_DOWN},
    ]


def layout(**_kwargs):
    history = _fetch_history()

    return html.Div(
        [
            html.H2("Scanner Match History"),
            html.P(
                "Persisted to SQLite, so this survives backend restarts -- every gainer/loser/"
                "most-active match logged over the last 7 trading days, checked periodically "
                "afterward. Leaderboards are always ranked by the most recent price check for "
                "each match, regardless of the horizon selected below.",
                className="benchmark-disclaimer",
            ),
            dcc.Interval(id="history-interval", interval=_POLL_MS, n_intervals=0),
            dcc.Store(id="history-data", data=history),
            dcc.RadioItems(
                id="history-horizon",
                options=_HORIZON_OPTIONS,
                value="latest",
                inline=True,
                style={"marginBottom": "10px"},
            ),
            dash_table.DataTable(
                id="history-summary-table",
                columns=_SUMMARY_COLUMNS,
                data=_summary_rows(history["summary"], "latest"),
                sort_action="custom",
                sort_mode="single",
                style_data_conditional=[
                    *_delta_conditional("avg_return", "avg_return_num"),
                    *_delta_conditional("avg_alpha", "avg_alpha_num"),
                ],
                **_TABLE_STYLE,
            ),
            html.H3("Best Matches", style={"marginTop": "20px"}),
            dash_table.DataTable(
                id="history-best-table",
                columns=_PICK_COLUMNS,
                data=_pick_rows(history["leaderboard_best"]),
                sort_action="custom",
                sort_mode="single",
                markdown_options={"link_target": "_blank"},
                style_data_conditional=[
                    *_delta_conditional("change", "pct_change_since_entry"),
                    *_delta_conditional("alpha", "alpha_vs_benchmark"),
                ],
                **_TABLE_STYLE,
            ),
            html.H3("Worst Matches", style={"marginTop": "20px"}),
            dash_table.DataTable(
                id="history-worst-table",
                columns=_PICK_COLUMNS,
                data=_pick_rows(history["leaderboard_worst"]),
                sort_action="custom",
                sort_mode="single",
                markdown_options={"link_target": "_blank"},
                style_data_conditional=[
                    *_delta_conditional("change", "pct_change_since_entry"),
                    *_delta_conditional("alpha", "alpha_vs_benchmark"),
                ],
                **_TABLE_STYLE,
            ),
            html.H3("Fade Risk — By Gap Size", style={"marginTop": "20px"}),
            html.P(
                "Does a bigger entry gap predict worse subsequent performance (\"gap and crap\") "
                "rather than better? Read small-sample buckets skeptically until more days build up.",
                className="benchmark-disclaimer",
            ),
            dash_table.DataTable(
                id="history-gap-bucket-table",
                columns=_BUCKET_COLUMNS,
                data=_bucket_rows(history["gap_buckets"]),
                sort_action="custom",
                sort_mode="single",
                style_data_conditional=_delta_conditional("avg_return", "avg_return_num"),
                **_TABLE_STYLE,
            ),
            html.H3("Fade Risk — By Entry RVOL", style={"marginTop": "20px"}),
            dash_table.DataTable(
                id="history-rvol-bucket-table",
                columns=_BUCKET_COLUMNS,
                data=_bucket_rows(history["rvol_buckets"]),
                sort_action="custom",
                sort_mode="single",
                style_data_conditional=_delta_conditional("avg_return", "avg_return_num"),
                **_TABLE_STYLE,
            ),
        ]
    )


@callback(
    Output("history-data", "data"),
    Input("history-interval", "n_intervals"),
)
def refresh_history(_n_intervals):
    return _fetch_history()


@callback(
    Output("history-summary-table", "data"),
    Input("history-data", "data"),
    Input("history-horizon", "value"),
    Input("history-summary-table", "sort_by"),
)
def update_summary_table(history, horizon, sort_by):
    rows = _summary_rows(history["summary"], horizon)
    return _sorted_rows(rows, sort_by, _SUMMARY_SORT_KEYS)


@callback(
    Output("history-best-table", "data"),
    Input("history-data", "data"),
    Input("history-best-table", "sort_by"),
)
def update_best_table(history, sort_by):
    rows = _pick_rows(history["leaderboard_best"])
    return _sorted_rows(rows, sort_by, _PICK_SORT_KEYS)


@callback(
    Output("history-worst-table", "data"),
    Input("history-data", "data"),
    Input("history-worst-table", "sort_by"),
)
def update_worst_table(history, sort_by):
    rows = _pick_rows(history["leaderboard_worst"])
    return _sorted_rows(rows, sort_by, _PICK_SORT_KEYS)


@callback(
    Output("history-gap-bucket-table", "data"),
    Input("history-data", "data"),
    Input("history-gap-bucket-table", "sort_by"),
)
def update_gap_bucket_table(history, sort_by):
    rows = _bucket_rows(history["gap_buckets"])
    return _sorted_rows(rows, sort_by, _BUCKET_SORT_KEYS)


@callback(
    Output("history-rvol-bucket-table", "data"),
    Input("history-data", "data"),
    Input("history-rvol-bucket-table", "sort_by"),
)
def update_rvol_bucket_table(history, sort_by):
    rows = _bucket_rows(history["rvol_buckets"])
    return _sorted_rows(rows, sort_by, _BUCKET_SORT_KEYS)
