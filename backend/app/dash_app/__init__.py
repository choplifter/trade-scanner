"""Plotly Dash analytics sub-app, mounted under the main FastAPI app at
/analytics (see app.main). Built at import time -- see app.dash_app.state
for how it gets access to live backend objects, which only exist after
FastAPI's lifespan() startup has run.

Prefix handling is split deliberately, not set via the simpler
url_base_pathname:
- Starlette's Mount("/analytics", ...) already strips "/analytics" from
  scope["path"] before a2wsgi ever sees the request (that's standard ASGI
  sub-app behavior), so the WSGI environ Flask/Dash receives has PATH_INFO
  starting at "/" -- routes_pathname_prefix must be "/" to match that, or
  Flask 404s on every request (confirmed empirically: url_base_pathname=
  "/analytics/" bakes the prefix into Dash's own route table, which then
  never matches the already-stripped path).
- The browser doesn't know any of this -- the HTML/JS Dash serves still
  needs to fetch assets/callbacks from "/analytics/...", so
  requests_pathname_prefix stays "/analytics/".
"""

import dash
from dash import Dash, Input, Output, callback, dcc, html

from app.dash_app import theme  # noqa: F401 -- import for its pio.templates side effect
from app.dash_app.state import backend_state
from app.dash_app.theme import STATUS_CRITICAL, STATUS_GOOD, STATUS_WARNING, TEXT_MUTED

dash_app = Dash(
    __name__,
    use_pages=True,
    routes_pathname_prefix="/",
    requests_pathname_prefix="/analytics/",
    title="Trading Dashboard Analytics",
)

_CONDITIONS_POLL_MS = 60_000
_LEVEL_LABEL = {"green": "Calm", "yellow": "Caution", "red": "Elevated Risk"}
_LEVEL_COLOR = {"green": STATUS_GOOD, "yellow": STATUS_WARNING, "red": STATUS_CRITICAL}

_BADGE_STYLE = {
    "display": "inline-flex",
    "alignItems": "center",
    "gap": "6px",
    "fontSize": "12px",
    "fontWeight": "600",
    "padding": "4px 10px",
    "borderRadius": "999px",
    "border": "1px solid rgba(11, 11, 11, 0.1)",
    "color": "#52514e",
    "marginLeft": "auto",
}

dash_app.layout = html.Div(
    [
        html.Nav(
            [
                *(
                    dcc.Link(page["name"], href=page["relative_path"], className="nav-link")
                    for page in dash.page_registry.values()
                ),
                dcc.Interval(id="market-conditions-interval", interval=_CONDITIONS_POLL_MS, n_intervals=0),
                html.Span(id="market-conditions-badge", style={"display": "none"}),
            ],
            className="page-nav",
        ),
        dash.page_container,
    ]
)


@callback(
    Output("market-conditions-badge", "children"),
    Output("market-conditions-badge", "style"),
    Output("market-conditions-badge", "title"),
    Input("market-conditions-interval", "n_intervals"),
)
def update_market_conditions_badge(_n_intervals):
    """Same red/yellow/green readout as the React header's badge (see
    app.market_data.market_conditions) -- reads backend_state directly
    rather than an HTTP round-trip, same pattern as every other Dash
    page/callback in this app. Hidden (not an error state) when the engine
    hasn't produced a reading yet -- no FMP_API_KEY configured, or the
    first refresh just hasn't landed yet.
    """
    engine = backend_state.scanner_engine
    conditions = engine.market_conditions if engine is not None else None
    if conditions is None:
        return None, {"display": "none"}, ""

    dot_style = {
        "display": "inline-block",
        "width": "8px",
        "height": "8px",
        "borderRadius": "50%",
        "background": _LEVEL_COLOR.get(conditions.level, TEXT_MUTED),
    }
    label = _LEVEL_LABEL.get(conditions.level, conditions.level)
    children = [html.Span(style=dot_style), label]
    return children, _BADGE_STYLE, " · ".join(conditions.reasons)
