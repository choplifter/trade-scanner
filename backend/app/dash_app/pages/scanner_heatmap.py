"""Home page: scanner table, gainers heatmap, symbol detail, and AI trade
ideas in one view.

engine.snapshot_view() is synchronous and reads an in-memory dict -- no
Alpaca I/O -- so this page can poll on the same cadence as the live
scanner engine itself without adding real request load. When markets are
closed, snapshot_view() itself falls back to the most recently completed
session's real data (see ScannerEngine.backfill_latest_session_rows)
rather than returning nothing -- this page just labels that state.

Layout is a 2x2 grid: scanner table top-left, heatmap bottom-left, symbol
detail top-right, AI trade ideas bottom-right. Table and heatmap share one
poll interval/callback so a tick only calls snapshot_view() once. Clicking
a table row, a heatmap tile, or a trade-idea card all load that symbol into
the detail iframe (see pages/symbol_detail.py for why an iframe: same
lightweight-charts widget the React app uses, embedded via
assets/lightweight_chart.html).

Trade ideas call the same app.ai.trade_ideas.generate_trade_ideas() and
TradeIdeaTracker the React AI widget (routers/trade_ideas.py) uses, via
backend_state + async_bridge.run_async rather than an HTTP round-trip to
its own backend -- so a Dash-triggered generation also shows up in the
React app's past-picks performance table, and vice versa.
"""

import logging
import urllib.parse
from datetime import datetime, timezone

import dash
import pandas as pd
import plotly.express as px
from dash import ALL, Input, Output, State, callback, dash_table, dcc, html

from app.ai.trade_idea_tracker import TrackedIdea
from app.ai.trade_ideas import TradeIdea, generate_trade_ideas
from app.dash_app.async_bridge import run_async
from app.dash_app.state import backend_state
from app.dash_app.theme import DELTA_DOWN, DELTA_UP, TEXT_MUTED
from app.symbols.info import SymbolInfoResponse, get_symbol_info

logger = logging.getLogger(__name__)

dash.register_page(__name__, path="/", name="Scanner Heatmap")

_DEFAULT_POLL_MS = 5000

_SCANNER_OPTIONS = [
    {"label": "Market Gainers", "value": "gainers"},
    {"label": "Premarket Gainers", "value": "premarket_gainers"},
    {"label": "Losers", "value": "losers"},
    {"label": "Most Active", "value": "most_active"},
]
_SCANNER_LABELS = {opt["value"]: opt["label"] for opt in _SCANNER_OPTIONS}

_TABLE_COLUMNS = [
    {"name": "Symbol", "id": "symbol"},
    {"name": "Last", "id": "last"},
    {"name": "Chg %", "id": "chg"},
    {"name": "Vol", "id": "vol"},
    {"name": "RVol", "id": "rvol"},
    {"name": "Float", "id": "float_shares"},
    {"name": "Mkt Cap", "id": "market_cap"},
    {"name": "Short %", "id": "short_interest_pct"},
]


def _format_price(value: float) -> str:
    return f"{value:,.2f}"


def _format_pct(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


def _format_volume(value: float) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return f"{value:.0f}"


def _format_rvol(value: float) -> str:
    return f"{value:.2f}x"


# "—" (em dash) for float_shares/market_cap/short_interest_pct -- these
# are only populated when FMP_API_KEY is configured (see
# app.fundamentals.cache.FundamentalsCache), same as the React scanner table.
def _format_shares(value: float | None) -> str:
    if value is None:
        return "—"
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return f"{value:.0f}"


def _format_market_cap(value: float | None) -> str:
    if value is None:
        return "—"
    if value >= 1_000_000_000_000:
        return f"${value / 1_000_000_000_000:.2f}T"
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    return f"${value:.0f}"


def _format_short_interest_pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.2f}%"


# dash_table's sort_action="native" sorts whatever's in `data`, which for
# this table is the formatted display strings ("1.25B", "250.0M", "800.0K")
# -- lexicographic, not numeric, so e.g. "1.25B" < "250.0M" as strings even
# though 1.25B is the bigger number. Every column here is formatted for
# display (M/B/T suffixes, %, x), so native sort is wrong for all of them,
# not just float/market cap. sort_action="custom" + this map lets us sort
# the real ScannerRow values before formatting instead.
_COLUMN_SORT_KEYS = {
    "symbol": lambda r: r.symbol,
    "last": lambda r: r.last_price,
    "chg": lambda r: r.pct_change,
    "vol": lambda r: r.volume_today,
    "rvol": lambda r: r.rvol,
    "float_shares": lambda r: r.float_shares,
    "market_cap": lambda r: r.market_cap,
    "short_interest_pct": lambda r: r.short_interest_pct,
}


def _sorted_rows(rows, sort_by):
    if not sort_by:
        return rows
    key_fn = _COLUMN_SORT_KEYS.get(sort_by[0]["column_id"])
    if key_fn is None:
        return rows
    reverse = sort_by[0].get("direction") == "desc"

    # float_shares/market_cap/short_interest_pct are None when FMP_API_KEY
    # isn't configured or a symbol hasn't been fetched yet -- sort those to
    # the bottom regardless of direction (comparing None to a float raises
    # TypeError, and burying unknowns matches how the "—" cell reads).
    with_value = [r for r in rows if key_fn(r) is not None]
    without_value = [r for r in rows if key_fn(r) is None]
    with_value.sort(key=key_fn, reverse=reverse)
    return with_value + without_value


def _table_rows(rows) -> list[dict]:
    return [
        {
            "symbol": r.symbol,
            "last": _format_price(r.last_price),
            "chg": _format_pct(r.pct_change),
            "vol": _format_volume(r.volume_today),
            "rvol": _format_rvol(r.rvol),
            "float_shares": _format_shares(r.float_shares),
            "market_cap": _format_market_cap(r.market_cap),
            "short_interest_pct": _format_short_interest_pct(r.short_interest_pct),
            "pct_change_num": r.pct_change,
        }
        for r in rows
    ]


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


_TRADE_IDEAS_PROMPT = (
    "Click Generate to have Claude select today's 3 most notable setups from the "
    "gainers scanner, with reasons and a signal score based on gap %, relative "
    "volume, dollar volume, high-of-day status, news, VWAP, momentum, spread, "
    "multi-day context, float, and short interest."
)


def _trade_ideas_message(text: str) -> html.Div:
    return html.Div(text, className="home-ideas-empty")


def _trade_ideas_cards(ideas: list[TradeIdea], disclaimer: str) -> html.Div:
    cards = [
        html.Div(
            [
                html.Span(f"#{i + 1}", className="home-idea-rank"),
                html.Span(idea.symbol, className="home-idea-symbol"),
                html.Span(
                    f"Signal {idea.signal_score}/10",
                    className="home-idea-score",
                    title=(
                        "How many scanner signals (catalyst, VWAP, momentum, spread, "
                        "continuation, range) line up for this pick, not a prediction of outcome"
                    ),
                ),
                html.Span(idea.headline, className="home-idea-headline"),
                html.P(idea.reason, className="home-idea-note"),
            ],
            id={"type": "trade-idea-row", "index": idea.symbol},
            n_clicks=0,
            className="home-idea-card",
        )
        for i, idea in enumerate(ideas)
    ]
    return html.Div(
        [
            html.Div(cards, className="home-ideas-list"),
            html.P(disclaimer, className="home-ideas-disclaimer"),
        ]
    )


def layout(**_kwargs):
    poll_ms = _DEFAULT_POLL_MS
    if backend_state.settings is not None:
        poll_ms = int(backend_state.settings.scanner_poll_interval_regular * 1000)

    engine = backend_state.scanner_engine
    initial_rows = engine.snapshot_view("gainers") if engine is not None else []
    default_symbol = initial_rows[0].symbol if initial_rows else None

    return html.Div(
        [
            html.H2("Scanner Heatmap"),
            dcc.Interval(id="heatmap-interval", interval=poll_ms, n_intervals=0),
            html.Div(
                [
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.H3("Scanner"),
                                    dcc.RadioItems(
                                        id="heatmap-scanner-view",
                                        options=_SCANNER_OPTIONS,
                                        value="gainers",
                                        inline=True,
                                    ),
                                ],
                                className="home-panel-header",
                            ),
                            html.Div(
                                dash_table.DataTable(
                                    id="home-scanner-table",
                                    columns=_TABLE_COLUMNS,
                                    data=_table_rows(initial_rows),
                                    row_deletable=False,
                                    # "custom" (not "native"): see _sorted_rows --
                                    # native sort would sort the formatted display
                                    # strings lexicographically, not the underlying
                                    # numbers.
                                    sort_action="custom",
                                    sort_mode="single",
                                    # No fixed_rows: that switches the table into a
                                    # JS-measured/virtualized layout that computes pixel
                                    # widths/heights once at mount and never re-measures
                                    # on container resize, so the table would stay stuck
                                    # at its original size when the splitter is dragged.
                                    # A plain table + CSS `position: sticky` header (below)
                                    # gets the same sticky-header effect and reflows for
                                    # free since it's just normal DOM/CSS.
                                    # overflowX: with 3 more columns (float/mkt
                                    # cap/short %) the table can now be wider than
                                    # a narrowed splitter panel -- scroll inside
                                    # the table instead of blowing out the layout.
                                    style_table={"height": "100%", "overflowY": "auto", "overflowX": "auto"},
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
                                        {"if": {"column_id": "symbol"}, "textAlign": "left", "fontWeight": "600"}
                                    ],
                                    style_header={
                                        "position": "sticky",
                                        "top": 0,
                                        "zIndex": 1,
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
                                            "if": {"filter_query": "{pct_change_num} >= 0", "column_id": "chg"},
                                            "color": DELTA_UP,
                                        },
                                        {
                                            "if": {"filter_query": "{pct_change_num} < 0", "column_id": "chg"},
                                            "color": DELTA_DOWN,
                                        },
                                    ],
                                    cell_selectable=True,
                                ),
                                className="home-panel-body",
                            ),
                        ],
                        className="home-panel home-panel-table",
                    ),
                    html.Div(
                        [
                            html.Div(html.H3("Heatmap"), className="home-panel-header"),
                            html.Div(
                                dcc.Graph(
                                    id="heatmap-graph",
                                    style={"height": "100%"},
                                    config={"responsive": True},
                                ),
                                className="home-panel-body",
                            ),
                        ],
                        className="home-panel home-panel-heatmap",
                    ),
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.H3("Symbol Detail"),
                                    html.Span(default_symbol or "Select a symbol", id="home-symbol-label"),
                                ],
                                className="home-panel-header",
                            ),
                            html.Div(
                                [
                                    html.Iframe(
                                        id="home-symbol-frame",
                                        src=_iframe_src(default_symbol),
                                        style={"width": "100%", "height": "60%", "border": "none"},
                                    ),
                                    html.Div(
                                        _render_symbol_info(None),
                                        id="home-symbol-info",
                                        style={"marginTop": "8px"},
                                    ),
                                ],
                                className="home-panel-body",
                            ),
                        ],
                        className="home-panel home-panel-symbol",
                    ),
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.H3("AI Top 3 Trade Ideas"),
                                    html.Button("Generate", id="trade-ideas-generate", n_clicks=0),
                                ],
                                className="home-panel-header",
                            ),
                            html.Div(
                                dcc.Loading(
                                    html.Div(_trade_ideas_message(_TRADE_IDEAS_PROMPT), id="trade-ideas-body"),
                                    type="circle",
                                ),
                                className="home-panel-body",
                            ),
                        ],
                        className="home-panel home-panel-ideas",
                    ),
                    html.Div(className="home-splitter home-splitter-v"),
                    html.Div(className="home-splitter home-splitter-h"),
                ],
                className="home-grid",
            ),
        ]
    )


@callback(
    Output("heatmap-graph", "figure"),
    Output("home-scanner-table", "data"),
    Input("heatmap-interval", "n_intervals"),
    Input("heatmap-scanner-view", "value"),
    Input("home-scanner-table", "sort_by"),
)
def update_home_panels(_n_intervals, view_name, sort_by):
    engine = backend_state.scanner_engine
    rows = engine.snapshot_view(view_name or "gainers") if engine is not None else []
    is_latest_session = engine.is_latest_session_fallback if engine is not None else False

    # Table gets the user's chosen sort; the treemap below keeps the
    # engine's own gap%-ranked order regardless -- sorting only makes
    # sense for the flat table, not which tile is biggest in the heatmap.
    table_data = _table_rows(_sorted_rows(rows, sort_by))

    if not rows:
        fig = px.treemap(names=["No symbols matching this scanner right now"], parents=[""])
        fig.update_layout(margin=dict(t=30, l=10, r=10, b=10))
        return fig, table_data

    label = _SCANNER_LABELS.get(view_name, view_name or "gainers")
    df = pd.DataFrame([r.model_dump() for r in rows])
    df["root"] = label

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
            "float_shares": ":,.0f",
            "market_cap": ":,.0f",
            "short_interest_pct": ":.2f",
            "root": False,
        },
    )
    suffix = " (Last Session)" if is_latest_session else ""
    fig.update_layout(
        title=label + suffix,
        margin=dict(t=30, l=10, r=10, b=10),
    )
    return fig, table_data


@callback(
    Output("home-symbol-frame", "src"),
    Output("home-symbol-label", "children"),
    Output("home-symbol-info", "children"),
    Input("home-scanner-table", "active_cell"),
    Input("heatmap-graph", "clickData"),
    Input({"type": "trade-idea-row", "index": ALL}, "n_clicks"),
    State("home-scanner-table", "data"),
    prevent_initial_call=True,
)
def update_home_symbol_detail(active_cell, click_data, _idea_clicks, table_data):
    triggered_id = dash.ctx.triggered_id
    symbol = None

    if triggered_id == "home-scanner-table":
        if active_cell and table_data:
            symbol = table_data[active_cell["row"]]["symbol"]
    elif triggered_id == "heatmap-graph":
        points = (click_data or {}).get("points") or []
        if points and points[0].get("parent"):
            # Leaf (symbol) nodes have a parent ("Gainers"); the root
            # "Gainers" tile itself has none -- ignore clicks on that.
            symbol = points[0].get("label")
    elif isinstance(triggered_id, dict) and triggered_id.get("type") == "trade-idea-row":
        # The ALL-pattern Input also fires (with n_clicks still 0) the
        # moment idea cards first mount -- ctx.triggered[0]["value"] is the
        # actual n_clicks of whichever card fired, so falsy here means this
        # is that mount event, not a genuine click.
        if dash.ctx.triggered[0]["value"]:
            symbol = triggered_id["index"]

    if not symbol:
        return dash.no_update, dash.no_update, dash.no_update

    fundamentals = backend_state.fundamentals
    alpaca = backend_state.alpaca_clients
    info = None
    if fundamentals is not None and alpaca is not None and alpaca.settings.has_credentials:
        try:
            info = run_async(get_symbol_info(fundamentals, alpaca, symbol))
        except Exception:
            logger.exception("Symbol info fetch failed for %s", symbol)

    return _iframe_src(symbol), symbol, _render_symbol_info(info)


@callback(
    Output("trade-ideas-body", "children"),
    Input("trade-ideas-generate", "n_clicks"),
    prevent_initial_call=True,
)
def generate_trade_ideas_callback(_n_clicks):
    settings = backend_state.settings
    if settings is None or not settings.has_anthropic_credentials:
        return _trade_ideas_message(
            "ANTHROPIC_API_KEY not configured -- add it to backend/.env to enable AI trade ideas."
        )

    engine = backend_state.scanner_engine
    rows = engine.snapshot_view("gainers") if engine is not None else []
    if not rows:
        return _trade_ideas_message("No active movers to summarize right now.")

    client = backend_state.anthropic_client
    alpaca = backend_state.alpaca_clients
    try:
        result = run_async(generate_trade_ideas(client, alpaca, rows))
    except Exception:
        logger.exception("Trade idea generation failed")
        return _trade_ideas_message("Failed to generate trade ideas.")

    last_prices = {r.symbol: r.last_price for r in rows}
    generated_at = datetime.now(timezone.utc)
    tracked = [
        TrackedIdea(
            symbol=idea.symbol,
            headline=idea.headline,
            entry_price=last_prices[idea.symbol],
            generated_at=generated_at,
        )
        for idea in result.ideas
        if idea.symbol in last_prices
    ]
    if backend_state.trade_idea_tracker is not None:
        backend_state.trade_idea_tracker.record(tracked)

    return _trade_ideas_cards(result.ideas, result.disclaimer)
