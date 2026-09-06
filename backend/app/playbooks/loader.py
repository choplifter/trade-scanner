"""Discovers every playbook file in this directory, the way
app.strategies.loader discovers strategies: drop a .py file in here and it
is offered, no registry to edit and no import to add.

Loaded per use -- the runner loads once per tick, the backtest once per run
-- so an edit shows up on the next tick, and a report describes one version
of a rule. A handful of small files re-executed every few seconds costs
about what the strategies loader already costs.

Each playbook file exposes:
    NAME: str            -- display name, e.g. "Wheel"
    DESCRIPTION: str     -- one paragraph for the tab
    ENABLED: bool        -- optional, default True; False parks the file
    PARAMS: list[ParamSpec]  -- the knobs a campaign is started with
    def next_step(ctx: PlaybookContext) -> Action | None
    def chain_windows(params) -> list[tuple[int, int]]  -- optional: the DTE
        windows whose chains the runner and the backtest load for the
        script (default: min_dte..max_dte). A script that also holds a
        long-dated leg names that window here too.

Unlike a strategy a playbook has no dashboard switch: it is instantiated per
campaign, not scanned over a universe, so ENABLED is enough. A file that
fails to load is *reported*, not just logged, for the same reason the
strategies loader reports: a missing playbook looks exactly like "nothing
proposed", and the tab should say "1 failed" out loud.

Single-user app -- these files are trusted code. No sandboxing.
"""

from __future__ import annotations

import importlib.util
import logging
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Literal

logger = logging.getLogger(__name__)

_DIR = Path(__file__).parent
# Infrastructure in this directory, not playbooks. Listed explicitly rather
# than inferred: a file that *tries* to be a playbook and gets NAME wrong
# must be reported, so "has no NAME" cannot be the test for "is not a
# playbook". A helper added here is a load error until it is listed --
# noisy in exactly the right direction.
_EXCLUDED = {
    "__init__.py",
    "actions.py",
    "alpaca_adapter.py",
    "backtest.py",
    "context.py",
    "loader.py",
    "paper_loop.py",
    "runner.py",
    "snapshot.py",
    "store.py",
    "synthetic_chain.py",
}

ParamType = Literal["int", "float", "bool"]


@dataclass(frozen=True)
class ParamSpec:
    """One knob: its name in `params`, its type, default and bounds, and the
    label the tab shows."""

    name: str
    type: ParamType
    default: object
    label: str
    min: float | None = None
    max: float | None = None
    step: float | None = None
    help: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "default": self.default,
            "label": self.label,
            "min": self.min,
            "max": self.max,
            "step": self.step,
            "help": self.help,
        }

    def coerce(self, value):
        if self.type == "bool":
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
        number = int(value) if self.type == "int" else float(value)
        if self.min is not None and number < self.min:
            raise ValueError(f"{self.name} must be at least {self.min:g}")
        if self.max is not None and number > self.max:
            raise ValueError(f"{self.name} must be at most {self.max:g}")
        return number


@dataclass(frozen=True)
class LoadedPlaybook:
    """One playbook file, ready to run. `stem` is the key a campaign stores
    and the API speaks; `filename` is what a load error names."""

    name: str
    stem: str
    filename: str
    description: str
    params: tuple[ParamSpec, ...]
    _next_step: object = field(repr=False)
    _chain_windows: object = field(default=None, repr=False)

    def next_step(self, ctx):
        return self._next_step(ctx)  # type: ignore[operator]

    def chain_windows(self, params: dict) -> list[tuple[int, int]]:
        """The DTE windows whose chains the script reads, (min, max) each:
        the script's own `chain_windows(params)` when it defines one, else
        its min_dte..max_dte (21..45 when it has neither)."""
        if self._chain_windows is not None:
            out = [(int(lo), int(hi)) for lo, hi in self._chain_windows(params)]  # type: ignore[operator]
            if out:
                return out
        return [(int(params.get("min_dte", 21)), int(params.get("max_dte", 45)))]

    def resolve_params(self, given: dict | None) -> dict:
        """Defaults filled in, types coerced, bounds checked; an unknown key
        is an error rather than silently ignored -- a typo in a parameter
        name must not read as "the default applied"."""
        given = dict(given or {})
        known = {p.name for p in self.params}
        unknown = sorted(set(given) - known)
        if unknown:
            raise ValueError(f"unknown parameter{'s' if len(unknown) > 1 else ''}: {', '.join(unknown)}")
        out: dict = {}
        for spec in self.params:
            out[spec.name] = spec.coerce(given[spec.name]) if spec.name in given and given[spec.name] is not None else spec.default
        return out

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "stem": self.stem,
            "filename": self.filename,
            "description": self.description,
            "params": [p.to_dict() for p in self.params],
        }


@dataclass(frozen=True)
class LoadError:
    filename: str
    error: str


def _load_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"_playbook_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # fresh module object every call -- see module docstring
    return module


def _validate(module: ModuleType, path: Path) -> LoadedPlaybook:
    name = getattr(module, "NAME", None)
    if not isinstance(name, str) or not name.strip():
        raise TypeError("NAME must be a non-blank string.")
    description = getattr(module, "DESCRIPTION", "")
    if not isinstance(description, str):
        raise TypeError("DESCRIPTION must be a string.")
    params = getattr(module, "PARAMS", [])
    if not isinstance(params, (list, tuple)) or not all(isinstance(p, ParamSpec) for p in params):
        raise TypeError("PARAMS must be a list of ParamSpec.")
    names = [p.name for p in params]
    if len(set(names)) != len(names):
        raise ValueError("PARAMS names must be unique.")
    next_step = getattr(module, "next_step", None)
    if not callable(next_step):
        raise TypeError("next_step(ctx) is missing or not callable.")
    chain_windows = getattr(module, "chain_windows", None)
    if chain_windows is not None and not callable(chain_windows):
        raise TypeError("chain_windows(params), when present, must be callable.")
    return LoadedPlaybook(
        name=name, stem=path.stem, filename=path.name, description=description, params=tuple(params), _next_step=next_step,
        _chain_windows=chain_windows,
    )


def load_playbooks(only: str | None = None) -> tuple[list[LoadedPlaybook], list[LoadError]]:
    """Every enabled playbook in this directory, plus whatever failed to
    load. `only` selects one by NAME or file stem; a name that matches
    nothing yields an empty list, which the caller reports."""
    playbooks: list[LoadedPlaybook] = []
    errors: list[LoadError] = []
    for path in sorted(_DIR.glob("*.py")):
        if path.name in _EXCLUDED:
            continue
        try:
            module = _load_module(path)
            if not getattr(module, "ENABLED", True):
                continue
            playbook = _validate(module, path)
        except Exception as exc:
            logger.exception("Playbook %s failed to load -- skipping", path.name)
            errors.append(LoadError(filename=path.name, error=f"{type(exc).__name__}: {exc}"))
            continue
        if only is not None and only not in (playbook.name, path.stem):
            continue
        playbooks.append(playbook)
    return playbooks, errors


def get_playbook(stem: str) -> LoadedPlaybook | None:
    found, _errors = load_playbooks(only=stem)
    return found[0] if found else None
