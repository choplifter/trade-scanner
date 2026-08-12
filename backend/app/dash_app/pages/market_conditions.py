"""Full background/detail behind the market-conditions traffic light --
the compact badge (see app.dash_app's nav and the React header) only shows
a one-line "reasons" tooltip; this is where "why is it yellow" gets a real
answer. Reads backend_state directly, same in-process pattern as every
other Dash page (see e.g. scanner_heatmap.py), on the same poll cadence as
the badge itself.

Thresholds shown here are imported straight from
app.market_data.market_conditions rather than restated as separate numbers
-- displayed values can't drift from what actually drives the badge's
color.
"""

import dash
from dash import Input, Output, callback, dcc, html

from app.dash_app.state import backend_state
from app.dash_app.theme import (
    MARKET_CONDITIONS_LEVEL_COLOR,
    MARKET_CONDITIONS_LEVEL_LABEL,
    TEXT_MUTED,
)
from app.market_data.market_conditions import (
    _BREADTH_RED,
    _BREADTH_YELLOW,
    _TRACKED_COUNTRIES,
    _VIX_CHANGE_RED,
    _VIX_CHANGE_YELLOW,
    _VIX_RED,
    _VIX_YELLOW,
)

dash.register_page(__name__, path="/market-conditions", name="Market Conditions")

_CONDITIONS_POLL_MS = 60_000

_DISCLAIMER = (
    "Purely descriptive market weather, not a trade signal -- same non-advisory "
    "framing as AI Trade Ideas. This page explains what feeds the badge, not "
    "what to do about it."
)


def _section(title: str, children: list) -> html.Div:
    return html.Div(
        [html.H3(title), *children],
        style={"marginBottom": "28px"},
    )


def layout(**_kwargs):
    return html.Div(
        [
            html.H2("Market Conditions"),
            dcc.Interval(id="mc-page-interval", interval=_CONDITIONS_POLL_MS, n_intervals=0),
            html.Div(id="mc-page-body"),
        ],
        style={"maxWidth": "760px"},
    )


def _unavailable() -> html.Div:
    return html.Div(
        [
            html.P(
                "Not available -- FMP_API_KEY isn't configured, or the first refresh "
                "hasn't landed yet. Add FMP_API_KEY to backend/.env to enable this "
                "(the same key that powers float/market cap/short interest)."
            )
        ]
    )


def _level_badge(level: str) -> html.Span:
    color = MARKET_CONDITIONS_LEVEL_COLOR.get(level, TEXT_MUTED)
    return html.Span(
        MARKET_CONDITIONS_LEVEL_LABEL.get(level, level),
        style={
            "display": "inline-flex",
            "alignItems": "center",
            "padding": "6px 14px",
            "borderRadius": "999px",
            "border": f"1px solid {color}",
            "color": color,
            "fontWeight": "700",
            "fontSize": "14px",
        },
    )


def _vix_section(vix) -> html.Div:
    intro = html.P(
        "The CBOE Volatility Index (VIX) measures the market's expected 30-day "
        "volatility, derived from S&P 500 options prices -- a rough gauge of how "
        "much fear or complacency is priced into the market right now. Higher "
        "means options traders are paying more for protection, which historically "
        "clusters with selloffs and fast, wide-ranging moves."
    )
    thresholds = html.Ul(
        [
            html.Li(f"Below {_VIX_YELLOW:.0f}: calm"),
            html.Li(f"{_VIX_YELLOW:.0f}-{_VIX_RED:.0f}: caution"),
            html.Li(f"{_VIX_RED:.0f}+: elevated fear"),
            html.Li(
                f"Independent of level: a same-day move of +{_VIX_CHANGE_YELLOW:.0f}% "
                f"triggers caution and +{_VIX_CHANGE_RED:.0f}% triggers elevated fear "
                "-- a fast spike is a stress signal even if the absolute level is "
                "still nominally calm."
            ),
        ]
    )
    if vix is None:
        return _section("VIX", [intro, thresholds, html.P("No current reading.", style={"color": TEXT_MUTED})])
    reading = html.P(
        [
            html.Span(f"{vix['price']:.2f}", style={"fontSize": "20px", "fontWeight": "700"}),
            html.Span(
                f"  ({vix['change_pct']:+.2f}% today)",
                style={"color": TEXT_MUTED, "marginLeft": "8px"},
            ),
        ]
    )
    return _section("VIX", [reading, intro, thresholds])


def _events_section(events: list) -> html.Div:
    countries = ", ".join(sorted(_TRACKED_COUNTRIES))
    intro = html.P(
        f"Today's high-impact global economic calendar events (CPI, GDP releases, "
        f"central bank rate decisions, and similar market-moving releases) from "
        f"{countries} -- the economies whose scheduled releases are actually likely "
        "to move a US small/mid-cap scanner universe. Any high-impact event today "
        "is enough to trigger caution on its own, regardless of VIX or breadth, "
        "since these are scheduled moments of concentrated, hard-to-predict volatility."
    )
    if not events:
        body = html.P("No high-impact events scheduled today.", style={"color": TEXT_MUTED})
    else:
        body = html.Table(
            [
                html.Thead(html.Tr([html.Th("Time (UTC)"), html.Th("Country"), html.Th("Event")])),
                html.Tbody(
                    [
                        html.Tr(
                            [
                                html.Td(e["date"][11:16] if len(e["date"]) >= 16 else e["date"]),
                                html.Td(e["country"]),
                                html.Td(e["event"]),
                            ]
                        )
                        for e in events
                    ]
                ),
            ],
            style={"width": "100%", "borderCollapse": "collapse"},
        )
    return _section("Today's High-Impact Events", [intro, body])


def _breadth_section(breadth_pct) -> html.Div:
    intro = html.P(
        "The % of the live-tracked scanner universe currently trading above its "
        "prior close -- a one-sided signal: a broad rally isn't flagged as risk, "
        "only a broad selloff is. A single stock or sector moving on its own "
        "story is normal; the whole tape moving the same direction at once is a "
        "different, more macro kind of risk."
    )
    thresholds = html.Ul(
        [
            html.Li(f"Below {_BREADTH_RED:.0f}%: broad selloff"),
            html.Li(f"{_BREADTH_RED:.0f}-{_BREADTH_YELLOW:.0f}%: mixed/weak"),
            html.Li(f"{_BREADTH_YELLOW:.0f}%+: not flagged"),
        ]
    )
    if breadth_pct is None:
        value = html.P("No current reading.", style={"color": TEXT_MUTED})
    else:
        value = html.P(f"{breadth_pct:.0f}% green", style={"fontSize": "20px", "fontWeight": "700"})
    return _section("Scanner Breadth", [value, intro, thresholds])


@callback(Output("mc-page-body", "children"), Input("mc-page-interval", "n_intervals"))
def update_market_conditions_page(_n_intervals):
    engine = backend_state.scanner_engine
    conditions = engine.market_conditions if engine is not None else None
    if conditions is None:
        return _unavailable()

    return html.Div(
        [
            html.Div(
                [_level_badge(conditions.level), html.P(_DISCLAIMER, style={"color": TEXT_MUTED, "marginTop": "10px"})],
                style={"marginBottom": "20px"},
            ),
            html.Ul([html.Li(r) for r in conditions.reasons], style={"marginBottom": "28px"}),
            _vix_section(
                {"price": conditions.vix.price, "change_pct": conditions.vix.change_pct}
                if conditions.vix
                else None
            ),
            _events_section(
                [
                    {"date": e.date.isoformat(), "country": e.country, "event": e.event}
                    for e in conditions.high_impact_events_today
                ]
            ),
            _breadth_section(conditions.breadth_pct),
        ]
    )
