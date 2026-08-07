"""In-memory log of past AI-generated trade ideas, so the dashboard can show
how the ranking's picks actually moved afterward -- the only real check on
whether the selection heuristic (gap/rvol/catalyst/VWAP/momentum/spread) is
doing anything, versus just producing plausible-sounding reasoning.

Deliberately not persisted: this is a lightweight self-check for a personal
dashboard, not a trading journal, and a fresh set of picks each restart is
fine. "Current price" for a tracked symbol is read off the scanner engine's
already-polled live rows rather than a fresh API call, so checking
performance costs nothing extra.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class TrackedIdea:
    symbol: str
    headline: str
    entry_price: float
    generated_at: datetime


class TradeIdeaTracker:
    _MAX_ENTRIES = 200

    def __init__(self) -> None:
        self._entries: list[TrackedIdea] = []

    def record(self, ideas: list[TrackedIdea]) -> None:
        self._entries.extend(ideas)
        if len(self._entries) > self._MAX_ENTRIES:
            self._entries = self._entries[-self._MAX_ENTRIES :]

    def all(self) -> list[TrackedIdea]:
        return list(self._entries)
