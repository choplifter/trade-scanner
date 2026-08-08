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
from dash import Dash, dcc, html

from app.dash_app import theme  # noqa: F401 -- import for its pio.templates side effect

dash_app = Dash(
    __name__,
    use_pages=True,
    routes_pathname_prefix="/",
    requests_pathname_prefix="/analytics/",
    title="Trading Dashboard Analytics",
)

dash_app.layout = html.Div(
    [
        html.Nav(
            [
                dcc.Link(page["name"], href=page["relative_path"], className="nav-link")
                for page in dash.page_registry.values()
            ],
            className="page-nav",
        ),
        dash.page_container,
    ]
)
