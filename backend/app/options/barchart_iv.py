"""Barchart's per-symbol "Options Overview History" export, read for the
IV history it carries (Date, Imp Vol, IV Rank, IV Pctl, ...).

Pure: text in, readings out. The import itself is
scripts/import_barchart_iv.py; why a seeded history is acceptable at all
is next to SOURCE_BARCHART in app.options.iv_history_store.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date, timedelta

# The export is named "<symbol>_options-overview-history-<mm-dd-yyyy>.csv".
_FILENAME = re.compile(r"^([a-z0-9.\-]+)_options-overview-history-\d{2}-\d{2}-\d{4}(?: \(\d+\))?\.csv$", re.IGNORECASE)


def symbol_from_filename(name: str) -> str | None:
    match = _FILENAME.match(name)
    return match.group(1).upper() if match else None


def _percent(raw: str) -> float | None:
    try:
        return float(raw.replace("%", "").replace(",", "").strip()) / 100
    except (AttributeError, ValueError):
        return None


def newest_session(text: str) -> date | None:
    """The last session the export covers -- what its window ends on."""
    days = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            days.append(date.fromisoformat((row.get("Date") or "").strip()))
        except ValueError:
            continue
    return max(days) if days else None


def parse_history(text: str, *, as_of: date, days: int = 365) -> list[tuple[date, float]]:
    """(session date, IV as a fraction) for the trailing `days` up to and
    including `as_of`, oldest first. The export runs a little past a year
    (1Y reached back to 19 Sept 2025 on 2 Oct 2026), and a reading from
    outside the 52 weeks Barchart ranks against is exactly what made LQD
    read 34 % against Barchart's 90 %; cut to the window, every symbol but
    SLV matched to a tenth of a point. Skips the trailing "Downloaded
    from" line and any row without a usable IV."""
    start = as_of - timedelta(days=days)
    out: dict[date, float] = {}
    for row in csv.DictReader(io.StringIO(text)):
        raw_date = (row.get("Date") or "").strip()
        try:
            day = date.fromisoformat(raw_date)
        except ValueError:
            continue
        iv = _percent(row.get("Imp Vol", ""))
        if iv is None or iv <= 0 or not start < day <= as_of:
            continue
        out[day] = iv
    return sorted(out.items())
