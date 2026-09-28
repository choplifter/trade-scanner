"""Backtest page: click-to-run historical validation for the app's ranking
and momentum-alarm logic, all replaying the *actual* production functions
against history (see app.scanners.backtest, app.scanners.momentum_backtest)
rather than reimplementations -- same principle as every other backtest/
drift tool in this app.

Three independent, separately-triggered sections rather than one combined
run: each fetches a different resolution/amount of historical data (daily
bars for the ranking backtest, 5-minute bars -- disk-cached, see
app.scanners.bar_cache -- for the momentum backtest and parameter sweep)
and can take real wall-clock time, so nothing here runs on page load or a
shared poll interval -- only a click. Dash callbacks block the requesting
thread for the duration (see async_bridge.py), so dcc.Loading spinners are
the whole progress UI -- if the browser feels stuck, it's fetching from
Alpaca, not stuck.

Same scope caveats as the underlying CLI reports (backend/scripts/
backtest_report.py, momentum_backtest_report.py, momentum_param_sweep.py):
no catalyst/headline backtesting (needs historical news, unbuilt), no
point-in-time universe reconstruction (today's live universe is backtested
against past dates), momentum backtest/sweep are long-side only.
"""

import logging
import urllib.parse
from datetime import date, datetime, timezone

import dash
import httpx
from dash import Input, Output, State, callback, dash_table, dcc, html

from app.alpaca.universe import UniverseSymbol
from app.attribution.sources import cboe_daily_closes
from app.dash_app.async_bridge import run_async
from app.dash_app.state import backend_state
from app.dash_app.theme import DELTA_DOWN, DELTA_UP, TEXT_MUTED
from app.scanners import bucket_analysis
from app.scanners.backtest import run_backtest
from app.scanners.bar_cache import get_cached_5m_bars_multi
from app.scanners.momentum_backtest import run_momentum_backtest, sweep_momentum_params
from app.scanners.strategy_backtest import run_strategy_backtest
from app.scanners.strategy_slices import by_exit_reason, by_session_part, by_vix_band
from app.strategies.loader import inventory, load_strategies

dash.register_page(__name__, path="/backtest", name="Backtest")

logger = logging.getLogger(__name__)

_NOT_READY = "Backend not ready yet -- try again in a moment."
_NO_UNIVERSE = "No live universe available yet -- try again once the scanner has polled at least once."

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
        {"if": {"column_id": "view"}, "textAlign": "left"},
        {"if": {"column_id": "bucket"}, "textAlign": "left"},
        {"if": {"column_id": "group"}, "textAlign": "left"},
        {"if": {"column_id": "kind"}, "textAlign": "left"},
        {"if": {"column_id": "strategy"}, "textAlign": "left", "fontWeight": "600"},
        {"if": {"column_id": "flag"}, "textAlign": "left", "color": TEXT_MUTED},
        {"if": {"column_id": "symbol"}, "textAlign": "left", "fontWeight": "600"},
        {"if": {"column_id": "trading_date"}, "textAlign": "left"},
        {"if": {"column_id": "timestamp"}, "textAlign": "left"},
        {"if": {"column_id": "result"}, "textAlign": "left", "fontWeight": "600"},
        {
            "if": {"column_id": "copy"},
            "textAlign": "center",
            "width": "28px",
            "minWidth": "28px",
            "maxWidth": "28px",
            "cursor": "pointer",
            "color": TEXT_MUTED,
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


def _format_pct(value: float | None) -> str:
    if value is None:
        return "—"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


def _offset_label(moment: datetime) -> str:
    """Explicit UTC offset (e.g. "UTC+02:00") rather than relying on the
    reader to know it -- same rationale as market_conditions.py's own
    _offset_label.
    """
    offset = moment.strftime("%z")
    return f"UTC{offset[:3]}:{offset[3:]}" if offset else "local time"


def _local_timestamp(iso_ts: str) -> str:
    """Momentum pick timestamps are recorded in UTC (bar.timestamp.
    isoformat()) -- converted here to this machine's local timezone for
    display. Same same-machine assumption market_conditions.py's
    _as_of_line already makes: this app's backend and browser both run on
    the user's own PC, so "this process's local time" is the viewer's
    local time too.
    """
    return datetime.fromisoformat(iso_ts).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _iframe_src(symbol: str | None, target_time: int | None = None, timeframe: str | None = None) -> str:
    """Same embedded-chart iframe the scanner heatmap's Symbol Detail panel
    uses (see scanner_heatmap.py's _iframe_src) -- clicking a pick here
    loads it into this page's own panel rather than navigating away, same
    click-a-row-to-load-the-chart UX as the scanner page.

    `target_time` (unix seconds, UTC) is the pick's own entry time -- passed
    through as a "time" query param so lightweight_chart.html can jump the
    view to that exact bar. `timeframe` is the resolution *that specific
    backtest actually replayed* ("1D" for the daily ranking backtest, "5m"
    for the 5-minute-resolution momentum backtest) -- passed through as "tf"
    so the chart opens on the same resolution the win/loss was computed at,
    rather than always starting from the chart's own default and possibly
    settling on a resolution neither backtest ever looked at. Still widens
    further (see lightweight_chart.html's loadChart()/ESCALATION_ORDER) if
    that specific resolution's native lookback doesn't reach back far
    enough for this particular pick.
    """
    if not symbol:
        return "/analytics/assets/lightweight_chart.html"
    src = "/analytics/assets/lightweight_chart.html?symbol=" + urllib.parse.quote(symbol)
    if target_time is not None:
        src += "&time=" + str(target_time)
    if timeframe is not None:
        src += "&tf=" + urllib.parse.quote(timeframe)
    return src


def _pick_row_id(symbol: str, target_time: int, view: str | None = None) -> str:
    """Packs symbol + entry time (unix seconds) into one row "id" -- not
    shown as a column, just read back from active_cell["row_id"] on click
    (see update_backtest_symbol_panel) so the chart panel knows not just
    which symbol but which specific bar to jump to. Avoids indexing into
    the table's "data" by row position, which native pagination makes
    unreliable.

    `view` is appended for the daily table only, where symbol+date is no
    longer unique: a gapper that's also a most-active name produces one row
    per view for the same date (see backtest.simulate_from_bars), and
    _register_copy_single_pick_callback looks a clicked row up by id -- with
    a colliding id it would copy whichever of the two it found first. The
    momentum table's rows stay two-part, and nothing downstream reads past
    the first two fields.
    """
    packed = f"{symbol}|{target_time}"
    return f"{packed}|{view}" if view is not None else packed


def _parse_row_id(row_id: str) -> tuple[str, int | None]:
    """Symbol + entry time. Any trailing component (the daily table's view,
    above) is deliberately ignored -- the chart panel keys off symbol and
    bar, which are the same whichever view surfaced the pick.
    """
    parts = row_id.split("|")
    symbol = parts[0]
    ts = parts[1] if len(parts) > 1 else ""
    return symbol, (int(ts) if ts else None)


def _sorted_rows(rows: list[dict], sort_by, sort_keys: dict) -> list[dict]:
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


_WIN_LOSS_OPTIONS = [
    {"label": " All", "value": "all"},
    {"label": " Wins", "value": "win"},
    {"label": " Losses", "value": "loss"},
]


def _win_loss_filter(filter_id: str):
    return dcc.RadioItems(id=filter_id, options=_WIN_LOSS_OPTIONS, value="all", inline=True)


def _picks_controls(filter_id: str, copy_button_id: str):
    return html.Div(
        [
            _win_loss_filter(filter_id),
            html.Button("Copy Picks", id=copy_button_id, n_clicks=0, title="Copy the rows shown below to the clipboard"),
        ],
        style={"display": "flex", "alignItems": "center", "gap": "16px", "marginBottom": "8px"},
    )


def _register_copy_picks_callback(button_id: str, table_id: str, sink_id: str) -> None:
    """Copies every row currently shown in `table_id` (respecting whatever
    the win/loss filter and sort currently have it displaying) to the
    clipboard as a tab-separated block -- one header line straight from
    the table's own column definitions, one line per row -- so a handful
    of interesting candidates can be pasted straight into another chat
    (e.g. Claude Code) instead of retyped by hand. Only real columns are
    included (State reads the table's "columns" prop, not each row's raw
    dict, so hidden fields like the packed "id"/"*_num" sort helpers never
    leak into the copied text) -- and the "copy" column itself (see
    _register_copy_single_pick_callback) is excluded too, since its own
    value is just a clipboard icon, not data worth copying.

    Clientside (not a normal @callback) for the same reason
    scanner_heatmap.py's own clipboard copy is: a clipboard write has to
    happen synchronously inside the click's own event handler in most
    browsers, or the browser silently refuses it.
    """
    dash.clientside_callback(
        """
        function(n_clicks, data, columns) {
            if (!n_clicks || !data || !data.length || !columns) {
                return window.dash_clientside.no_update;
            }
            const cols = columns.filter((c) => c.id !== "copy");
            const lines = [cols.map((c) => c.name).join("\\t")];
            data.forEach((row) => {
                lines.push(cols.map((c) => (row[c.id] != null ? String(row[c.id]) : "")).join("\\t"));
            });
            if (navigator.clipboard) {
                navigator.clipboard.writeText(lines.join("\\n")).catch(function () {});
            }
            return window.dash_clientside.no_update;
        }
        """,
        Output(sink_id, "children"),
        Input(button_id, "n_clicks"),
        State(table_id, "data"),
        State(table_id, "columns"),
        prevent_initial_call=True,
    )


def _register_copy_single_pick_callback(table_id: str, sink_id: str) -> None:
    """Per-row twin of _register_copy_picks_callback: clicking a row's
    "copy" cell (see the "copy" column added to _DAILY_PICK_COLUMNS/
    _MOMENTUM_PICK_COLUMNS) copies just that one pick, same tab-separated
    single-line format the bulk copy uses per row, so one specific
    candidate can be grabbed without copying (and then trimming down) the
    whole filtered list.

    Looks the clicked row up by its packed "id" (see _pick_row_id), not
    active_cell.row's index, for the same reason update_backtest_
    symbol_panel does -- native pagination makes indexing into "data" by
    position unreliable.

    Also fires update_backtest_symbol_panel (both callbacks listen to the
    same table's active_cell) -- clicking "copy" loads that pick's chart
    too, a harmless side effect accepted elsewhere in this app already
    (see scanner_heatmap.py's own copy column).
    """
    dash.clientside_callback(
        """
        function(activeCell, data, columns) {
            if (!activeCell || activeCell.column_id !== "copy" || !data || !columns) {
                return window.dash_clientside.no_update;
            }
            const row = data.find((r) => r.id === activeCell.row_id);
            if (!row) {
                return window.dash_clientside.no_update;
            }
            const cols = columns.filter((c) => c.id !== "copy");
            const line = cols.map((c) => (row[c.id] != null ? String(row[c.id]) : "")).join("\\t");
            if (navigator.clipboard) {
                navigator.clipboard.writeText(line).catch(function () {});
            }
            return window.dash_clientside.no_update;
        }
        """,
        Output(sink_id, "children"),
        Input(table_id, "active_cell"),
        State(table_id, "data"),
        State(table_id, "columns"),
        prevent_initial_call=True,
    )


def _filter_by_result(rows: list[dict], filter_value: str) -> list[dict]:
    if filter_value == "win":
        return [r for r in rows if r["result"] == "Win"]
    if filter_value == "loss":
        return [r for r in rows if r["result"] == "Loss"]
    return rows


def _result_conditional(column_id: str) -> list[dict]:
    return [
        {"if": {"filter_query": f'{{{column_id}}} = "Win"', "column_id": column_id}, "color": DELTA_UP},
        {"if": {"filter_query": f'{{{column_id}}} = "Loss"', "column_id": column_id}, "color": DELTA_DOWN},
    ]


def _noise_flag(sample_size: int) -> str:
    """The under-the-floor warning, defined once for every table on this
    page. Both the group tables and the bucket tables show it, and they
    must show the same words for the same floor -- two spellings of one
    warning reads as two different warnings.

    Bucket tables need this at least as much as group tables do: bucketing
    splits a sample four ways per view, so tail buckets sit under the floor
    routinely even when the run as a whole is large. A 30-60% gap bucket
    with 11 picks and a 72.7% win rate looks like a finding next to the
    ~49% the big buckets show, and is three picks away from 45%.
    """
    return "" if sample_size >= bucket_analysis.MIN_SAMPLE_SIZE else f"noisy (n<{bucket_analysis.MIN_SAMPLE_SIZE})"


def _stats_cell(stats: dict) -> dict:
    return {
        "sample_size": stats["sample_size"],
        "win_rate": f"{stats['win_rate']:.1f}%" if stats["win_rate"] is not None else "—",
        "avg_return": _format_pct(stats["avg_return"]),
        "avg_return_num": stats["avg_return"],
        "flag": _noise_flag(stats["sample_size"]),
    }


def _top_symbols(universe: dict[str, UniverseSymbol], max_symbols: int) -> list[str]:
    ranked = sorted(universe.values(), key=lambda u: u.avg_dollar_vol_20d, reverse=True)
    return [u.symbol for u in ranked[:max_symbols]]


def _labeled_input(label: str, input_id: str, value, input_type: str = "number", **kwargs):
    return html.Div(
        [
            html.Label(label, style={"fontSize": "11px", "color": TEXT_MUTED, "display": "block"}),
            dcc.Input(id=input_id, type=input_type, value=value, **kwargs),
        ],
        style={"display": "flex", "flexDirection": "column", "gap": "2px"},
    )


def _force_refresh_checklist(checklist_id: str):
    return html.Div(
        dcc.Checklist(
            id=checklist_id,
            options=[{"label": " Force refresh cache", "value": "refresh"}],
            value=[],
        ),
        style={"paddingBottom": "6px"},
    )


_BUCKET_COLUMNS = [
    {"name": "View", "id": "view"},
    {"name": "Bucket", "id": "bucket"},
    {"name": "Sample", "id": "sample_size"},
    {"name": "Win Rate", "id": "win_rate"},
    {"name": "Avg Return", "id": "avg_return"},
    # Same trailing noise flag the group tables carry -- see _noise_flag.
    {"name": "", "id": "flag"},
]

_GROUP_COLUMNS = [
    {"name": "Group", "id": "group"},
    {"name": "Sample", "id": "sample_size"},
    {"name": "Win Rate", "id": "win_rate"},
    {"name": "Avg Return", "id": "avg_return"},
    {"name": "", "id": "flag"},
]

_GROUP_COLUMNS_WITH_VIEW = [{"name": "View", "id": "view"}, *_GROUP_COLUMNS]

_VIEW_LABEL = {"gainers": "Gainers", "losers": "Losers", "most_active": "Most Active"}


def _bucket_rows(buckets: list[dict]) -> list[dict]:
    return [
        {
            "view": b["view"],
            "bucket": b["bucket"],
            "sample_size": b["sample_size"],
            "win_rate": f"{b['win_rate']:.1f}%",
            "avg_return": _format_pct(b["avg_return"]),
            "avg_return_num": b["avg_return"],
            "flag": _noise_flag(b["sample_size"]),
        }
        for b in buckets
    ]


def _bucket_table(table_id: str, rows: list[dict]):
    return dash_table.DataTable(
        id=table_id,
        columns=_BUCKET_COLUMNS,
        data=rows,
        style_data_conditional=_delta_conditional("avg_return", "avg_return_num"),
        **_TABLE_STYLE,
    )


def _group_table(table_id: str, rows: list[dict], show_view: bool = False):
    return dash_table.DataTable(
        id=table_id,
        columns=_GROUP_COLUMNS_WITH_VIEW if show_view else _GROUP_COLUMNS,
        data=rows,
        style_data_conditional=_delta_conditional("avg_return", "avg_return_num"),
        **_TABLE_STYLE,
    )


# --- Daily ranking backtest -------------------------------------------------


def _daily_section():
    return html.Div(
        [
            html.H3("Daily Ranking Backtest"),
            html.P(
                "Replays past trading days through engine._rank_gainers/_rank_losers/_rank_most_active "
                "to check whether gap%/RVOL predict win rate, whether high-RVOL entries fade, and "
                "whether a shaved-top entry candle says anything about outcome. No catalyst/headline "
                "boost (needs historical news, unbuilt) and no float (bulk float is today's, not "
                "point-in-time). Read Most Active with care: Max symbols already selects by "
                "dollar volume — so its picks repeat much more than the other two views' (measured at "
                "300 symbols over 180 days: it drew on 62.6% of the pool with 16 names present on 90%+ "
                "of days, against ~100% and none for Gainers/Losers), and more so as Max symbols shrinks.",
                className="benchmark-disclaimer",
            ),
            html.Div(
                [
                    _labeled_input("Lookback days", "backtest-daily-lookback", 180, min=20, step=10),
                    _labeled_input("Horizon days", "backtest-daily-horizon", 1, min=1, step=1),
                    _labeled_input("Max symbols", "backtest-daily-max-symbols", 300, min=10, step=10),
                    html.Button("Run Daily Backtest", id="backtest-daily-run", n_clicks=0),
                ],
                style={"display": "flex", "gap": "16px", "alignItems": "flex-end", "marginBottom": "10px", "flexWrap": "wrap"},
            ),
            dcc.Loading(html.Div(id="backtest-daily-output"), type="circle"),
            html.H4("Picks", style={"marginTop": "16px"}),
            _picks_controls("backtest-daily-picks-filter", "backtest-daily-picks-copy"),
            dash_table.DataTable(
                id="backtest-daily-picks-table",
                columns=_DAILY_PICK_COLUMNS,
                data=[],
                sort_action="custom",
                sort_mode="single",
                page_size=20,
                style_data_conditional=[
                    *_delta_conditional("outcome", "outcome_num"),
                    *_result_conditional("result"),
                ],
                **_TABLE_STYLE,
            ),
            dcc.Store(id="backtest-daily-picks-store", data=[]),
            html.Div(id="backtest-daily-picks-copy-sink", style={"display": "none"}),
            html.Div(id="backtest-daily-picks-copy-row-sink", style={"display": "none"}),
        ],
        style={"marginTop": "24px", "paddingTop": "16px", "borderTop": "1px solid #e1e0d9"},
    )


_DAILY_PICK_COLUMNS = [
    {"name": "Symbol", "id": "symbol"},
    {"name": "View", "id": "view"},
    {"name": "Date", "id": "trading_date"},
    {"name": "Entry Gap%", "id": "entry_gap"},
    {"name": "Entry RVOL", "id": "entry_rvol"},
    # The magnitude Most Active ranks on -- without it those rows show no
    # column explaining why they're in the list.
    {"name": "Entry $Vol", "id": "entry_dollar_volume"},
    {"name": "Shaved Top", "id": "shaved_top"},
    {"name": "Outcome", "id": "outcome"},
    {"name": "Result", "id": "result"},
    {"name": "", "id": "copy"},
]

_DAILY_PICK_SORT_KEYS = {
    "symbol": lambda p: p["symbol"],
    "view": lambda p: p["view"],
    "trading_date": lambda p: p["trading_date"],
    # These read the *_num helpers, not the display strings in the same row
    # dict -- _sorted_rows runs over _daily_pick_rows' output, where
    # entry_rvol is "1.23x" and entry_dollar_volume is "$1.2M". Sorting on
    # those would order them as text ("9.00x" above "10.00x").
    "entry_gap": lambda p: p["entry_pct_change"],
    "entry_rvol": lambda p: p["entry_rvol_num"],
    "entry_dollar_volume": lambda p: p["entry_dollar_volume_num"],
    "outcome": lambda p: p["outcome_num"],
}


def _format_dollar_volume(value: float) -> str:
    """Compact $ magnitude -- dollar volume spans several orders of
    magnitude across a ranked day, and the raw integer is unreadable at a
    glance in a table cell. Sorting uses the raw number (see
    _DAILY_PICK_SORT_KEYS), so rounding here costs nothing.
    """
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"${value / 1_000:.0f}K"
    return f"${value:.0f}"


def _daily_pick_rows(picks: list[dict]) -> list[dict]:
    return [
        {
            # Midnight UTC of the pick's trading date -- daily bars have no
            # time-of-day, so this is just an anchor for the chart's
            # nearest-bar search (see lightweight_chart.html's
            # findNearestIndex), not a claim about when the day's move
            # actually happened.
            "id": _pick_row_id(
                p["symbol"],
                int(datetime.fromisoformat(p["trading_date"]).replace(tzinfo=timezone.utc).timestamp()),
                p["view"],
            ),
            "symbol": p["symbol"],
            "view": _VIEW_LABEL.get(p["view"], p["view"]),
            "trading_date": p["trading_date"],
            "entry_gap": _format_pct(p["entry_pct_change"]),
            "entry_pct_change": p["entry_pct_change"],
            "entry_rvol": f"{p['entry_rvol']:.2f}x",
            "entry_rvol_num": p["entry_rvol"],
            "entry_dollar_volume": _format_dollar_volume(p["entry_dollar_volume"]),
            "entry_dollar_volume_num": p["entry_dollar_volume"],
            "shaved_top": "Yes" if p["is_shaved_top"] else "No",
            "outcome": _format_pct(p["pct_change_since_entry"]),
            "outcome_num": p["pct_change_since_entry"],
            "result": "Win" if p["pct_change_since_entry"] > 0 else "Loss",
            "copy": "📋",
        }
        for p in picks
    ]


@callback(
    Output("backtest-daily-picks-table", "data"),
    Input("backtest-daily-picks-store", "data"),
    Input("backtest-daily-picks-filter", "value"),
    Input("backtest-daily-picks-table", "sort_by"),
)
def update_daily_picks_table(picks, filter_value, sort_by):
    rows = _filter_by_result(_daily_pick_rows(picks or []), filter_value)
    return _sorted_rows(rows, sort_by, _DAILY_PICK_SORT_KEYS)


_register_copy_picks_callback("backtest-daily-picks-copy", "backtest-daily-picks-table", "backtest-daily-picks-copy-sink")
_register_copy_single_pick_callback("backtest-daily-picks-table", "backtest-daily-picks-copy-row-sink")


def _daily_report_layout(report: dict):
    if report["sample_size"] == 0:
        return html.P(
            f"{report['symbols_with_bars']}/{report['symbol_count']} symbols returned bar data, "
            "but no trigger events reconstructed.",
            className="benchmark-disclaimer",
        )

    fade = report["fade_risk"]
    # Per view, never pooled -- see backtest.fade_risk_by_view.
    fade_rows = []
    for entry in fade["views"]:
        fade_rows.append(
            {
                "view": entry["view"],
                "group": f"RVOL <= {fade['threshold']:.0f}x",
                **_stats_cell(entry["rvol_at_or_below_threshold"]),
            }
        )
        fade_rows.append(
            {
                "view": entry["view"],
                "group": f"RVOL > {fade['threshold']:.0f}x",
                **_stats_cell(entry["rvol_above_threshold"]),
            }
        )
    shaved_rows = []
    for entry in report["shaved_top"]:
        shaved_rows.append({"view": entry["view"], "group": "Shaved top", **_stats_cell(entry["shaved_top"])})
        shaved_rows.append({"view": entry["view"], "group": "Not shaved top", **_stats_cell(entry["not_shaved_top"])})

    return html.Div(
        [
            html.P(
                f"{report['symbols_with_bars']}/{report['symbol_count']} symbols returned bar data, "
                f"{report['sample_size']} picks reconstructed over {report['lookback_days']} days "
                f"({report['horizon_days']}-day forward horizon).",
                className="benchmark-disclaimer",
            ),
            html.H4("Fade Risk — By Gap Size"),
            _bucket_table("backtest-daily-gap-table", _bucket_rows(report["gap_buckets"])),
            html.H4("Fade Risk — By Entry RVOL"),
            _bucket_table("backtest-daily-rvol-table", _bucket_rows(report["rvol_buckets"])),
            html.H4(f"High-RVOL Fade Risk (>{fade['threshold']:.0f}x)"),
            _group_table("backtest-daily-fade-table", fade_rows, show_view=True),
            html.H4("Shaved-Top Entry Candle"),
            _group_table("backtest-daily-shaved-table", shaved_rows, show_view=True),
        ]
    )


@callback(
    Output("backtest-daily-output", "children"),
    Output("backtest-daily-picks-store", "data"),
    Input("backtest-daily-run", "n_clicks"),
    State("backtest-daily-lookback", "value"),
    State("backtest-daily-horizon", "value"),
    State("backtest-daily-max-symbols", "value"),
    prevent_initial_call=True,
)
def run_daily_backtest_callback(_n_clicks, lookback_days, horizon_days, max_symbols):
    clients = backend_state.alpaca_clients
    settings = backend_state.settings
    universe = backend_state.universe
    if clients is None or settings is None:
        return html.P(_NOT_READY, className="benchmark-disclaimer"), []
    if not universe:
        return html.P(_NO_UNIVERSE, className="benchmark-disclaimer"), []

    symbols = _top_symbols(universe, int(max_symbols or 300))
    try:
        report = run_async(
            run_backtest(
                clients, settings, symbols, lookback_days=int(lookback_days or 180), horizon_days=int(horizon_days or 1)
            )
        )
    except Exception:
        logger.exception("Daily backtest failed")
        return html.P("Backtest failed -- check backend logs.", className="benchmark-disclaimer"), []

    return _daily_report_layout(report), report["picks"]


# --- Momentum alarm backtest -------------------------------------------------


def _momentum_section():
    return html.Div(
        [
            html.H3("Momentum Alarm Backtest"),
            html.P(
                "5-minute-resolution replay of the live momentum alarm (formulas.is_momentum_alert) -- "
                "does requiring the shaved-top/green/above-VWAP entry confirmation actually improve on "
                "the momentum threshold alone? Long side only, matching the live alarm itself. Fetches are "
                "disk-cached (up to 12h old reused automatically).",
                className="benchmark-disclaimer",
            ),
            html.Div(
                [
                    _labeled_input("Lookback days", "backtest-momentum-lookback", 30, min=1, step=1),
                    _labeled_input("Horizon (minutes)", "backtest-momentum-horizon", 15, min=1, step=1),
                    _labeled_input("Threshold % (blank = default)", "backtest-momentum-threshold", None, min=0, step=0.5),
                    _labeled_input("Max symbols", "backtest-momentum-max-symbols", 100, min=5, step=5),
                    _force_refresh_checklist("backtest-momentum-force-refresh"),
                    html.Button("Run Momentum Backtest", id="backtest-momentum-run", n_clicks=0),
                ],
                style={"display": "flex", "gap": "16px", "alignItems": "flex-end", "marginBottom": "10px", "flexWrap": "wrap"},
            ),
            dcc.Loading(html.Div(id="backtest-momentum-output"), type="circle"),
            html.H4("Picks", style={"marginTop": "16px"}),
            html.P(
                f"Timestamps shown in your local time ({_offset_label(datetime.now().astimezone())}).",
                className="benchmark-disclaimer",
            ),
            _picks_controls("backtest-momentum-picks-filter", "backtest-momentum-picks-copy"),
            dash_table.DataTable(
                id="backtest-momentum-picks-table",
                columns=_MOMENTUM_PICK_COLUMNS,
                data=[],
                sort_action="custom",
                sort_mode="single",
                page_size=20,
                style_data_conditional=[
                    *_delta_conditional("outcome", "outcome_num"),
                    *_result_conditional("result"),
                ],
                **_TABLE_STYLE,
            ),
            dcc.Store(id="backtest-momentum-picks-store", data=[]),
            html.Div(id="backtest-momentum-picks-copy-sink", style={"display": "none"}),
            html.Div(id="backtest-momentum-picks-copy-row-sink", style={"display": "none"}),
        ],
        style={"marginTop": "24px", "paddingTop": "16px", "borderTop": "1px solid #e1e0d9"},
    )


_KIND_LABEL = {"threshold_only": "Threshold Only", "full_alert": "Full Alert"}

_MOMENTUM_PICK_COLUMNS = [
    {"name": "Symbol", "id": "symbol"},
    {"name": "Condition", "id": "kind"},
    {"name": "Timestamp", "id": "timestamp"},
    {"name": "Outcome", "id": "outcome"},
    {"name": "Result", "id": "result"},
    {"name": "", "id": "copy"},
]

_MOMENTUM_PICK_SORT_KEYS = {
    "symbol": lambda p: p["symbol"],
    "kind": lambda p: p["kind"],
    "timestamp": lambda p: p["timestamp"],
    # outcome_num, not the "+1.23%" display string -- see the note in
    # _DAILY_PICK_SORT_KEYS.
    "outcome": lambda p: p["outcome_num"],
}


def _momentum_pick_rows(picks: list[dict]) -> list[dict]:
    return [
        {
            "id": _pick_row_id(p["symbol"], int(datetime.fromisoformat(p["timestamp"]).timestamp())),
            "symbol": p["symbol"],
            "kind": _KIND_LABEL.get(p["kind"], p["kind"]),
            "timestamp": _local_timestamp(p["timestamp"]),
            "outcome": _format_pct(p["pct_change_since_entry"]),
            "outcome_num": p["pct_change_since_entry"],
            "result": "Win" if p["pct_change_since_entry"] > 0 else "Loss",
            "copy": "📋",
        }
        for p in picks
    ]


@callback(
    Output("backtest-momentum-picks-table", "data"),
    Input("backtest-momentum-picks-store", "data"),
    Input("backtest-momentum-picks-filter", "value"),
    Input("backtest-momentum-picks-table", "sort_by"),
)
def update_momentum_picks_table(picks, filter_value, sort_by):
    rows = _filter_by_result(_momentum_pick_rows(picks or []), filter_value)
    return _sorted_rows(rows, sort_by, _MOMENTUM_PICK_SORT_KEYS)


_register_copy_picks_callback(
    "backtest-momentum-picks-copy", "backtest-momentum-picks-table", "backtest-momentum-picks-copy-sink"
)
_register_copy_single_pick_callback("backtest-momentum-picks-table", "backtest-momentum-picks-copy-row-sink")


def _momentum_report_layout(report: dict):
    if report["sample_size"] == 0:
        return html.P(
            f"{report['symbols_with_bars']}/{report['symbol_count']} symbols returned bar data, "
            "but no trigger events reconstructed.",
            className="benchmark-disclaimer",
        )

    comparison = report["comparison"]
    rows = [
        {"kind": f"Threshold only (momentum >= {report['threshold']}%)", **_stats_cell(comparison["threshold_only"])},
        {"kind": "Full alert (+ shaved top, green, above VWAP)", **_stats_cell(comparison["full_alert"])},
    ]

    return html.Div(
        [
            html.P(
                f"{report['symbols_with_bars']}/{report['symbol_count']} symbols returned bar data, "
                f"{report['sample_size']} trigger events reconstructed over {report['lookback_days']} days "
                f"({report['horizon_minutes']}-minute forward horizon, {report['threshold']}% threshold).",
                className="benchmark-disclaimer",
            ),
            dash_table.DataTable(
                id="backtest-momentum-table",
                columns=[
                    {"name": "Condition", "id": "kind"},
                    {"name": "Sample", "id": "sample_size"},
                    {"name": "Win Rate", "id": "win_rate"},
                    {"name": "Avg Return", "id": "avg_return"},
                    {"name": "", "id": "flag"},
                ],
                data=rows,
                style_data_conditional=_delta_conditional("avg_return", "avg_return_num"),
                **_TABLE_STYLE,
            ),
        ]
    )


@callback(
    Output("backtest-momentum-output", "children"),
    Output("backtest-momentum-picks-store", "data"),
    Input("backtest-momentum-run", "n_clicks"),
    State("backtest-momentum-lookback", "value"),
    State("backtest-momentum-horizon", "value"),
    State("backtest-momentum-threshold", "value"),
    State("backtest-momentum-max-symbols", "value"),
    State("backtest-momentum-force-refresh", "value"),
    prevent_initial_call=True,
)
def run_momentum_backtest_callback(_n_clicks, lookback_days, horizon_minutes, threshold, max_symbols, force_refresh):
    clients = backend_state.alpaca_clients
    settings = backend_state.settings
    universe = backend_state.universe
    if clients is None or settings is None:
        return html.P(_NOT_READY, className="benchmark-disclaimer"), []
    if not universe:
        return html.P(_NO_UNIVERSE, className="benchmark-disclaimer"), []

    symbols = _top_symbols(universe, int(max_symbols or 100))
    try:
        report = run_async(
            run_momentum_backtest(
                clients,
                settings,
                symbols,
                lookback_days=int(lookback_days or 30),
                horizon_minutes=int(horizon_minutes or 15),
                threshold=float(threshold) if threshold not in (None, "") else None,
                force_refresh="refresh" in (force_refresh or []),
            )
        )
    except Exception:
        logger.exception("Momentum backtest failed")
        return html.P("Backtest failed -- check backend logs.", className="benchmark-disclaimer"), []

    return _momentum_report_layout(report), report["picks"]


# --- Parameter sweep ----------------------------------------------------------


def _sweep_section():
    return html.Div(
        [
            html.H3("Parameter Sweep"),
            html.P(
                "Fetches 5-minute bars once, then cheaply re-simulates every (threshold, horizon) "
                "combination in memory -- widening the grid only costs more simulation time, not more "
                "network time. Ranked by full-alert avg return, with combinations under the "
                f"n={bucket_analysis.MIN_SAMPLE_SIZE} sample floor pushed to the bottom regardless of "
                "how good the number looks.",
                className="benchmark-disclaimer",
            ),
            html.Div(
                [
                    _labeled_input("Lookback days", "backtest-sweep-lookback", 30, min=1, step=1),
                    _labeled_input(
                        "Thresholds (%, comma-separated)", "backtest-sweep-thresholds", "1,2,3,5,7,10", input_type="text"
                    ),
                    _labeled_input(
                        "Horizons (min, comma-separated)", "backtest-sweep-horizons", "5,10,15,30,60", input_type="text"
                    ),
                    _labeled_input("Max symbols", "backtest-sweep-max-symbols", 100, min=5, step=5),
                    _force_refresh_checklist("backtest-sweep-force-refresh"),
                    html.Button("Run Sweep", id="backtest-sweep-run", n_clicks=0),
                ],
                style={"display": "flex", "gap": "16px", "alignItems": "flex-end", "marginBottom": "10px", "flexWrap": "wrap"},
            ),
            dcc.Loading(html.Div(id="backtest-sweep-output"), type="circle"),
        ],
        style={"marginTop": "24px", "paddingTop": "16px", "borderTop": "1px solid #e1e0d9"},
    )


def _sweep_sufficient(result: dict) -> bool:
    return result["full_alert"]["sample_size"] >= bucket_analysis.MIN_SAMPLE_SIZE


def _sweep_avg_return(result: dict) -> float:
    avg_return = result["full_alert"]["avg_return"]
    return avg_return if avg_return is not None else float("-inf")


def _rank_sweep(results: list[dict]) -> list[dict]:
    """Sufficient-sample combinations first (best full-alert avg return
    first), then thin ones after -- mirrors momentum_param_sweep.py's
    _rank so the Dash and CLI views agree on ordering.
    """
    sufficient = sorted((r for r in results if _sweep_sufficient(r)), key=_sweep_avg_return, reverse=True)
    thin = sorted((r for r in results if not _sweep_sufficient(r)), key=_sweep_avg_return, reverse=True)
    return sufficient + thin


def _sweep_rows(results: list[dict]) -> list[dict]:
    rows = []
    for r in _rank_sweep(results):
        t = _stats_cell(r["threshold_only"])
        f = _stats_cell(r["full_alert"])
        rows.append(
            {
                "threshold": f"{r['threshold']:.1f}%",
                "horizon_minutes": f"{r['horizon_minutes']}min",
                "t_sample": t["sample_size"],
                "t_win_rate": t["win_rate"],
                "t_avg_return": t["avg_return"],
                "f_sample": f["sample_size"],
                "f_win_rate": f["win_rate"],
                "f_avg_return": f["avg_return"],
                "f_avg_return_num": f["avg_return_num"],
                "flag": f["flag"],
            }
        )
    return rows


def _sweep_report_layout(results: list[dict], symbol_count: int, symbols_with_bars: int, lookback_days: int):
    return html.Div(
        [
            html.P(
                f"{symbols_with_bars}/{symbol_count} symbols returned bar data over {lookback_days} days, "
                f"{len(results)} (threshold, horizon) combinations tested.",
                className="benchmark-disclaimer",
            ),
            dash_table.DataTable(
                id="backtest-sweep-table",
                columns=[
                    {"name": "Threshold", "id": "threshold"},
                    {"name": "Horizon", "id": "horizon_minutes"},
                    {"name": "Thresh-only n", "id": "t_sample"},
                    {"name": "Thresh-only Win%", "id": "t_win_rate"},
                    {"name": "Thresh-only Avg%", "id": "t_avg_return"},
                    {"name": "Full-alert n", "id": "f_sample"},
                    {"name": "Full-alert Win%", "id": "f_win_rate"},
                    {"name": "Full-alert Avg%", "id": "f_avg_return"},
                    {"name": "", "id": "flag"},
                ],
                data=_sweep_rows(results),
                style_data_conditional=_delta_conditional("f_avg_return", "f_avg_return_num"),
                **_TABLE_STYLE,
            ),
        ]
    )


@callback(
    Output("backtest-sweep-output", "children"),
    Input("backtest-sweep-run", "n_clicks"),
    State("backtest-sweep-lookback", "value"),
    State("backtest-sweep-thresholds", "value"),
    State("backtest-sweep-horizons", "value"),
    State("backtest-sweep-max-symbols", "value"),
    State("backtest-sweep-force-refresh", "value"),
    prevent_initial_call=True,
)
def run_sweep_callback(_n_clicks, lookback_days, thresholds_str, horizons_str, max_symbols, force_refresh):
    clients = backend_state.alpaca_clients
    universe = backend_state.universe
    if clients is None:
        return html.P(_NOT_READY, className="benchmark-disclaimer")
    if not universe:
        return html.P(_NO_UNIVERSE, className="benchmark-disclaimer")

    try:
        thresholds = [float(x.strip()) for x in (thresholds_str or "").split(",") if x.strip()]
        horizons = [int(x.strip()) for x in (horizons_str or "").split(",") if x.strip()]
    except ValueError:
        return html.P("Thresholds/horizons must be comma-separated numbers.", className="benchmark-disclaimer")
    if not thresholds or not horizons:
        return html.P("Enter at least one threshold and one horizon.", className="benchmark-disclaimer")

    symbols = _top_symbols(universe, int(max_symbols or 100))
    lookback = int(lookback_days or 30)
    try:
        bars_by_symbol = run_async(
            get_cached_5m_bars_multi(clients, symbols, lookback, force_refresh="refresh" in (force_refresh or []))
        )
        results = sweep_momentum_params(bars_by_symbol, thresholds, horizons)
    except Exception:
        logger.exception("Parameter sweep failed")
        return html.P("Sweep failed -- check backend logs.", className="benchmark-disclaimer")

    return _sweep_report_layout(results, len(symbols), len(bars_by_symbol), lookback)


def _symbol_panel():
    """Same embedded chart panel as the scanner heatmap's "Symbol Detail"
    (see scanner_heatmap.py) -- sits to the right of the backtest sections
    and stays put (position: sticky, see .backtest-symbol-panel in
    theme.css) as the much taller left column scrolls, so a pick clicked
    far down a Picks table doesn't need the chart scrolled back into view.
    """
    return html.Div(
        [
            html.Div(
                [html.H3("Chart"), html.Span("Click a pick to load its chart", id="backtest-symbol-label")],
                className="home-panel-header",
            ),
            html.Div(
                html.Iframe(
                    id="backtest-symbol-frame",
                    src=_iframe_src(None),
                    style={"width": "100%", "height": "70vh", "border": "none"},
                ),
                className="home-panel-body",
            ),
        ],
        className="home-panel backtest-symbol-panel",
    )


@callback(
    Output("backtest-symbol-frame", "src"),
    Output("backtest-symbol-label", "children"),
    Input("backtest-daily-picks-table", "active_cell"),
    Input("backtest-momentum-picks-table", "active_cell"),
    prevent_initial_call=True,
)
def update_backtest_symbol_panel(daily_active_cell, momentum_active_cell):
    triggered_id = dash.ctx.triggered_id
    if triggered_id == "backtest-daily-picks-table":
        active_cell, timeframe = daily_active_cell, "1D"
    else:
        active_cell, timeframe = momentum_active_cell, "5m"
    row_id = active_cell.get("row_id") if active_cell else None
    if not row_id:
        return dash.no_update, dash.no_update
    symbol, target_time = _parse_row_id(row_id)
    return _iframe_src(symbol, target_time, timeframe), symbol


# --- Strategy rules -----------------------------------------------------------
#
# What the section is for: the app has nine entry rules and, until now, no
# answer to which of them is worth firing on. Each is replayed over the same
# bars by app.scanners.strategy_backtest -- the production rule objects, not
# reimplementations -- and scored in R at the exit its own stop and target
# produced. The slices underneath exist because an expectancy of zero over
# three hundred signals is usually two different rules averaged together.

_R = "{:+.2f}"


def _format_r(value: float | None) -> str:
    return "—" if value is None else _R.format(value)


def _format_share(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}%"


_STRATEGY_COLUMNS = [
    {"name": "Strategy", "id": "strategy"},
    {"name": "", "id": "flag"},
    {"name": "Signals", "id": "trades", "type": "numeric"},
    {"name": "Expectancy R", "id": "expectancy"},
    {"name": "Win rate", "id": "win_rate"},
    {"name": "Avg win R", "id": "avg_win"},
    {"name": "Avg loss R", "id": "avg_loss"},
    {"name": "Ambiguous", "id": "ambiguous"},
]

_SLICE_COLUMNS = [
    {"name": "Bucket", "id": "bucket"},
    {"name": "", "id": "flag"},
    {"name": "Signals", "id": "trades", "type": "numeric"},
    {"name": "Expectancy R", "id": "expectancy"},
    {"name": "Win rate", "id": "win_rate"},
    {"name": "Avg win R", "id": "avg_win"},
    {"name": "Avg loss R", "id": "avg_loss"},
]


def _strategy_row(bucket: dict, label_id: str, label: str) -> dict:
    """One table row from one slice_stats dict. Numbers stay in the row as
    well as their formatted strings: the conditional colouring is a filter
    query over a numeric field, and "—" cannot be compared to zero."""
    return {
        label_id: label,
        "trades": bucket["trades"],
        "expectancy": _format_r(bucket["expectancy_r"]),
        "expectancy_r": bucket["expectancy_r"] if bucket["expectancy_r"] is not None else 0,
        "win_rate": _format_share(bucket["win_rate"]),
        "avg_win": _format_r(bucket["avg_win_r"]),
        "avg_loss": _format_r(bucket["avg_loss_r"]),
        "ambiguous": _format_share(bucket.get("ambiguous_pct")),
        "flag": _noise_flag(bucket["trades"]),
    }


def _strategy_section():
    return html.Div(
        [
            html.H3("Strategy Rules"),
            html.P(
                "Every strategy on disk replayed over the same 5-minute bars, including the ones currently "
                "switched off -- a parked rule has to be measurable before it is turned back on. Each signal "
                "gets its own structural stop and target and is scored where the trade ended, in R, so a rule "
                "whose average forward move is positive but whose path runs through its stop reads as the loser "
                "it is. Expectancy leads and win rate follows: a rule taking +0.3R wins and -1R losses can win "
                "seven times in ten and still lose money. Costs are charged on both sides and are an assumption, "
                "not a measurement -- historical spreads are not available here, so an edge worth having must "
                "survive a pessimistic figure. Click a row for that rule's slices. Every rule walks every bar, so "
                "the run costs roughly a minute per strategy per 60 symbols and 30 days -- the defaults are sized "
                "for a few minutes, not for a fast answer.",
                className="benchmark-disclaimer",
            ),
            html.Div(
                [
                    _labeled_input("Lookback days", "backtest-strategy-lookback", 20, min=5, step=5),
                    _labeled_input("Cost bps per side", "backtest-strategy-cost", 10, min=0, step=1),
                    _labeled_input("Max symbols", "backtest-strategy-max-symbols", 40, min=5, step=5),
                    _force_refresh_checklist("backtest-strategy-force-refresh"),
                    html.Button("Run Strategy Backtest", id="backtest-strategy-run", n_clicks=0),
                ],
                style={"display": "flex", "gap": "16px", "alignItems": "flex-end", "marginBottom": "10px", "flexWrap": "wrap"},
            ),
            dcc.Loading(html.Div(id="backtest-strategy-output"), type="circle"),
            dash_table.DataTable(
                id="backtest-strategy-table",
                columns=_STRATEGY_COLUMNS,
                data=[],
                sort_action="native",
                style_data_conditional=_delta_conditional("expectancy", "expectancy_r"),
                **_TABLE_STYLE,
            ),
            html.Div(id="backtest-strategy-slices", style={"marginTop": "12px"}),
            dcc.Store(id="backtest-strategy-picks"),
        ]
    )


def _slice_table(title: str, note: str, buckets: list[dict]):
    return html.Div(
        [
            html.H4(title, style={"marginTop": "14px", "marginBottom": "2px", "fontSize": "13px"}),
            html.P(note, className="benchmark-disclaimer"),
            dash_table.DataTable(
                columns=_SLICE_COLUMNS,
                data=[_strategy_row(b, "bucket", b["bucket"]) for b in buckets],
                style_data_conditional=_delta_conditional("expectancy", "expectancy_r"),
                **_TABLE_STYLE,
            ),
        ]
    )


@callback(
    Output("backtest-strategy-output", "children"),
    Output("backtest-strategy-table", "data"),
    Output("backtest-strategy-picks", "data"),
    Input("backtest-strategy-run", "n_clicks"),
    State("backtest-strategy-lookback", "value"),
    State("backtest-strategy-cost", "value"),
    State("backtest-strategy-max-symbols", "value"),
    State("backtest-strategy-force-refresh", "value"),
    prevent_initial_call=True,
)
def run_strategy_backtest_callback(_n_clicks, lookback_days, cost_bps, max_symbols, force_refresh):
    clients = backend_state.alpaca_clients
    universe = backend_state.universe
    if clients is None:
        return html.P(_NOT_READY, className="benchmark-disclaimer"), [], None
    if not universe:
        return html.P(_NO_UNIVERSE, className="benchmark-disclaimer"), [], None

    lookback = int(lookback_days or 30)
    cost = float(cost_bps or 0)
    symbols = _top_symbols(universe, int(max_symbols or 60))
    # inventory() rather than load_strategies(): a rule switched off in the
    # Signals panel is exactly the one whose measurement is interesting, and
    # loading it by stem is the loader's own way of overriding the toggle.
    states, errors = inventory()
    notes = [f"{e.filename}: {e.error}" for e in errors]

    rows: list[dict] = []
    picks_by_strategy: dict[str, list[dict]] = {}
    for state in states:
        loaded, load_errors = load_strategies(only=state.stem)
        notes += [f"{e.filename}: {e.error}" for e in load_errors]
        if not loaded:
            notes.append(f"{state.name}: could not be loaded from {state.filename}")
            continue
        try:
            report = run_async(
                run_strategy_backtest(
                    clients,
                    loaded[0],
                    symbols,
                    lookback_days=lookback,
                    cost_bps=cost,
                    force_refresh="refresh" in (force_refresh or []),
                )
            )
        except Exception as exc:
            logger.exception("Strategy backtest failed for %s", state.name)
            notes.append(f"{state.name}: {type(exc).__name__}: {exc}")
            continue

        stats = report["expectancy"]
        picks = report["picks"]
        picks_by_strategy[state.name] = picks
        row = _strategy_row(
            {
                "trades": stats["trades"],
                "expectancy_r": stats["expectancy_r"],
                "win_rate": stats["win_rate"],
                "avg_win_r": stats["avg_win_r"],
                "avg_loss_r": stats["avg_loss_r"],
                "ambiguous_pct": round(stats["ambiguous_exits"] / stats["trades"] * 100, 1) if stats["trades"] else None,
            },
            "strategy",
            state.name if state.enabled else f"{state.name} (off)",
        )
        rows.append(row)

    # Best expectancy first, and anything below the sample floor after it
    # regardless of how good the number looks -- same ordering rule as the
    # parameter sweep.
    rows.sort(key=lambda r: (bool(r["flag"]), -r["expectancy_r"]))
    summary = [
        html.P(
            f"{len(rows)} of {len(states)} strategies over {lookback} days and {len(symbols)} symbols, "
            f"{cost:.0f}bp per side. Rows marked "
            f"\u201cnoisy\u201d rest on fewer than {bucket_analysis.MIN_SAMPLE_SIZE} signals.",
            style={"margin": "4px 0"},
        )
    ]
    summary += [html.P(n, className="benchmark-disclaimer", style={"margin": "2px 0"}) for n in notes]
    return html.Div(summary), rows, picks_by_strategy


@callback(
    Output("backtest-strategy-slices", "children"),
    Input("backtest-strategy-table", "active_cell"),
    State("backtest-strategy-table", "derived_viewport_data"),
    State("backtest-strategy-picks", "data"),
    prevent_initial_call=True,
)
def show_strategy_slices(active_cell, rows, picks_by_strategy):
    """The clicked rule cut three ways. derived_viewport_data rather than
    data: the table sorts natively, and after a sort the active cell's row
    index refers to what is on screen."""
    if not active_cell or not rows or not picks_by_strategy:
        return None
    label = rows[active_cell["row"]]["strategy"]
    name = label.removesuffix(" (off)")
    picks = picks_by_strategy.get(name) or []
    if not picks:
        return html.P(f"{name} produced no signals over this window.", className="benchmark-disclaimer")

    # The VIX close per day from Cboe's keyless history -- the same source as
    # the header's reading, so "calm" here means what it means there. A
    # failed fetch leaves every signal in the "VIX not known" band rather
    # than dropping the slice.
    vix_by_day: dict = {}
    try:
        vix_by_day = run_async(_vix_closes())
    except Exception:
        logger.exception("VIX closes for the strategy slices could not be fetched")

    return html.Div(
        [
            html.H3(f"{name}: where it worked", style={"marginTop": "18px"}),
            _slice_table(
                "By part of the session",
                "When the signal fired. A rule that pays in the first half hour and gives it back after lunch "
                "is invisible in the whole-sample figure.",
                by_session_part(picks),
            ),
            _slice_table(
                "By how the trade ended",
                "A rule whose wins are all session-close exits is a rule whose target is never reached, "
                "whatever its expectancy.",
                by_exit_reason(picks),
            ),
            _slice_table(
                "By the day's VIX",
                "The tape the signal fired into, by that day's VIX close (Cboe delayed). Days without a close "
                "are kept apart rather than dropped, so the buckets still add up to the sample.",
                by_vix_band(picks, vix_by_day),
            ),
        ]
    )


async def _vix_closes() -> dict[date, float]:
    async with httpx.AsyncClient(timeout=20.0) as client:
        return await cboe_daily_closes(client, "_VIX")


def layout(**_kwargs):
    return html.Div(
        [
            html.H2("Backtest"),
            html.P(
                "Replays historical data through the app's actual ranking/alert functions -- not "
                "reimplementations -- to check whether the live scanner's assumptions hold up. Each "
                "section below fetches its own data on click and can take real time (tens of seconds "
                "to a couple minutes for larger runs); nothing here runs automatically. Click any row "
                "in a Picks table to load that symbol's chart on the right. \"Copy Picks\" copies everything "
                "currently shown (respecting the win/loss filter and sort) to the clipboard; the 📋 on a "
                "single row copies just that one pick.",
                className="benchmark-disclaimer",
            ),
            html.Div(
                [
                    html.Div([_strategy_section(), _daily_section(), _momentum_section(), _sweep_section()], className="backtest-main"),
                    _symbol_panel(),
                ],
                className="backtest-layout",
            ),
        ]
    )
