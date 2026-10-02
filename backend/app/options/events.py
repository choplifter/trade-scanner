"""What is scheduled inside an option's life, and what it did to the stock
before: the next earnings report with the stock's moves over its past
reports, the tracked macro releases, and where today's at-the-money IV
sits in its own history and against the stock's realised vol.

The strip and the Optimizer read this to say "this expiry is held through
earnings", "the market prices ±4 % to this expiry, the stock moved ±6 %
on average over its last eight reports", "IV rank 78 %". All of it is
descriptive; none of it decides anything. Every block is nullable and
says why it is null, because "no report known" and "FMP not configured"
are different statements and the whole codebase keeps them apart.

The pure parts (moves from closes, the summary) are here; the I/O is
`gather_events`, which takes the app's calendars and stores as arguments
so a test can hand it fakes.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timezone

from app.services.market_clock import ET

from app.options.iv_context import iv_premium, realized_vol
from app.options.iv_history_store import COMPARABLE_DTE

logger = logging.getLogger(__name__)

# Reports to measure: two years of quarterlies.
EARNINGS_HISTORY = 8
# Calendar days of daily bars that cover them, with slack for a moved date.
BARS_LOOKBACK_DAYS = 2 * 365 + 60


@dataclass(frozen=True)
class EarningsMove:
    report_date: date
    close_before: float
    close_after: float

    @property
    def move_pct(self) -> float:
        return (self.close_after / self.close_before - 1.0) * 100.0

    def to_dict(self) -> dict:
        return {
            "report_date": self.report_date.isoformat(),
            "close_before": round(self.close_before, 4),
            "close_after": round(self.close_after, 4),
            "move_pct": round(self.move_pct, 2),
        }


def closes_by_day(bars) -> dict[date, float]:
    """Daily bars (anything with .timestamp and .close) keyed by their ET
    session date."""
    out: dict[date, float] = {}
    for bar in bars or []:
        ts = getattr(bar, "timestamp", None)
        close = getattr(bar, "close", None)
        if ts is None or close is None:
            continue
        if isinstance(ts, datetime):
            day = (ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)).astimezone(ET).date()
        else:
            day = ts
        out[day] = float(close)
    return out


def earnings_moves(report_dates, closes: dict[date, float], *, today: date, limit: int = EARNINGS_HISTORY) -> list[EarningsMove]:
    """The stock's close-to-close move across each past report: the last
    close before the report date to the first close after it.

    Two sessions on purpose. FMP's before/after-market field is unreliable
    filler on many symbols, and a report before the open moves the stock
    on the report date while one after the close moves it the next day;
    the window from the close before to the close after covers both, at
    the cost of one ordinary session's drift. Only reports whose both
    closes exist are measured, newest first, at most `limit`."""
    if not closes:
        return []
    days = sorted(closes)
    moves: list[EarningsMove] = []
    for report in sorted((d for d in report_dates if d < today), reverse=True):
        before = next((d for d in reversed(days) if d < report), None)
        after = next((d for d in days if d > report), None)
        if before is None or after is None:
            continue
        moves.append(EarningsMove(report_date=report, close_before=closes[before], close_after=closes[after]))
        if len(moves) >= limit:
            break
    return moves


def summarize_moves(moves: list[EarningsMove]) -> dict:
    """Typical size of the move, sign ignored: the median (what "usually"
    means) and the mean (what a straddle seller pays for), plus the largest,
    so one blow-up is visible next to a quiet median."""
    if not moves:
        return {"samples": 0, "median_abs_pct": None, "mean_abs_pct": None, "max_abs_pct": None}
    sizes = [abs(m.move_pct) for m in moves]
    return {
        "samples": len(sizes),
        "median_abs_pct": round(statistics.median(sizes), 2),
        "mean_abs_pct": round(statistics.fmean(sizes), 2),
        "max_abs_pct": round(max(sizes), 2),
    }


async def _daily_bars(clients, underlying: str) -> tuple[list | None, str | None]:
    """The daily bars both the earnings history and the realised vol read,
    fetched once: (bars, None), or (None, why not)."""
    if clients is None:
        return None, "no market data client for the daily bars"
    try:
        from app.market_data.bars import get_daily_bars_multi

        bars = await get_daily_bars_multi(clients, [underlying], lookback_days=BARS_LOOKBACK_DAYS)
    except Exception:
        logger.exception("Daily bars failed for %s", underlying)
        return None, "daily bars unavailable"
    return bars.get(underlying, []), None


async def _earnings_block(earnings_calendar, bars: list | None, bars_note: str | None, underlying: str, today: date) -> dict | None:
    if earnings_calendar is None:
        return None
    try:
        dates = await earnings_calendar.report_dates(underlying)
    except Exception:
        logger.exception("Earnings dates lookup failed for %s", underlying)
        return None
    if not dates:
        return None
    upcoming = [d for d in dates if d >= today]
    block: dict = {
        "report_date": upcoming[0].isoformat() if upcoming else None,
        "days_until": (upcoming[0] - today).days if upcoming else None,
        "history": [],
        **summarize_moves([]),
        "history_note": None,
    }
    past = [d for d in dates if d < today]
    if not past:
        block["history_note"] = "no past report dates known"
        return block
    if bars is None:
        block["history_note"] = bars_note
        return block
    moves = earnings_moves(past, closes_by_day(bars), today=today)
    block["history"] = [m.to_dict() for m in moves]
    block.update(summarize_moves(moves))
    if not moves:
        block["history_note"] = "no daily closes around the past reports"
        if not upcoming:
            # FMP lists report rows for some ETFs, years old and unmeasurable:
            # nothing upcoming and nothing measured is nothing to say.
            return None
    return block


async def _macro_block(macro_calendar) -> list[dict]:
    if macro_calendar is None:
        return []
    try:
        return [e.to_dict() for e in await macro_calendar.upcoming()]
    except Exception:
        logger.exception("Macro calendar lookup failed")
        return []


async def _iv_rank_block(iv_store, underlying: str, atm_iv: float | None, dte: int | None, today: date) -> dict:
    """The rank, and -- when the caller says which expiry the IV belongs to
    -- today's reading recorded, so the history the rank needs builds up
    from every chain looked at, not only from Idea-tab requests. One row
    per symbol and session (the store's key), so repeats are harmless."""
    block: dict = {"atm_iv": None if atm_iv is None else round(atm_iv, 4), "rank": None, "samples": 0}
    if iv_store is None or atm_iv is None:
        return block
    try:
        # A reading from the wrong stretch of the curve must not *join* the
        # history -- a 1-DTE chain priced 178 % on the day MU reported,
        # against 57 % at 51 days -- but it can still be placed against it:
        # a caller asking "where does this number sit" gets an answer, and
        # one that does not say which expiry it read gets the benefit of
        # the doubt. Only the write is guarded.
        comparable = dte is not None and COMPARABLE_DTE[0] <= dte <= COMPARABLE_DTE[1]
        if dte is not None and not comparable:
            return block
        rank, samples = await iv_store.rank(underlying, atm_iv)
        if comparable and hasattr(iv_store, "record"):
            await iv_store.record(underlying, today, atm_iv, dte)
    except Exception:
        logger.exception("IV rank lookup failed for %s", underlying)
        return block
    block["rank"] = None if rank is None else rank.to_dict()
    block["samples"] = samples
    return block


def _realized_block(bars: list | None, atm_iv: float | None) -> dict:
    """Is the IV high right now, answered without any stored history: the
    chain's ATM IV over the stock's 20-session realised vol. Unlike the
    rank this exists from the first day, and for any expiry -- it says what
    *this* chain charges against what the stock has been moving."""
    realized = realized_vol([close for _, close in sorted(closes_by_day(bars).items())]) if bars else None
    premium = iv_premium(atm_iv, realized)
    return {
        "realized_vol_20d": None if realized is None else round(realized, 4),
        "iv_over_realized": None if premium is None else round(premium, 2),
    }


async def gather_events(
    underlying: str,
    *,
    earnings_calendar=None,
    macro_calendar=None,
    iv_store=None,
    clients=None,
    atm_iv: float | None = None,
    dte: int | None = None,
    today: date | None = None,
) -> dict:
    """The events payload for one underlying. `atm_iv` is the chain's
    current at-the-money IV as the caller sees it (the frontend has the
    chain on screen; asking the broker for it again here would be a second
    chain fetch for one number), `dte` the days to that chain's expiry --
    given, today's reading is recorded for the rank's history."""
    underlying = underlying.upper()
    today = today or datetime.now(timezone.utc).astimezone(ET).date()
    wants_bars = earnings_calendar is not None or atm_iv is not None
    bars, bars_note = await _daily_bars(clients, underlying) if wants_bars else (None, None)
    earnings = await _earnings_block(earnings_calendar, bars, bars_note, underlying, today)
    macro = await _macro_block(macro_calendar)
    iv = {**await _iv_rank_block(iv_store, underlying, atm_iv, dte, today), **_realized_block(bars, atm_iv)}
    return {
        "underlying": underlying,
        "today": today.isoformat(),
        "earnings": earnings,
        "macro": macro,
        "iv": iv,
        "sources": {
            "earnings": earnings_calendar is not None and bool(getattr(earnings_calendar, "configured", True)),
            "macro": macro_calendar is not None and bool(getattr(macro_calendar, "configured", True)),
            "iv_rank": iv_store is not None,
        },
    }
