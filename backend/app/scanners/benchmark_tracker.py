"""In-memory log of the first time each symbol was flagged by any of the
scanner's ranked views (gainers/losers/most_active/moderate_movers) since
this backend started, so the dashboard can show how the scanner's own
selection criteria (gap %, RVOL, the movers backstop) actually performed
afterward relative to the broad market -- the real test of whether this
tool finds stocks that keep moving, as opposed to
app.ai.trade_idea_tracker.TradeIdeaTracker, which only checks the AI
layer's curation of a handful of already-scanner-selected picks.

Deliberately not persisted -- same rationale as TradeIdeaTracker: a
lightweight self-check for a personal dashboard, not a trading journal.
Only the FIRST appearance per symbol *and view* is kept (record_if_new
no-ops on a repeat): re-flagging on every poll while a stock is still in
the top 50 isn't a new signal, it's the same one.

Keyed per (symbol, view) rather than per symbol, matching how
ScannerHistoryStore already dedupes. Per-symbol keying silently swallowed
any view that overlaps an earlier one: moderate_movers is a subset of
gainers and gainers is recorded first, so every one of its symbols was
already present and the view would have shown nothing at all here while
showing up normally in the persisted history. The cost is that a symbol
flagged by two views now appears once per view -- which is what the "Via"
column was always there to distinguish, and previously it was arbitrary
which view won the race.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class TrackedAppearance:
    symbol: str
    view: str
    entry_price: float
    entry_pct_change: float
    entry_rvol: float
    # None when no live benchmark price was available yet (e.g. app just
    # started, or the symbol was first flagged from closed-market fallback
    # data before any live poll) -- callers should treat that as "no
    # benchmark comparison available for this entry", not zero.
    benchmark_entry_price: float | None
    # Most recent news headline as of the moment this was flagged, if any
    # (same fetch ScannerHistoryStore uses -- see engine.py's
    # _record_new_appearances). None just means no recent headline was
    # found, not that anything's wrong with this entry.
    entry_headline: str | None
    first_seen_at: datetime
    # Dollar volume at the moment it was flagged. Entry-time like everything
    # else here, not live -- this table answers what the scanner picked and
    # how it did, so the inputs must be the ones the decision was made on.
    #
    # Defaulted, and therefore last: the fields above have no defaults, and a
    # defaulted field cannot precede them. None covers entries recorded before
    # this existed, which clears within a poll after a restart.
    entry_dollar_volume: float | None = None


class ScannerBenchmarkTracker:
    # Raised from 300 alongside per-(symbol, view) keying: a symbol in three
    # views now costs three entries where it used to cost one, and there are
    # four tracked views rather than three. Same effective history depth, not
    # a deliberate increase in how far back this reaches.
    _MAX_ENTRIES = 500

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], TrackedAppearance] = {}

    def record_if_new(
        self,
        symbol: str,
        view: str,
        entry_price: float,
        entry_pct_change: float,
        entry_rvol: float,
        benchmark_entry_price: float | None,
        entry_headline: str | None = None,
        entry_dollar_volume: float | None = None,
    ) -> None:
        key = (symbol, view)
        if key in self._entries:
            return
        self._entries[key] = TrackedAppearance(
            symbol=symbol,
            view=view,
            entry_price=entry_price,
            entry_pct_change=entry_pct_change,
            entry_rvol=entry_rvol,
            entry_dollar_volume=entry_dollar_volume,
            benchmark_entry_price=benchmark_entry_price,
            entry_headline=entry_headline,
            first_seen_at=datetime.now(timezone.utc),
        )
        if len(self._entries) > self._MAX_ENTRIES:
            oldest = min(self._entries, key=lambda k: self._entries[k].first_seen_at)
            del self._entries[oldest]

    def all(self) -> list[TrackedAppearance]:
        return list(self._entries.values())


def compute_performance(
    entries: list[TrackedAppearance],
    current_price_for: Callable[[str], float | None],
    current_benchmark_price: float | None,
    current_headline_for: Callable[[str], str | None] | None = None,
) -> list[dict]:
    """Shared by both the FastAPI router and the Dash page (see
    app.dash_app.pages.benchmark) so the math lives in exactly one place --
    takes plain callables/values rather than a ScannerEngine reference to
    avoid a circular import (engine.py already imports this module for
    ScannerBenchmarkTracker itself).

    `current_headline_for` supplies today's headline as it stands *now*,
    reported alongside entry_headline rather than replacing it. The two
    answer different questions and both are needed:

      - entry_headline is a point-in-time snapshot, deliberately frozen. It
        is what scanner_history stores and what the drift report and the
        catalyst backtests read, so backfilling it with news published after
        the flag would inject look-ahead bias into exactly those
        measurements.
      - current_headline is for a human reading a live monitor. Most
        appearances are first flagged premarket, hours before the day's
        story is written -- WETO was flagged at 08:04 ET up 168% with no
        news yet, and picked up a headline mid-session -- so a column
        showing only entry_headline reads as "no news" for symbols that
        visibly do have some, and disagrees with the scanner table beside it.

    Omitting the callable keeps current_headline None, which is what a caller
    with no live news cache should get rather than a misleading blank.
    """
    now = datetime.now(timezone.utc)
    picks = []
    for entry in entries:
        current_price = current_price_for(entry.symbol)
        pct_change_since_entry = (
            round((current_price - entry.entry_price) / entry.entry_price * 100, 2)
            if current_price is not None and entry.entry_price
            else None
        )
        benchmark_pct_change = (
            round(
                (current_benchmark_price - entry.benchmark_entry_price)
                / entry.benchmark_entry_price
                * 100,
                2,
            )
            if current_benchmark_price is not None and entry.benchmark_entry_price
            else None
        )
        alpha = (
            round(pct_change_since_entry - benchmark_pct_change, 2)
            if pct_change_since_entry is not None and benchmark_pct_change is not None
            else None
        )
        picks.append(
            {
                "symbol": entry.symbol,
                "view": entry.view,
                "first_seen_at": entry.first_seen_at.isoformat(),
                "minutes_since": round((now - entry.first_seen_at).total_seconds() / 60, 1),
                "entry_price": round(entry.entry_price, 2),
                "entry_pct_change": round(entry.entry_pct_change, 2),
                "entry_rvol": round(entry.entry_rvol, 2),
                "entry_dollar_volume": entry.entry_dollar_volume,
                "entry_headline": entry.entry_headline,
                "current_headline": (
                    current_headline_for(entry.symbol) if current_headline_for else None
                ),
                "current_price": round(current_price, 2) if current_price is not None else None,
                "pct_change_since_entry": pct_change_since_entry,
                "benchmark_pct_change_since_entry": benchmark_pct_change,
                "alpha_vs_benchmark": alpha,
            }
        )
    picks.sort(key=lambda p: p["first_seen_at"], reverse=True)
    return picks
