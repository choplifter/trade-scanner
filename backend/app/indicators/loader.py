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
    MAX_TIMEFRAME: str (optional) -- the coarsest chart timeframe this still
        says something at; omitted means every timeframe
    def compute(ctx: IndicatorContext) -> dict[str, ...] -- sub-series name
        -> value (float|None for "level") or point list (for "series")

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
            }
        except Exception:
            logger.exception("Indicator %s failed to load/compute -- skipping", path.name)
            continue
        results.append(result)
    return results
