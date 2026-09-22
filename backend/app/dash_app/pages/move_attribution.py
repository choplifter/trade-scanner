"""Move Attribution: SPY/QQQ's outsized moves, each lined up with what else
was happening -- a macro release, the 10-year, the VIX, mega-cap earnings,
options expiry -- plus how much more often each of those sits on a big move
than on an ordinary one. The work is in app.attribution; this page asks for
it and renders it.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import dash
from dash import Input, Output, State, callback, dash_table, dcc, html
from dash.dash_table.Format import Format, Scheme, Sign

from app.attribution.build import build_report, headlines, is_macro_headline
from app.attribution.sources import MEGA_CAPS
from app.dash_app.async_bridge import run_async
from app.dash_app.pages.backtest import _TABLE_STYLE, _iframe_src
from app.dash_app.state import backend_state
from app.dash_app.theme import DELTA_DOWN, DELTA_UP, TEXT_MUTED

dash.register_page(__name__, path="/move-attribution", name="Move Attribution")

logger = logging.getLogger(__name__)

NY = ZoneInfo("America/New_York")

_SYMBOLS = ["SPY", "QQQ", "IWM", "DIA"]

_PCT = Format(precision=2, scheme=Scheme.fixed, sign=Sign.positive)
_ONE = Format(precision=1, scheme=Scheme.fixed, sign=Sign.positive)
_TWO = Format(precision=2, scheme=Scheme.fixed)

_MOVE_COLUMNS = [
    {"name": "When (New York)", "id": "when"},
    {"name": "Move %", "id": "ret", "type": "numeric", "format": _PCT},
    {"name": "σ", "id": "z", "type": "numeric", "format": _ONE},
    {"name": "Gap %", "id": "gap", "type": "numeric", "format": _PCT},
    {"name": "Session %", "id": "session_move", "type": "numeric", "format": _PCT},
    {"name": "10Y bp", "id": "yield_bp", "type": "numeric", "format": _ONE},
    {"name": "VIX %", "id": "vix", "type": "numeric", "format": _ONE},
    {"name": "Release", "id": "releases"},
    {"name": "Also that day", "id": "context"},
]

_STAT_COLUMNS = [
    {"name": "Factor", "id": "name"},
    {"name": "Big moves with it", "id": "big_with"},
    {"name": "Share of big moves", "id": "share_big", "type": "numeric", "format": Format(precision=0, scheme=Scheme.percentage)},
    {"name": "Share of all", "id": "share_all", "type": "numeric", "format": Format(precision=1, scheme=Scheme.percentage)},
    {"name": "Lift", "id": "lift", "type": "numeric", "format": _TWO},
    {"name": "Avg |move| with", "id": "abs_with", "type": "numeric", "format": _TWO},
    {"name": "without", "id": "abs_without", "type": "numeric", "format": _TWO},
]


def _labeled(label: str, control):
    return html.Div(
        [html.Label(label, style={"fontSize": "11px", "color": TEXT_MUTED, "display": "block"}), control],
        style={"display": "flex", "flexDirection": "column", "gap": "2px"},
    )


def _controls():
    return html.Div(
        [
            _labeled("Symbol", dcc.Dropdown(id="attr-symbol", options=_SYMBOLS, value="SPY", clearable=False, style={"width": "110px"})),
            _labeled(
                "Moves",
                dcc.RadioItems(
                    id="attr-mode",
                    options=[{"label": " Daily", "value": "daily"}, {"label": " Intraday", "value": "intraday"}],
                    value="daily",
                    inline=True,
                    inputStyle={"marginLeft": "8px"},
                ),
            ),
            _labeled(
                "Candle (intraday)",
                dcc.Dropdown(id="attr-candle", options=[{"label": "5m", "value": 5}, {"label": "15m", "value": 15}], value=15, clearable=False, style={"width": "90px"}),
            ),
            _labeled("Lookback (days)", dcc.Input(id="attr-lookback", type="number", value=365 * 3, min=10, step=10, style={"width": "110px"})),
            _labeled("Big = beyond σ", dcc.Input(id="attr-k", type="number", value=2.5, min=1, step=0.25, style={"width": "80px"})),
            html.Button("Run", id="attr-run", n_clicks=0),
        ],
        style={"display": "flex", "gap": "16px", "alignItems": "flex-end", "marginBottom": "10px", "flexWrap": "wrap"},
    )


def _chart_panel():
    return html.Div(
        [
            html.Div(
                [html.H3("Chart"), html.Span("Click a move to load its chart and headlines", id="attr-chart-label")],
                className="home-panel-header",
            ),
            html.Div(
                [
                    html.Iframe(id="attr-frame", src=_iframe_src(None), style={"width": "100%", "height": "55vh", "border": "none"}),
                    dcc.Loading(html.Div(id="attr-headlines", style={"marginTop": "8px"}), type="dot"),
                ],
                className="home-panel-body",
            ),
        ],
        className="home-panel backtest-symbol-panel",
    )


def layout(**_kwargs):
    return html.Div(
        [
            html.H2("Move Attribution"),
            html.P(
                "Finds the outsized moves — beyond the chosen number of standard deviations of the recent past "
                "(twenty days; intraday, twenty sessions of the same part of the day) — and lines each up with what "
                "else happened: CPI, payrolls, GDP, PCE and FOMC releases, the 10-year yield's move, the VIX, "
                "mega-cap earnings (" + ", ".join(MEGA_CAPS) + ") and options expiry. It shows what coincided, not "
                "what caused it. Lift says how much more often a factor sits on a big move than on any candle: "
                "2.0 means twice as often as chance. Daily lookbacks are in days (1095 = three years); intraday, "
                "keep it to weeks — months of 5-minute bars take a while to fetch.",
                className="benchmark-disclaimer",
            ),
            html.Div(
                [
                    html.Div(
                        [
                            _controls(),
                            dcc.Loading(html.Div(id="attr-summary"), type="circle"),
                            html.H4("Factors", style={"marginTop": "16px"}),
                            dash_table.DataTable(id="attr-stats", columns=_STAT_COLUMNS, data=[], **_TABLE_STYLE),
                            html.H4("Big moves", style={"marginTop": "16px"}),
                            dash_table.DataTable(
                                id="attr-moves",
                                columns=_MOVE_COLUMNS,
                                data=[],
                                sort_action="native",
                                page_size=25,
                                style_data_conditional=[
                                    {"if": {"filter_query": "{ret} >= 0", "column_id": "ret"}, "color": DELTA_UP},
                                    {"if": {"filter_query": "{ret} < 0", "column_id": "ret"}, "color": DELTA_DOWN},
                                ],
                                **{
                                    **_TABLE_STYLE,
                                    "style_cell_conditional": [
                                        {"if": {"column_id": c}, "textAlign": "left"} for c in ("when", "releases", "context")
                                    ],
                                },
                            ),
                            dcc.Store(id="attr-meta"),
                        ],
                        className="backtest-main",
                    ),
                    _chart_panel(),
                ],
                className="backtest-layout",
            ),
        ]
    )


async def _gather(*coros):
    import asyncio

    return await asyncio.gather(*coros)


def _news_list(title: str, news: list[dict]):
    if not news:
        return html.P(f"{title}: no headlines in that window.", className="benchmark-disclaimer")
    items = [
        html.Li(
            [
                html.Span(n["at"].astimezone(NY).strftime("%m-%d %H:%M "), style={"color": TEXT_MUTED}),
                html.A(n["headline"], href=n["url"], target="_blank", rel="noopener noreferrer") if n.get("url") else n["headline"],
                html.Span(f"  {', '.join(n['symbols'][:4])}", style={"color": TEXT_MUTED, "fontSize": "11px"}),
            ],
            style={"marginBottom": "4px"},
        )
        for n in news
    ]
    return html.Div(
        [html.H4(title, style={"margin": "8px 0 4px"}), html.Ul(items, style={"paddingLeft": "16px", "fontSize": "12px", "maxHeight": "30vh", "overflowY": "auto"})]
    )


def _when(at: datetime, mode: str) -> str:
    local = at.astimezone(NY)
    return local.strftime("%Y-%m-%d %a") if mode == "daily" else local.strftime("%Y-%m-%d %a %H:%M")


def _num(value, digits=4):
    return None if value is None else round(value, digits)


@callback(
    Output("attr-summary", "children"),
    Output("attr-stats", "data"),
    Output("attr-moves", "data"),
    Output("attr-meta", "data"),
    Input("attr-run", "n_clicks"),
    State("attr-symbol", "value"),
    State("attr-mode", "value"),
    State("attr-candle", "value"),
    State("attr-lookback", "value"),
    State("attr-k", "value"),
    prevent_initial_call=True,
)
def run_report(_n, symbol, mode, candle, lookback, k):
    clients, settings = backend_state.alpaca_clients, backend_state.settings
    if clients is None or settings is None:
        return html.P("Backend not ready yet -- try again in a moment.", className="benchmark-disclaimer"), [], [], None
    try:
        report = run_async(
            build_report(
                clients,
                settings,
                symbol,
                mode=mode,
                candle_minutes=int(candle or 15),
                lookback_days=int(lookback or 365),
                k=float(k or 2.5),
            )
        )
    except Exception as exc:
        logger.exception("Move attribution failed")
        return html.P(f"Failed: {exc}", className="benchmark-disclaimer"), [], [], None

    big = report.big
    unit = "days" if mode == "daily" else f"{report.candle_minutes}m candles"
    span = f"{_when(report.rows[0].at, 'daily')[:10]} to {_when(report.rows[-1].at, 'daily')[:10]}" if report.rows else "no data"
    lines = [f"{len(big)} big moves in {len(report.rows)} {unit} of {symbol}, {span}."]
    opposite, sharp = report.yield_opposite
    if sharp:
        lines.append(
            f"Of the {sharp} big moves with a sharp 10-year move, {opposite} went against yields "
            f"({opposite / sharp:.0%}) -- price down as yields rose, or up as they fell."
        )
    summary = html.Div(
        [html.P(line, style={"margin": "4px 0"}) for line in lines]
        + [html.P(note, className="benchmark-disclaimer", style={"margin": "4px 0"}) for note in report.notes]
    )
    stats = [
        {
            "name": s.name,
            "big_with": f"{s.big_with} / {s.big_total}",
            "share_big": s.big_with / s.big_total if s.big_total else None,
            "share_all": s.all_with / s.all_total if s.all_total else None,
            "lift": _num(s.lift, 2),
            "abs_with": _num(s.avg_abs_with, 3),
            "abs_without": _num(s.avg_abs_without, 3),
        }
        for s in report.stats
    ]
    moves = [
        {
            "id": int(r.at.timestamp()),
            "when": _when(r.at, mode),
            "ret": _num(r.ret_pct),
            "z": _num(r.z, 2),
            "gap": _num(r.gap_pct),
            "session_move": _num(r.session_pct),
            "yield_bp": _num(r.yield_bp, 1),
            "vix": _num(r.vix_pct, 1),
            "releases": " · ".join(r.releases) if r.releases_known else "unknown",
            "context": " · ".join(r.context),
        }
        for r in sorted(big, key=lambda r: abs(r.z or 0), reverse=True)
    ]
    meta = {"symbol": symbol, "mode": mode, "minutes": report.candle_minutes}
    return summary, stats, moves, meta


@callback(
    Output("attr-frame", "src"),
    Output("attr-chart-label", "children"),
    Output("attr-headlines", "children"),
    Input("attr-moves", "active_cell"),
    State("attr-meta", "data"),
    prevent_initial_call=True,
)
def show_move(active_cell, meta):
    row_id = active_cell.get("row_id") if active_cell else None
    if not row_id or not meta:
        return dash.no_update, dash.no_update, dash.no_update
    at = datetime.fromtimestamp(int(row_id), tz=timezone.utc)
    symbol, mode, minutes = meta["symbol"], meta["mode"], meta["minutes"]
    if mode == "daily":
        timeframe = "1D"
        # The evening before (after-close earnings, overnight news) through
        # that day's close.
        day = at.astimezone(NY).date()
        start = datetime.combine(day - timedelta(days=1), datetime.min.time(), tzinfo=NY).replace(hour=16)
        end = datetime.combine(day, datetime.min.time(), tzinfo=NY).replace(hour=16)
    else:
        timeframe = f"{minutes}m"
        # Index-tagged news gets a few hours' run-up: a premarket jump is
        # often the market reading a story from before dawn.
        start, end = at - timedelta(hours=3), at + timedelta(minutes=minutes)
    # Untagged news is dense -- hundreds of press releases an hour -- so it
    # is read over a tight window and kept only where it reads as
    # market-wide (is_macro_headline): the hour before an intraday move, or
    # on a daily one the releases through the first hour of trading.
    if mode == "daily":
        near_start = datetime.combine(day, datetime.min.time(), tzinfo=NY).replace(hour=8)
        near_end = near_start + timedelta(hours=2, minutes=30)
    else:
        near_start, near_end = at - timedelta(hours=1), at + timedelta(minutes=minutes)

    clients = backend_state.alpaca_clients
    # Two lists, because the newest twenty of everything is mostly
    # afternoon noise: headlines tagged with the index itself carry the
    # macro and policy news, in time order so the morning's release reads
    # first; the mega-caps' own news beside it.
    index_symbols = sorted({symbol, "SPY", "QQQ"})
    index_news, megacap_news, all_news = (
        run_async(
            _gather(
                headlines(clients, index_symbols, start, end, limit=50),
                headlines(clients, list(MEGA_CAPS), start, end, limit=15),
                headlines(clients, None, near_start, near_end, limit=200),
            )
        )
        if clients
        else ([], [], [])
    )
    index_news.sort(key=lambda n: n["at"])
    all_news = sorted((n for n in all_news if is_macro_headline(n["headline"])), key=lambda n: n["at"])
    body = html.Div(
        [
            _news_list("Tagged " + " / ".join(index_symbols), index_news),
            _news_list("Market-wide stories, any ticker (keyword match: Fed, rates, tariffs, oil, war...)", all_news),
            _news_list("Mega-caps", megacap_news),
        ]
    )
    label = f"{symbol} · {_when(at, mode)}"
    return _iframe_src(symbol, int(at.timestamp()), timeframe), label, body
