"""Screener page: build your own filters instead of picking one of three
fixed views.

Every control here is generated from the server's field registry
(app.scanners.screener.FIELDS) -- the field dropdowns, the operator choices
per field type, and the table's columns. Nothing about which fields exist or
what can be asked of them is written down in this file, so adding a field to
the registry adds it here with no change to this page.

Filter rows are rendered from a dcc.Store rather than as a fixed set of
inputs, because the number of filters is a user decision. Dash needs every
component that a callback touches to exist in the layout, so a variable
number of them means pattern-matching IDs (dash.ALL) over a list rebuilt
from state -- see build_filter_rows.
"""

import logging

import dash
from dash import ALL, Input, Output, State, callback, callback_context, dash_table, dcc, html

from app.dash_app.state import backend_state
from app.dash_app.theme import DELTA_DOWN, DELTA_UP, TEXT_MUTED
from app.scanners import screener
from app.scanners.screener_service import screen_live_rows

dash.register_page(__name__, path="/screener", name="Screener")

logger = logging.getLogger(__name__)

_NOT_READY = "Backend not ready yet -- try again in a moment."

# Which registry fields the table shows by default. Deliberately a starting
# point, not a limit: the column picker offers everything in the registry.
_DEFAULT_COLUMNS = [
    "symbol", "last_price", "pct_change", "rvol", "dollar_volume_today", "float_shares",
]

_OPERATOR_LABELS = {
    "gt": "greater than",
    "gte": "at least",
    "lt": "less than",
    "lte": "at most",
    "between": "between",
    "eq": "is",
    "ne": "is not",
    "contains": "contains",
    "in": "is one of",
    "is_true": "is true",
    "is_false": "is false",
}

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
        {"if": {"column_id": "exchange"}, "textAlign": "left"},
        {"if": {"column_id": "company_name"}, "textAlign": "left"},
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


def _field_options():
    return [{"label": f.label, "value": f.name} for f in screener.FIELDS if not f.sortable_only]


def _filterable_field_options():
    return [{"label": f.label, "value": f.name} for f in screener.FIELDS if f.operators]


def _operator_options(field_name: str):
    spec = screener.FIELDS_BY_NAME.get(field_name)
    if spec is None:
        return []
    return [{"label": _OPERATOR_LABELS.get(op, op), "value": op} for op in spec.operators]


def _needs_value(op: str) -> bool:
    return op not in ("is_true", "is_false")


def build_filter_rows(filters: list[dict]):
    """One row per filter: field, operator, and however many value boxes
    that operator needs.

    Value inputs are always present but hidden when the operator doesn't use
    one (booleans), rather than omitted -- Dash resolves pattern-matching
    inputs positionally, so a row that sometimes has an input and sometimes
    doesn't would shift every later row's values by one.
    """
    rows = []
    for index, entry in enumerate(filters):
        field_name = entry.get("field") or screener.FIELDS[0].name
        operators = _operator_options(field_name)
        op = entry.get("op") or (operators[0]["value"] if operators else "gt")
        show_value = _needs_value(op)
        show_second = op == "between"
        rows.append(
            html.Div(
                [
                    dcc.Dropdown(
                        id={"type": "screener-field", "index": index},
                        options=_filterable_field_options(),
                        value=field_name,
                        clearable=False,
                        style={"width": "190px"},
                    ),
                    dcc.Dropdown(
                        id={"type": "screener-op", "index": index},
                        options=operators,
                        value=op,
                        clearable=False,
                        style={"width": "150px"},
                    ),
                    dcc.Input(
                        id={"type": "screener-value", "index": index},
                        value=entry.get("value"),
                        type="text",
                        placeholder="value",
                        style={"width": "110px", "display": "block" if show_value else "none"},
                    ),
                    dcc.Input(
                        id={"type": "screener-value2", "index": index},
                        value=entry.get("value2"),
                        type="text",
                        placeholder="and",
                        style={"width": "110px", "display": "block" if show_second else "none"},
                    ),
                    html.Button(
                        "✕",
                        id={"type": "screener-remove", "index": index},
                        n_clicks=0,
                        title="Remove this filter",
                        style={"border": "none", "background": "none", "cursor": "pointer", "color": TEXT_MUTED},
                    ),
                ],
                style={"display": "flex", "gap": "8px", "alignItems": "center", "marginBottom": "6px"},
            )
        )
    if not rows:
        rows.append(
            html.Div("No filters -- showing everything tradable.", className="benchmark-disclaimer")
        )
    return rows


def _coerce(raw, op: str):
    """Filter values arrive as text. Numeric operators need numbers; text
    operators keep the string. A value that won't parse is left as-is and the
    screener's own comparison drops it, rather than raising here.
    """
    if raw is None or raw == "":
        return None
    if op in ("eq", "ne", "contains"):
        return raw
    if op == "in":
        return [part.strip() for part in str(raw).split(",") if part.strip()]
    try:
        return float(raw)
    except (TypeError, ValueError):
        return raw


def _screen_from_state(filters, sort_by, direction, limit) -> screener.Screen:
    parsed = []
    for entry in filters:
        op = entry.get("op") or "gt"
        parsed.append(
            screener.Filter(
                field=entry.get("field"),
                op=op,
                value=_coerce(entry.get("value"), op),
                value2=_coerce(entry.get("value2"), op),
            )
        )
    return screener.Screen(
        filters=parsed,
        sort_by=sort_by or "pct_change",
        descending=direction != "asc",
        limit=int(limit or 100),
    )


def _format(value, field_name: str) -> str:
    spec = screener.FIELDS_BY_NAME.get(field_name)
    if value is None:
        return "—"
    if spec is None:
        return str(value)
    if spec.type == screener.BOOLEAN:
        return "Yes" if value else "No"
    if spec.type == screener.TEXT:
        return str(value)
    if spec.type == screener.PERCENT:
        return f"{value:+.2f}%"
    if spec.type == screener.CURRENCY:
        if abs(value) >= 1_000_000_000:
            return f"${value / 1_000_000_000:.2f}B"
        if abs(value) >= 1_000_000:
            return f"${value / 1_000_000:.1f}M"
        return f"${value:,.2f}"
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if abs(value) >= 1_000:
        return f"{value / 1_000:.0f}K"
    return f"{value:,.2f}"


def _result_rows(payload: dict, columns: list[str]) -> list[dict]:
    derived = payload.get("derived", {})
    rows = []
    for row in payload["rows"]:
        cells = {}
        for name in columns:
            spec = screener.FIELDS_BY_NAME.get(name)
            raw = derived.get(name, {}).get(row["symbol"]) if spec and spec.derived else row.get(name)
            cells[name] = _format(raw, name)
            if name == "pct_change":
                cells["pct_change_num"] = raw
        rows.append(cells)
    return rows


def layout(**_kwargs):
    return html.Div(
        [
            html.H2("Screener"),
            html.P(
                "Build your own filters instead of choosing between fixed views. Every field, "
                "operator and column below comes from the server's field registry, so this page "
                "never needs changing when a new screenable field is added. Presets are ordinary "
                "screens -- load one and edit it.",
                className="benchmark-disclaimer",
            ),
            html.Div(
                [
                    html.Div(
                        [
                            html.Label("Preset", style={"fontSize": "11px", "color": TEXT_MUTED, "display": "block"}),
                            dcc.Dropdown(
                                id="screener-preset",
                                options=[
                                    {"label": p["label"], "value": p["name"]} for p in screener.preset_list()
                                ],
                                placeholder="Load a preset…",
                                style={"width": "220px"},
                            ),
                        ]
                    ),
                    html.Div(
                        [
                            html.Label("Sort by", style={"fontSize": "11px", "color": TEXT_MUTED, "display": "block"}),
                            dcc.Dropdown(
                                id="screener-sort",
                                options=[{"label": f.label, "value": f.name} for f in screener.FIELDS],
                                value="pct_change",
                                clearable=False,
                                style={"width": "180px"},
                            ),
                        ]
                    ),
                    html.Div(
                        [
                            html.Label("Direction", style={"fontSize": "11px", "color": TEXT_MUTED, "display": "block"}),
                            dcc.Dropdown(
                                id="screener-direction",
                                options=[{"label": "High → Low", "value": "desc"}, {"label": "Low → High", "value": "asc"}],
                                value="desc",
                                clearable=False,
                                style={"width": "140px"},
                            ),
                        ]
                    ),
                    html.Div(
                        [
                            html.Label("Limit", style={"fontSize": "11px", "color": TEXT_MUTED, "display": "block"}),
                            dcc.Input(id="screener-limit", type="number", value=100, min=1, step=10, style={"width": "90px"}),
                        ]
                    ),
                ],
                style={"display": "flex", "gap": "16px", "alignItems": "flex-end", "flexWrap": "wrap", "marginBottom": "12px"},
            ),
            html.H4("Filters", style={"marginBottom": "6px"}),
            html.Div(id="screener-filter-rows"),
            html.Div(
                [
                    html.Button("+ Add filter", id="screener-add", n_clicks=0),
                    html.Button("Run Screen", id="screener-run", n_clicks=0, style={"fontWeight": "600"}),
                ],
                style={"display": "flex", "gap": "10px", "marginTop": "8px", "marginBottom": "14px"},
            ),
            html.Div(
                [
                    html.Label("Columns", style={"fontSize": "11px", "color": TEXT_MUTED, "display": "block"}),
                    dcc.Dropdown(
                        id="screener-columns",
                        options=_field_options(),
                        value=_DEFAULT_COLUMNS,
                        multi=True,
                    ),
                ],
                style={"marginBottom": "12px"},
            ),
            dcc.Loading(html.Div(id="screener-summary"), type="circle"),
            dash_table.DataTable(id="screener-table", columns=[], data=[], page_size=25, **_TABLE_STYLE),
            dcc.Store(id="screener-filters", data=[]),
        ]
    )


@callback(
    Output("screener-filters", "data"),
    Input("screener-add", "n_clicks"),
    Input({"type": "screener-remove", "index": ALL}, "n_clicks"),
    Input({"type": "screener-field", "index": ALL}, "value"),
    Input({"type": "screener-op", "index": ALL}, "value"),
    Input({"type": "screener-value", "index": ALL}, "value"),
    Input({"type": "screener-value2", "index": ALL}, "value"),
    Input("screener-preset", "value"),
    State("screener-filters", "data"),
    prevent_initial_call=True,
)
def update_filters(_add, _removes, fields, ops, values, values2, preset_name, current):
    """Single owner of the filter list.

    Everything that can change a filter feeds this one callback, because Dash
    forbids two callbacks writing the same Output -- adding, removing,
    editing a row and loading a preset are all edits to the same list.
    """
    triggered = callback_context.triggered_id
    current = current or []

    if triggered == "screener-preset":
        if not preset_name:
            return dash.no_update
        preset = screener.PRESETS.get(preset_name)
        return [f.model_dump() for f in preset["screen"].filters] if preset else []

    if triggered == "screener-add":
        first = screener.FIELDS_BY_NAME["pct_change"]
        return [*current, {"field": first.name, "op": first.operators[0], "value": None, "value2": None}]

    if isinstance(triggered, dict) and triggered.get("type") == "screener-remove":
        index = triggered["index"]
        return [entry for i, entry in enumerate(current) if i != index]

    # A field/operator/value edit: rebuild from what the inputs now hold. If
    # the field changed, its old operator may not be valid for the new type,
    # so fall back to that field's first operator rather than keeping one the
    # registry says doesn't apply.
    rebuilt = []
    for i, field_name in enumerate(fields):
        spec = screener.FIELDS_BY_NAME.get(field_name)
        op = ops[i] if i < len(ops) else None
        if spec and (op not in spec.operators):
            op = spec.operators[0] if spec.operators else "gt"
        rebuilt.append(
            {
                "field": field_name,
                "op": op,
                "value": values[i] if i < len(values) else None,
                "value2": values2[i] if i < len(values2) else None,
            }
        )
    return rebuilt


@callback(
    Output("screener-filter-rows", "children"),
    Input("screener-filters", "data"),
)
def render_filters(filters):
    return build_filter_rows(filters or [])


@callback(
    Output("screener-sort", "value"),
    Output("screener-direction", "value"),
    Input("screener-preset", "value"),
    prevent_initial_call=True,
)
def apply_preset_sort(preset_name):
    """A preset carries its sort as well as its filters -- loading "Top
    Losers" and getting a descending sort would silently invert it.
    """
    preset = screener.PRESETS.get(preset_name or "")
    if preset is None:
        return dash.no_update, dash.no_update
    return preset["screen"].sort_by, "desc" if preset["screen"].descending else "asc"


@callback(
    Output("screener-table", "columns"),
    Output("screener-table", "data"),
    Output("screener-table", "style_data_conditional"),
    Output("screener-summary", "children"),
    Input("screener-run", "n_clicks"),
    State("screener-filters", "data"),
    State("screener-sort", "value"),
    State("screener-direction", "value"),
    State("screener-limit", "value"),
    State("screener-columns", "value"),
    prevent_initial_call=True,
)
def run_screen_callback(_clicks, filters, sort_by, direction, limit, columns):
    engine = backend_state.scanner_engine
    settings = backend_state.settings
    if engine is None or settings is None:
        return [], [], [], html.P(_NOT_READY, className="benchmark-disclaimer")

    columns = columns or _DEFAULT_COLUMNS
    screen = _screen_from_state(filters or [], sort_by, direction, limit)
    try:
        payload = screen_live_rows(engine, settings, screen)
    except Exception:
        logger.exception("Screen failed")
        return [], [], [], html.P("Screen failed -- check backend logs.", className="benchmark-disclaimer")

    table_columns = [
        {"name": screener.FIELDS_BY_NAME[c].label, "id": c} for c in columns if c in screener.FIELDS_BY_NAME
    ]
    conditional = [
        {"if": {"filter_query": "{pct_change_num} >= 0", "column_id": "pct_change"}, "color": DELTA_UP},
        {"if": {"filter_query": "{pct_change_num} < 0", "column_id": "pct_change"}, "color": DELTA_DOWN},
    ]
    fallback = " (last completed session)" if payload["is_latest_session"] else ""
    summary = html.P(
        f"{payload['total_matched']} of {payload['tradable_size']} tradable symbols matched "
        f"({payload['universe_size']} in the universe); showing {len(payload['rows'])}{fallback}.",
        className="benchmark-disclaimer",
    )
    return table_columns, _result_rows(payload, columns), conditional, summary
