"""Plotly template mirroring the light-theme design tokens in
frontend/src/styles.css, so the Dash analytics pages read as part of the
same product instead of default Plotly styling. Importing this module
(done once, from app.dash_app) registers the template as Plotly's global
default -- every figure built anywhere in dash_app/pages picks it up
automatically, no per-figure wiring needed.

Scoped to the light palette only (not synced to OS dark-mode preference,
unlike the React app) -- that would need a clientside callback reading
prefers-color-scheme and threading it through every figure callback, more
than this pass calls for. A single consistent look beats an unstyled one.
"""

import plotly.graph_objects as go
import plotly.io as pio

PAGE_BG = "#f9f9f7"
SURFACE_BG = "#fcfcfb"
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
TEXT_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
BORDER = "rgba(11, 11, 11, 0.1)"
DELTA_UP = "#006300"
DELTA_DOWN = "#d03b3b"
ACCENT = "#2a78d6"
VWAP_COLOR = "#eda100"
STATUS_GOOD = "#0ca30c"
STATUS_WARNING = "#fab219"
STATUS_CRITICAL = "#d03b3b"

# Shared between the nav badge (dash_app/__init__.py) and the market
# conditions detail page (pages/market_conditions.py) -- kept here rather
# than defined in __init__.py so the detail page can import them without
# risking a circular import (Dash's use_pages=True auto-discovers page
# modules during the Dash(...) constructor call in __init__.py itself, so
# anything a page imports back from __init__ would need to already be
# defined above that line).
MARKET_CONDITIONS_LEVEL_LABEL = {"green": "Calm", "yellow": "Caution", "red": "Elevated Risk"}
MARKET_CONDITIONS_LEVEL_COLOR = {"green": STATUS_GOOD, "yellow": STATUS_WARNING, "red": STATUS_CRITICAL}

_FONT = dict(family="system-ui, -apple-system, 'Segoe UI', sans-serif", color=TEXT_PRIMARY, size=12)

_template = go.layout.Template()
_template.layout = go.Layout(
    paper_bgcolor=SURFACE_BG,
    plot_bgcolor=SURFACE_BG,
    font=_FONT,
    title=dict(font=dict(size=13, color=TEXT_SECONDARY)),
    xaxis=dict(
        gridcolor=GRIDLINE,
        linecolor=BORDER,
        zerolinecolor=GRIDLINE,
        tickfont=dict(color=TEXT_MUTED, size=11),
    ),
    yaxis=dict(
        gridcolor=GRIDLINE,
        linecolor=BORDER,
        zerolinecolor=GRIDLINE,
        tickfont=dict(color=TEXT_MUTED, size=11),
    ),
    colorway=[ACCENT, DELTA_UP, DELTA_DOWN, VWAP_COLOR, TEXT_SECONDARY],
)

pio.templates["trading_dash"] = _template
pio.templates.default = "trading_dash"
