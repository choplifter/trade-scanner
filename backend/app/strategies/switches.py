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
import time
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


# A process-local freeze of the switch state, set by pin_current(). None in
# the live app, which must see a toggle on its next poll tick.
_pinned: dict | None = None


def pin_current() -> None:
    """Freeze the switch state for the rest of this process.

    For backtests, which read some switches per *evaluation*: a report has
    to describe one version of a rule (see loader), and without this a
    toggle clicked in the dashboard mid-run changes the rule mid-walk --
    observed as a 15-minute-range run finishing with the 5-minute
    population after a click in the Signals panel.
    """
    global _pinned
    _pinned = _read()


# Windows file sharing is the reason for every retry below: python opens
# files without FILE_SHARE_DELETE, so while any process has this file open
# -- the engine's poll tick, a backtest, a virus scanner -- a concurrent
# replace fails with PermissionError, and opens against a delete-pending
# file fail the same way. The locks last milliseconds; a few short retries
# outlast them. Observed live as a 500 on the settings POST and as the
# panel flipping to defaults mid-session.
_IO_RETRIES = 8
_IO_RETRY_DELAY_SECONDS = 0.015


def _read(strict: bool = False) -> dict:
    """The whole switch file, or {} when missing or unreadable.

    Failing toward {} -- every default -- is deliberate for *readers*: the
    default state of every strategy is on, and a strategy silently off
    looks exactly like a quiet market. Writers pass strict=True and get
    the exception instead: a setter that reads defaults and writes them
    back with one change wipes every other switch the user has set, which
    is worse than the request failing.
    """
    if _pinned is not None:
        return dict(_pinned)
    for attempt in range(_IO_RETRIES):
        try:
            raw = _PATH.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except PermissionError:
            if attempt + 1 < _IO_RETRIES:
                time.sleep(_IO_RETRY_DELAY_SECONDS)
                continue
            if strict:
                raise
            logger.warning("Could not read %s (still locked) -- using defaults", _PATH)
            return {}
        except OSError as exc:
            if strict:
                raise
            logger.warning("Could not read %s (%s) -- using defaults", _PATH, exc)
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            if strict:
                raise
            logger.warning("%s is not valid JSON (%s) -- using defaults", _PATH, exc)
            return {}
        if not isinstance(data, dict):
            if strict:
                raise ValueError(f"{_PATH} is not a JSON object")
            logger.warning("%s is not a JSON object -- using defaults", _PATH)
            return {}
        return data
    return {}


def _write(data: dict) -> None:
    # Write-then-rename rather than write_text, which truncates in place: the
    # running app and a backtest process read this file concurrently, and one
    # of them was observed catching it empty mid-write (JSONDecodeError at
    # char 0, falling back to defaults). os.replace is atomic on the same
    # volume, so a reader now sees the old content or the new, never neither.
    #
    # The replace is retried because of the Windows sharing rules described
    # at _IO_RETRIES; the last resort is the plain truncating write --
    # re-accepting the tiny empty-read race for one write beats returning a
    # 500 for a toggle.
    payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
    tmp = _PATH.with_name(_PATH.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    for attempt in range(_IO_RETRIES):
        try:
            tmp.replace(_PATH)
            return
        except PermissionError:
            if attempt + 1 < _IO_RETRIES:
                time.sleep(_IO_RETRY_DELAY_SECONDS)
    logger.warning("Replace of %s still locked after retries -- writing in place", _PATH)
    _PATH.write_text(payload, encoding="utf-8")


def switched_off() -> set[str]:
    """The filename stems currently switched off. Missing file means none."""
    return {
        stem for stem, on in _read().items() if not stem.startswith("_") and not on
    }


def set_switched(stem: str, enabled: bool) -> None:
    """Flip one strategy's switch and persist it, leaving settings intact.

    Reads strictly: writing defaults-plus-one-change over a file that was
    merely locked for a moment would wipe every other switch."""
    data = _read(strict=True)
    if enabled:
        data.pop(stem, None)
    else:
        data[stem] = False
    _write(data)


OPENING_RANGE_KEY = "_opening_range_minutes"
# Five is Aziz's definition and this app's default; fifteen is the common
# alternative for names whose first minutes are too thin to make a box worth
# trading (the SDOT shape: 1,685 shares in the first five minutes). A choice
# of two rather than a free integer: every listed value is one that has been
# (or is about to be) measured, and an arbitrary minute count would be a
# parameter to overfit.
OPENING_RANGE_CHOICES = (5, 15)


def opening_range_minutes() -> int:
    """How long the opening range is, in minutes -- read by every ORB-family
    rule at evaluate time and by the chart's Opening Range box, so the box
    drawn is always the box traded."""
    value = _read().get(OPENING_RANGE_KEY, OPENING_RANGE_CHOICES[0])
    return value if value in OPENING_RANGE_CHOICES else OPENING_RANGE_CHOICES[0]


def set_opening_range_minutes(minutes: int) -> None:
    if minutes not in OPENING_RANGE_CHOICES:
        raise ValueError(f"opening range must be one of {OPENING_RANGE_CHOICES}, not {minutes!r}")
    data = _read(strict=True)
    data[OPENING_RANGE_KEY] = minutes
    _write(data)


def measured_move_target_enabled() -> bool:
    """Whether the break rules may fall back to a constructed 2R target when
    no level lies ahead of the entry. See breakout.signal_for for what that
    changes and what it deliberately does not."""
    return bool(_read().get(MEASURED_MOVE_KEY, True))


def set_measured_move_target(enabled: bool) -> None:
    data = _read(strict=True)
    data[MEASURED_MOVE_KEY] = bool(enabled)
    _write(data)
