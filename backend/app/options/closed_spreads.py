"""Closed option spreads: the per-contract round trips the trade record
already keeps (app.trading.trades -- each OCC contract its own trip, an
expiry or assignment closing it at 0.00) put back together into the
structures they were traded as.

Legs belong to one spread when they were opened on the same underlying in
the same second -- a multi-leg order fills all its legs at once, and two
separate orders a second apart are rare enough to accept the merge. A
condor closed one side at a time stays one spread: its close time is the
last leg's, and its outcome lists both ways it ended ("closed + expired").

How a leg ended comes from the exit order ids: the simulated book names its
settlements "expiry:..." / "assign:...", and at Alpaca the id is the
account activity's (OPEXP / OPASN / OPEXC), looked up in `activity_kinds`.
An exit at 0.00 with no such id is reported as expired -- a short leg that
was assigned leaves shares behind, which the record shows as its own trip.

Pure: trade rows in, spreads out. The routers fetch.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from app.options.occ import try_parse_occ
from app.services.market_clock import ET

ACTIVITY_OUTCOMES = {"OPEXP": "expired", "OPASN": "assigned", "OPEXC": "exercised"}


def _when(raw) -> datetime | None:
    if isinstance(raw, datetime):
        return raw
    try:
        return datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None


def leg_outcome(row: dict, activity_kinds: dict[str, str]) -> str:
    """closed | expired | assigned | exercised, from the leg's exit ids."""
    for oid in row.get("exit_order_ids") or []:
        oid = str(oid)
        if oid.startswith("assign:"):
            return "assigned"
        if oid.startswith(("expiry:", "lapsed:")):
            return "expired"
        kind = activity_kinds.get(oid)
        if kind in ACTIVITY_OUTCOMES:
            return ACTIVITY_OUTCOMES[kind]
    if float(row.get("exit_avg") or 0) == 0.0:
        return "expired"
    return "closed"


def _strategy(legs: list[dict]) -> str:
    """The structure's name from its legs, the ticket's vocabulary where it
    fits; "custom" for anything else."""
    from app.options.barchart_screener import Leg, classify

    if len(legs) == 1:
        leg = legs[0]
        long = leg["side"] == "long"
        if leg["kind"] == "call":
            return "long_call" if long else "short_call"
        return "long_put" if long else "short_put"
    shaped = [Leg(leg["strike"], leg["kind"], "buy" if leg["side"] == "long" else "sell", 0.0) for leg in legs]
    return classify(shaped) or "custom"


def group_spreads(rows: list[dict], activity_kinds: dict[str, str] | None = None) -> list[dict]:
    """Option round trips grouped into closed spreads, newest close first.
    Stock trips and rows that are not option contracts are left out."""
    activity_kinds = activity_kinds or {}
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        parsed = try_parse_occ(str(row.get("symbol") or ""))
        opened = _when(row.get("opened_at"))
        if parsed is None or opened is None or int(row.get("multiplier") or 1) != 100:
            continue
        key = (parsed.underlying, opened.astimezone(ET).replace(microsecond=0).isoformat())
        groups[key].append({**row, "_occ": parsed, "_opened": opened, "_closed": _when(row.get("closed_at")) or opened})

    out = []
    for (underlying, _), members in groups.items():
        unit = min(float(m["qty"]) for m in members) or 1.0
        legs = []
        for m in sorted(members, key=lambda r: (r["_occ"].kind, r["_occ"].strike)):
            ratio = float(m["qty"]) / unit
            sign = 1 if m["side"] == "long" else -1
            legs.append({
                "trade_id": m["id"],
                "symbol": m["symbol"],
                "kind": m["_occ"].kind,
                "strike": m["_occ"].strike,
                "expiry": m["_occ"].expiry.isoformat(),
                "side": m["side"],
                "qty": float(m["qty"]),
                "entry": float(m["entry_avg"]),
                "exit": float(m["exit_avg"]),
                "pnl": round(float(m["pnl"]), 2),
                "outcome": leg_outcome(m, activity_kinds),
                "closed_at": m["_closed"].isoformat(),
                "_signed_entry": sign * ratio * float(m["entry_avg"]),
                "_signed_exit": sign * ratio * float(m["exit_avg"]),
            })
        outcomes = sorted({leg["outcome"] for leg in legs})
        pnl = round(sum(leg["pnl"] for leg in legs), 2)
        entry_net = sum(leg.pop("_signed_entry") for leg in legs)
        exit_net = sum(leg.pop("_signed_exit") for leg in legs)
        opened_at = min(m["_opened"] for m in members)
        closed_at = max(m["_closed"] for m in members)
        out.append({
            # Stable across syncs: the first leg's own trip id. The journal
            # keys its notes by it.
            "id": "spread:" + sorted(leg["trade_id"] for leg in legs)[0],
            "underlying": underlying,
            "strategy": _strategy(legs),
            "expiry": min(leg["expiry"] for leg in legs),
            "qty": unit,
            "opened_at": opened_at.isoformat(),
            "closed_at": closed_at.isoformat(),
            # Per share, signed like a ticket: positive was paid (debit),
            # negative received (credit). The exit the same way, as held.
            "entry_net": round(entry_net, 4),
            "exit_net": round(exit_net, 4),
            "pnl": pnl,
            "outcome": " + ".join(outcomes),
            "held_days": round((closed_at - opened_at) / timedelta(days=1), 2),
            "legs": legs,
        })
    out.sort(key=lambda s: s["closed_at"], reverse=True)
    return out


def _day(iso: str) -> date:
    return datetime.fromisoformat(iso).astimezone(ET).date()


def summarize_spreads(spreads: list[dict]) -> dict:
    """Totals, and the per-day and per-week subtotals by close date (ET);
    weeks start on Monday."""
    pnls = [s["pnl"] for s in spreads]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    days: dict[date, list[float]] = defaultdict(list)
    weeks: dict[date, list[float]] = defaultdict(list)
    for s in spreads:
        d = _day(s["closed_at"])
        days[d].append(s["pnl"])
        weeks[d - timedelta(days=d.weekday())].append(s["pnl"])

    def rows(buckets: dict[date, list[float]], key: str) -> list[dict]:
        return [
            {key: k.isoformat(), "count": len(v), "wins": sum(p > 0 for p in v), "pnl": round(sum(v), 2)}
            for k, v in sorted(buckets.items(), reverse=True)
        ]

    return {
        "summary": {
            "count": len(spreads),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(spreads), 4) if spreads else None,
            "pnl": round(sum(pnls), 2),
            "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
            "best": max(pnls) if pnls else None,
            "worst": min(pnls) if pnls else None,
        },
        "days": rows(days, "date"),
        "weeks": rows(weeks, "week_start"),
    }


def in_range(spreads: list[dict], start: datetime | None) -> list[dict]:
    """The spreads closed on or after `start` (None: all)."""
    if start is None:
        return spreads
    return [s for s in spreads if datetime.fromisoformat(s["closed_at"]) >= start]
