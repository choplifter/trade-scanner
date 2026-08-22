"""Discovers and runs every indicator file in this directory on each call --
deliberately re-executed from source every time (not cached via
sys.modules/import_module) so adding or editing a .py file here takes effect
on the very next /bars request, with no backend restart. This directory
holds a handful of small files computing simple aggregates, so re-exec'ing
them per request is cheap; /bars is an on-demand, per-view endpoint, not a
tight poll loop.

Single-user app -- these files are trusted code, same as any other module in
this codebase. No sandboxing.

Each indicator file exposes:
    NAME: str -- display group name, e.g. "Premarket Range"
    KIND: "level" | "series"
    COLORS: dict[str, str] (optional) -- sub-series name -> hex color
    STYLE: dict (optional) -- how this indicator's lines are drawn:
        {"width": int, "dash": "solid"|"dotted"|"dashed"|"large-dashed"|
         "sparse-dotted"}. Either key may be omitted. Omitting STYLE
        entirely leaves the client's default for the indicator's kind, which
        is what every indicator got before this existed.
    MAX_TIMEFRAME: str (optional) -- the coarsest chart timeframe this still
        says something at; omitted means every timeframe
    def compute(ctx: IndicatorContext) -> dict[str, ...] -- sub-series name
        -> value (float|None for "level") or point list (for "series")

STYLE is per indicator rather than one setting in the renderer because
weight and dash pattern are how a reader tells one *kind* of line from
another at a glance -- a level tested nine times and a premarket boundary
should not look identical just because both happen to be horizontal. Keeping
it here means an indicator file owns its whole appearance, colour included,
instead of half of it living in the chart component.

MAX_TIMEFRAME exists because an indicator describing one period is noise
once a single candle covers that period or more: yesterday's high sits
*inside* one bar of a weekly chart, so drawing it there adds a line that
cannot be crossed. Filtering here rather than inside each compute() keeps
the rule declarative and keeps zoom out of the indicator files entirely --
they are handed a context and asked for numbers, never asked to decide
whether they should have been called.

Filtered indicators are omitted from the response rather than returned
empty, so the client renders nothing for them rather than an empty legend
entry.
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from types import ModuleType

from app.indicators.context import IndicatorContext, timeframe_rank

logger = logging.getLogger(__name__)

_DIR = Path(__file__).parent
_EXCLUDED = {"__init__.py", "context.py", "loader.py"}

# The dash patterns STYLE may name. Mirrored by the client, which maps them to
# its own drawing enum -- kept here as well so a typo in an indicator file is
# caught by a test rather than silently rendering as the default line.
DASH_PATTERNS = ("solid", "dotted", "dashed", "large-dashed", "sparse-dotted")


# The kind an entry carries when its file could not be loaded or computed.
# It has no series, so nothing draws it -- the client shows it in the legend
# instead, which is the whole point.
KIND_ERROR = "error"


def module_name(path: Path) -> str:
    """A readable name for a file that may have failed before defining NAME."""
    return path.stem.replace("_", " ").title()


def _load_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"_indicator_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # fresh module object every call -- see module docstring
    return module


def run_indicators(ctx: IndicatorContext) -> list[dict]:
    results = []
    for path in sorted(_DIR.glob("*.py")):
        if path.name in _EXCLUDED:
            continue
        try:
            module = _load_module(path)
            max_timeframe = getattr(module, "MAX_TIMEFRAME", None)
            if max_timeframe is not None and timeframe_rank(ctx.timeframe) > timeframe_rank(
                max_timeframe
            ):
                continue
            result = {
                "name": module.NAME,
                "kind": module.KIND,
                "series": module.compute(ctx),
                "colors": getattr(module, "COLORS", {}),
                "style": getattr(module, "STYLE", {}),
            }
        except Exception as exc:
            logger.exception("Indicator %s failed to load/compute", path.name)
            # Reported, not dropped. A skipped indicator draws nothing, which
            # on a chart is indistinguishable from an indicator that found
            # nothing -- so a broken file looks like a quiet market.
            #
            # Observed exactly that way: these files are re-executed per
            # request while the modules they import are not, so editing a
            # shared module leaves the two out of step until a restart. The
            # indicator called a function signature that did not exist yet,
            # raised, and simply vanished from the chart with no sign of why.
            results.append(
                {
                    "name": module_name(path),
                    "kind": KIND_ERROR,
                    "series": {},
                    "colors": {},
                    "style": {},
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        results.append(result)
    return results
