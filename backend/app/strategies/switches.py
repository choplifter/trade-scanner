"""The runtime on/off switches for strategy signals, persisted across
restarts.

ENABLED = False in a strategy file *parks* it: invisible everywhere, the
equivalent of deleting the file without losing it. This is the other switch
-- the one flipped from the dashboard while the market is open, without
touching a file. A switched-off strategy still exists (the UI lists it, a
backtest can still name it), it just stops producing signals.

Stored as one JSON object in the backend working directory, next to
scanner_history.sqlite3. Plain keys are strategy filename stems mapped to
bools; only the switched-off entries matter, so a strategy absent from the
file is on -- a freshly dropped-in strategy file starts live (the
drop-in-and-it-works behaviour the loader exists for) and deleting the JSON
file returns everything to defaults.

Keys starting with "_" are reserved for shared signal settings rather than
per-strategy switches -- currently the measured-move target fallback the
break rules read (see strategies.breakout). Reserved by prefix so a future
strategy file cannot collide with a setting by taking its name.

Re-read on every load_strategies call / signal evaluation rather than
cached, the same cadence the strategy files themselves are re-executed at.
The file is a few dozen bytes; correctness of "I just switched that off"
beats a cache.

Keyed by filename stem, not display NAME: the stem is what load_strategies'
`only` already accepts, and it survives a rule being renamed in place.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Relative, like scanner_history_db_path: resolved against the backend
# working directory. Tests point this at a scratch file, same pattern as
# loader._DIR.
_PATH = Path("strategy_switches.json")

# When the break rules find no level ahead of an entry, aim at a constructed
# measured-move target instead of declining the trade (see
# breakout.signal_for). On by default: the user asked for it after watching
# a textbook break go unmarked on a new-high day, which is exactly when no
# level is ahead -- and unlike a strategy switch there is no "quiet market"
# ambiguity in it being on.
MEASURED_MOVE_KEY = "_measured_move_target"


def _read() -> dict:
    """The whole switch file, or {} when missing or unreadable.

    Failing toward {} -- every default -- is deliberate: the default state
    of every strategy is on, and a strategy silently off looks exactly like
    a quiet market, which is the failure mode this package is shaped around
    avoiding.
    """
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        logger.exception("Could not read %s -- using defaults", _PATH)
        return {}
    if not isinstance(data, dict):
        logger.warning("%s is not a JSON object -- using defaults", _PATH)
        return {}
    return data


def _write(data: dict) -> None:
    _PATH.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def switched_off() -> set[str]:
    """The filename stems currently switched off. Missing file means none."""
    return {
        stem for stem, on in _read().items() if not stem.startswith("_") and not on
    }


def set_switched(stem: str, enabled: bool) -> None:
    """Flip one strategy's switch and persist it, leaving settings intact."""
    data = _read()
    if enabled:
        data.pop(stem, None)
    else:
        data[stem] = False
    _write(data)


def measured_move_target_enabled() -> bool:
    """Whether the break rules may fall back to a constructed 2R target when
    no level lies ahead of the entry. See breakout.signal_for for what that
    changes and what it deliberately does not."""
    return bool(_read().get(MEASURED_MOVE_KEY, True))


def set_measured_move_target(enabled: bool) -> None:
    data = _read()
    data[MEASURED_MOVE_KEY] = bool(enabled)
    _write(data)
