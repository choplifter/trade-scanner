"""The runtime on/off switch for each strategy, persisted across restarts.

ENABLED = False in a strategy file *parks* it: invisible everywhere, the
equivalent of deleting the file without losing it. This is the other switch
-- the one flipped from the dashboard while the market is open, without
touching a file. A switched-off strategy still exists (the UI lists it, a
backtest can still name it), it just stops producing signals.

Stored as a JSON map of filename stem -> bool in the backend working
directory, next to scanner_history.sqlite3. Only the switched-off entries
matter: a strategy absent from the file is on, so a freshly dropped-in
strategy file starts live -- the drop-in-and-it-works behaviour the loader
exists for -- and deleting the JSON file turns everything back on.

Re-read on every load_strategies call rather than cached, the same cadence
the strategy files themselves are re-executed at. The file is a few dozen
bytes; correctness of "I just switched that off" beats a cache.

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


def switched_off() -> set[str]:
    """The filename stems currently switched off. Missing file means none.

    A corrupt file also means none -- everything on. Failing toward "on" is
    deliberate: the default state of every strategy is on, and a strategy
    silently off looks exactly like a quiet market, which is the failure
    mode this package is shaped around avoiding.
    """
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return set()
    except (OSError, json.JSONDecodeError):
        logger.exception("Could not read %s -- treating every strategy as on", _PATH)
        return set()
    if not isinstance(data, dict):
        logger.warning("%s is not a JSON object -- treating every strategy as on", _PATH)
        return set()
    return {stem for stem, on in data.items() if not on}


def set_switched(stem: str, enabled: bool) -> None:
    """Flip one strategy's switch and persist it."""
    offs = switched_off()
    if enabled:
        offs.discard(stem)
    else:
        offs.add(stem)
    _PATH.write_text(
        json.dumps({stem: False for stem in sorted(offs)}, indent=2) + "\n",
        encoding="utf-8",
    )
