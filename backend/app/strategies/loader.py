"""Discovers every strategy file in this directory, the way
app.indicators.loader discovers indicators: drop a .py file in here and it is
picked up, no registry to edit and no import to add.

One deliberate difference, and it is the reason this is a separate loader
rather than a copy. The indicator loader re-executes every file on every
call, so an edit shows up on the next /bars request. It can afford that
because /bars is an on-demand, per-view endpoint. A strategy is evaluated
once per bar per symbol -- a 120-symbol, 30-day backtest is on the order of
200,000 evaluations -- so re-executing per evaluation is not an option.

So loading is explicit and the *caller* picks the cadence:

  * The backtest calls load_strategies() once, at the start of a run. That is
    also the correct semantics rather than merely the fast one: a report has
    to describe one version of a rule, not whichever version happened to be
    on disk as the walk passed each bar.
  * The live path calls it once per poll tick. A handful of small files
    re-executed every few seconds costs about what the indicator loader
    already costs per chart request, and it keeps the drop-in-and-it-works
    behaviour where it matters most.

Single-user app -- these files are trusted code, same as any other module
here. No sandboxing.

Each strategy file exposes:
    NAME: str -- display name, e.g. "VWAP Reclaim"
    ENABLED: bool (optional, default True) -- False parks a strategy without
        deleting the file, the equivalent of detaching an expert advisor
    def evaluate(ctx: StrategyContext) -> Signal | None

Unlike the indicator loader, a file that fails to load is *reported*, not
just logged. A missing indicator line is visible on the chart; a missing
signal looks exactly like a quiet market. load_strategies returns the
failures alongside the strategies so a caller can say "3 loaded, 1 failed"
instead of silently trading fewer rules than the user believes are running.

evaluate() itself is not wrapped here. The backtest wants an exception to
stop the run -- a report built from a rule that threw on some bars would
understate its own signal count -- while the live path wraps each call so one
bad file cannot stall the poll loop. Those are opposite requirements, so the
choice belongs to the caller rather than to this module.
"""

from __future__ import annotations

import importlib.util
import logging
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from app.strategies.context import Signal, StrategyContext

logger = logging.getLogger(__name__)

_DIR = Path(__file__).parent
# Infrastructure in this directory, not strategies. Listed explicitly rather
# than inferred: a file that *tries* to be a strategy and gets NAME wrong must
# be reported, so "has no NAME" cannot be the test for "is not a strategy".
# The cost is that a new helper added here is a load error until it is listed
# -- which is noisy in exactly the right direction, and is how it was caught.
_EXCLUDED = {"__init__.py", "breakout.py", "context.py", "loader.py", "runner.py"}


@dataclass(frozen=True)
class LoadedStrategy:
    """One strategy file, ready to evaluate.

    Carries `filename` as well as `name` because the two answer different
    questions: a report names the strategy, a load error names the file to go
    and fix.
    """

    name: str
    filename: str
    _evaluate: object

    def evaluate(self, ctx: StrategyContext) -> Signal | None:
        return self._evaluate(ctx)  # type: ignore[operator]


@dataclass(frozen=True)
class LoadError:
    filename: str
    error: str


def _load_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"_strategy_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # fresh module object every call -- see module docstring
    return module


def _validate(module: ModuleType, filename: str) -> LoadedStrategy:
    """Check the contract before anything is allowed to trade on it.

    Checked here, once, rather than trusted at the call site: a file missing
    evaluate() would otherwise raise on some later bar, deep inside a walk,
    and read as a broken backtest rather than a malformed file.
    """
    name = getattr(module, "NAME", None)
    if not isinstance(name, str):
        raise TypeError("NAME must be a string.")
    if not name.strip():
        raise ValueError("NAME must not be blank -- it labels the rule in every report.")

    evaluate = getattr(module, "evaluate", None)
    if not callable(evaluate):
        raise TypeError("evaluate(ctx) is missing or not callable.")

    return LoadedStrategy(name=name, filename=filename, _evaluate=evaluate)


def load_strategies(
    only: str | None = None,
) -> tuple[list[LoadedStrategy], list[LoadError]]:
    """Every enabled strategy in this directory, plus whatever failed to load.

    `only` selects a single strategy by NAME or by filename stem, for running
    one rule at a time. A name that matches nothing yields an empty list --
    the caller reports that, since "no such strategy" and "no signals" are
    very different answers and must not look alike.
    """
    strategies: list[LoadedStrategy] = []
    errors: list[LoadError] = []

    for path in sorted(_DIR.glob("*.py")):
        if path.name in _EXCLUDED:
            continue
        try:
            module = _load_module(path)
            if not getattr(module, "ENABLED", True):
                continue
            strategy = _validate(module, path.name)
        except Exception as exc:
            logger.exception("Strategy %s failed to load -- skipping", path.name)
            errors.append(LoadError(filename=path.name, error=f"{type(exc).__name__}: {exc}"))
            continue

        if only is not None and only not in (strategy.name, path.stem):
            continue
        strategies.append(strategy)

    return strategies, errors


def available_names() -> list[str]:
    """Strategy names, for a CLI's error message or a UI's dropdown."""
    strategies, _ = load_strategies()
    return [s.name for s in strategies]
