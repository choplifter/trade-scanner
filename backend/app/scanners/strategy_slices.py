"""Cutting a strategy's signals into slices -- the question the aggregate
cannot answer.

A rule with an expectancy of zero over three hundred signals is usually not
one rule: it is a rule that works in the first half hour and gives it back
after lunch, or one that pays in a quiet tape and bleeds in a fearful one.
The whole-sample figure hides both.

Pure: it takes the picks app.scanners.strategy_backtest already produces
(one per signal, each with its R multiple) and the market's own daily
closes, and returns the same expectancy numbers per bucket. Nothing here
fetches anything.

Every bucket carries its own sample size, and MIN_SAMPLE_SIZE is the floor
below which a number is noise with a decimal point. The caller shows it;
this module does not hide small buckets, because "no signals at all in the
last hour" is itself an answer.
"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from app.scanners.bucket_analysis import MIN_SAMPLE_SIZE

ET = ZoneInfo("America/New_York")

# The session in four parts, by what trades differently in each: the
# opening auction's imbalance, the morning trend, the midday lull, and the
# close. Boundaries are the conventional ones, not fitted to the data --
# a boundary chosen to make a result look better is not a finding.
SESSION_PARTS: tuple[tuple[str, time, time], ...] = (
    ("09:30-10:00 open", time(9, 30), time(10, 0)),
    ("10:00-12:00 morning", time(10, 0), time(12, 0)),
    ("12:00-14:00 midday", time(12, 0), time(14, 0)),
    ("14:00-16:00 close", time(14, 0), time(16, 0)),
)

# VIX bands: the same reading the app's market-conditions light uses, so a
# "calm" here means what "calm" means everywhere else in the dashboard.
VIX_BANDS: tuple[tuple[str, float, float], ...] = (
    ("VIX < 16 calm", 0.0, 16.0),
    ("VIX 16-20", 16.0, 20.0),
    ("VIX 20-25 elevated", 20.0, 25.0),
    ("VIX 25+ fearful", 25.0, 1e9),
)


def slice_stats(picks: list[dict]) -> dict:
    """Expectancy in R for one bucket of picks, with what it rests on.

    Expectancy first and win rate second, deliberately: see
    exit_rules.expectancy, whose numbers these mirror for picks that carry
    their R multiple as a plain field.
    """
    rs = [p["r_multiple"] for p in picks if p.get("r_multiple") is not None]
    if not rs:
        return {"trades": 0, "expectancy_r": None, "win_rate": None, "avg_win_r": None, "avg_loss_r": None, "ambiguous_pct": None}
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    ambiguous = sum(1 for p in picks if p.get("ambiguous_exit"))
    return {
        "trades": len(rs),
        "expectancy_r": round(sum(rs) / len(rs), 3),
        "win_rate": round(len(wins) / len(rs) * 100, 1),
        "avg_win_r": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss_r": round(sum(losses) / len(losses), 2) if losses else None,
        "ambiguous_pct": round(ambiguous / len(picks) * 100, 1),
    }


def _entry(pick: dict) -> datetime:
    return datetime.fromisoformat(pick["timestamp"]).astimezone(ET)


def by_session_part(picks: list[dict]) -> list[dict]:
    """Every part of the session, in order, including the ones with no
    signals -- "this rule never fires after lunch" is worth seeing."""
    out = []
    for label, start, end in SESSION_PARTS:
        inside = [p for p in picks if start <= _entry(p).time() < end]
        out.append({"bucket": label, **slice_stats(inside)})
    # Anything outside the regular session (a strategy that fires premarket).
    outside = [p for p in picks if not any(s <= _entry(p).time() < e for _l, s, e in SESSION_PARTS)]
    if outside:
        out.append({"bucket": "outside the session", **slice_stats(outside)})
    return out


def by_exit_reason(picks: list[dict]) -> list[dict]:
    """How the trades ended. A rule whose wins are all session-close exits
    is a rule whose target is never reached, whatever its expectancy."""
    reasons = sorted({p.get("exit_reason", "unknown") for p in picks})
    return [{"bucket": r, **slice_stats([p for p in picks if p.get("exit_reason", "unknown") == r])} for r in reasons]


def by_vix_band(picks: list[dict], vix_by_day: dict) -> list[dict]:
    """Signals grouped by the VIX close of the day they fired on. Picks from
    a day the VIX is not known for are gathered separately rather than
    dropped, so the bucket sizes still add up to the whole sample."""
    banded: dict[str, list[dict]] = {label: [] for label, _lo, _hi in VIX_BANDS}
    unknown: list[dict] = []
    for pick in picks:
        vix = vix_by_day.get(_entry(pick).date())
        if vix is None:
            unknown.append(pick)
            continue
        for label, lo, hi in VIX_BANDS:
            if lo <= vix < hi:
                banded[label].append(pick)
                break
    out = [{"bucket": label, **slice_stats(banded[label])} for label, _lo, _hi in VIX_BANDS]
    if unknown:
        out.append({"bucket": "VIX not known", **slice_stats(unknown)})
    return out


def thin(bucket: dict, floor: int = MIN_SAMPLE_SIZE) -> bool:
    """Whether a bucket's number should be read as noise. Kept here so the
    page and any report agree on where that line is."""
    return bucket.get("trades", 0) < floor
