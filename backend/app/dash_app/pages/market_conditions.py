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

from datetime import datetime

import dash
import plotly.graph_objects as go
from dash import Input, Output, callback, dcc, html

from app.dash_app.state import backend_state
from app.dash_app.theme import (
    DELTA_DOWN,
    DELTA_UP,
    MARKET_CONDITIONS_LEVEL_COLOR,
    MARKET_CONDITIONS_LEVEL_LABEL,
    TEXT_MUTED,
)
from app.market_data.gamma_exposure import SYMBOLS as GEX_SYMBOLS
from app.market_data.gamma_exposure import _EXPIRATION_DAYS_AHEAD, _STRIKE_PCT_RANGE, top_walls
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


def _offset_label(moment: datetime) -> str:
    """Explicit UTC offset (e.g. "UTC+02:00") rather than the OS-provided
    %Z name (e.g. "Mitteleuropäische Sommerzeit" on a German-locale
    Windows host) so it's unambiguous regardless of host locale/language
    settings.
    """
    offset = moment.strftime("%z")
    return f"UTC{offset[:3]}:{offset[3:]}" if offset else "local time"


def _as_of_line() -> html.P:
    """Current date/time in whatever timezone this backend process is
    actually running in -- for this app's single-user, same-machine
    deployment (backend and browser both on the user's own PC) that's the
    same as the viewer's local time.
    """
    now = datetime.now().astimezone()
    return html.P(
        f"As of {now.strftime('%A, %B %d, %Y')} · {now.strftime('%H:%M')} ({_offset_label(now)})",
        style={"color": TEXT_MUTED, "marginBottom": "20px"},
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


def _unavailable(gex: dict) -> html.Div:
    # GEX rides on Alpaca, not FMP, so it can be available even when the
    # FMP-backed VIX/events/breadth section below can't be -- shown here too
    # rather than hidden behind the FMP-specific message.
    return html.Div(
        [
            _as_of_line(),
            html.P(
                "VIX/events/breadth not available -- FMP_API_KEY isn't configured, or "
                "the first refresh hasn't landed yet. Add FMP_API_KEY to backend/.env "
                "to enable this (the same key that powers float/market cap/short "
                "interest)."
            ),
            _gex_section(gex),
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
        # Events come from FMP in UTC -- converted to this host's local
        # timezone here (see _as_of_line) so the times line up with "now"
        # as the viewer actually experiences it, not a raw UTC print.
        local_events = [(e["date"].astimezone(), e["country"], e["event"]) for e in events]
        time_label = f"Time ({_offset_label(local_events[0][0])})"
        body = html.Table(
            [
                html.Thead(html.Tr([html.Th(time_label), html.Th("Country"), html.Th("Event")])),
                html.Tbody(
                    [
                        html.Tr(
                            [
                                html.Td(local_dt.strftime("%H:%M")),
                                html.Td(country),
                                html.Td(event),
                            ]
                        )
                        for local_dt, country, event in local_events
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


def _gex_wall_figure(reading) -> go.Figure:
    """The gamma-wall profile: net dollar gamma exposure per strike, colored
    by sign (DELTA_UP/DELTA_DOWN -- the same up/down mapping this page's
    other figures would use), with a marker at the current spot price for
    context. Scaled to millions rather than GexReading's raw dollars -- a
    single strike's exposure is naturally much smaller than the symbol-wide
    total shown in the summary line above it.
    """
    strikes = [row.strike for row in reading.by_strike]
    values_m = [row.net_gex / 1e6 for row in reading.by_strike]
    colors = [DELTA_UP if row.net_gex >= 0 else DELTA_DOWN for row in reading.by_strike]

    figure = go.Figure([go.Bar(x=strikes, y=values_m, marker_color=colors)])
    figure.add_vline(
        x=reading.spot_price,
        line_dash="dot",
        line_color=TEXT_MUTED,
        annotation_text="Spot",
        annotation_position="top",
    )
    figure.update_layout(
        height=260,
        margin={"l": 60, "r": 20, "t": 20, "b": 40},
        xaxis_title="Strike",
        yaxis_title="Net GEX ($M)",
        showlegend=False,
    )
    return figure


def _gex_section(gex: dict) -> html.Div:
    intro = html.P(
        "Net dealer gamma exposure (GEX) for every symbol a reading has been "
        "computed for -- SPY, QQQ, TSLA, NVDA and PLTR are kept warm here; the "
        "rest are computed on request from the React dashboard. A rough gauge of how "
        "options-market positioning tends to affect moves in the underlying. "
        "Positive means dealers are estimated net long gamma and tend to dampen "
        "moves (buy dips, sell rips); negative means dealers are estimated net "
        "short gamma and tend to amplify them (sell into drops, buy into rallies). "
        f"Computed from strikes within {_STRIKE_PCT_RANGE:.0%} of spot, expiring "
        f"within {_EXPIRATION_DAYS_AHEAD} days (today's 0DTE contracts are excluded "
        "-- Alpaca cannot compute greeks for a contract expiring the same day), "
        "using Alpaca's own Black-Scholes-modeled greeks rather than exchange-"
        "published Greeks -- treat the number as directional, not precise, and as "
        "one widely-used sign convention among traders who track this, not a "
        "settled industry standard."
    )
    rows = []
    for symbol in GEX_SYMBOLS:
        reading = gex.get(symbol)
        if reading is None:
            rows.append(
                html.P(f"{symbol}: no current reading.", style={"color": TEXT_MUTED})
            )
            continue
        rows.append(
            html.P(
                [
                    html.Span(symbol, style={"fontWeight": "700", "marginRight": "8px"}),
                    html.Span(
                        f"{reading.net_gex / 1e9:+.2f}B",
                        style={"fontSize": "20px", "fontWeight": "700"},
                    ),
                    html.Span(
                        f"  (calls {reading.call_gex / 1e9:+.2f}B · puts {reading.put_gex / 1e9:+.2f}B · "
                        f"{reading.contracts_used} contracts · spot {reading.spot_price:.2f} · "
                        f"as of {reading.as_of.astimezone().strftime('%H:%M')})",
                        style={"color": TEXT_MUTED, "marginLeft": "8px"},
                    ),
                ]
            )
        )
        if reading.by_strike:
            wall = top_walls(reading.by_strike, n=1)[0]
            rows.append(
                html.P(
                    f"Largest concentration: {wall.strike:g} strike ({wall.net_gex / 1e9:+.2f}B)",
                    style={"color": TEXT_MUTED, "marginBottom": "4px"},
                )
            )
            rows.append(dcc.Graph(figure=_gex_wall_figure(reading), style={"marginBottom": "16px"}))
    return _section("Gamma Exposure (GEX)", [*rows, intro])


@callback(Output("mc-page-body", "children"), Input("mc-page-interval", "n_intervals"))
def update_market_conditions_page(_n_intervals):
    engine = backend_state.scanner_engine
    conditions = engine.market_conditions if engine is not None else None
    gex = engine.gex if engine is not None else {}
    if conditions is None:
        return _unavailable(gex)

    return html.Div(
        [
            _as_of_line(),
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
                    {"date": e.date, "country": e.country, "event": e.event}
                    for e in conditions.high_impact_events_today
                ]
            ),
            _breadth_section(conditions.breadth_pct),
            _gex_section(gex),
        ]
    )
