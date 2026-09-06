"""A campaign's state, rebuilt from the books rather than stored: the shares
held, the short legs open with their marks, and the running numbers -- the
premiums collected, the cost basis, the realized P&L -- summed from the
events the runner recorded. Pure, so the runner and the backtest share it
and a test can feed it dicts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from app.options.occ import try_parse_occ
from app.playbooks.context import Event, OpenLeg

OPTION_EVENTS = {"sold_put", "sold_call", "closed", "rolled", "expired", "cash_settled", "assigned", "called_away"}


@dataclass(frozen=True)
class CampaignSnapshot:
    shares: int
    shares_avg_entry: float | None
    open_legs: tuple[OpenLeg, ...]
    premiums_collected: float
    realized_pnl: float
    cost_basis: float | None
    phase: str

    def to_dict(self) -> dict:
        return {
            "shares": self.shares,
            "shares_avg_entry": self.shares_avg_entry,
            "open_legs": [
                {
                    "occ": l.occ, "kind": l.kind, "strike": l.strike, "expiry": l.expiry.isoformat(), "dte": l.dte, "qty": l.qty,
                    "entry_credit": l.entry_credit, "mark": l.mark, "profit_pct": l.profit_pct, "delta": l.delta,
                }
                for l in self.open_legs
            ],
            "premiums_collected": round(self.premiums_collected, 2),
            "realized_pnl": round(self.realized_pnl, 2),
            "cost_basis": round(self.cost_basis, 4) if self.cost_basis is not None else None,
            "phase": self.phase,
        }


def _num(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def open_legs_from_marks(symbol: str, marks: list[dict], today: date) -> tuple[OpenLeg, ...]:
    """The campaign's short legs from positions in Alpaca's shape (signed
    qty as a string, money per share)."""
    legs: list[OpenLeg] = []
    for m in marks:
        occ = try_parse_occ(str(m.get("symbol", "")))
        if occ is None or occ.underlying != symbol.upper():
            continue
        qty = int(round(_num(m.get("qty")) or 0))
        if qty >= 0:
            continue
        entry = _num(m.get("avg_entry_price")) or 0.0
        mark = _num(m.get("current_price"))
        profit = ((entry - mark) / entry * 100.0) if mark is not None and entry > 0 else None
        legs.append(
            OpenLeg(
                occ=occ.symbol,
                kind=occ.kind,
                strike=float(occ.strike),
                expiry=occ.expiry,
                dte=(occ.expiry - today).days,
                qty=-qty,
                entry_credit=entry,
                mark=mark,
                profit_pct=profit,
                delta=None,
            )
        )
    legs.sort(key=lambda l: (l.expiry, l.strike))
    return tuple(legs)


def events_from_rows(rows: list[dict]) -> tuple[Event, ...]:
    out: list[Event] = []
    for r in rows:
        try:
            at = datetime.fromisoformat(str(r["at"]))
        except (KeyError, ValueError):
            continue
        out.append(
            Event(
                at=at, kind=str(r.get("kind")), occ=r.get("occ"), qty=int(r.get("qty") or 0), price=_num(r.get("price")),
                cash_delta=_num(r.get("cash_delta")), note=r.get("note"),
            )
        )
    return tuple(out)


def realized_share_pnl(events: tuple[Event, ...]) -> float:
    """Shares called away against the lots they were assigned at, first in
    first out: (call strike − put strike) × shares. Shares sold by hand are
    recorded as shares_changed with their price and count the same way."""
    lots: list[list[float]] = []  # [qty, price]
    pnl = 0.0
    for e in events:
        if e.kind == "assigned" and e.price is not None and e.qty > 0:
            lots.append([float(e.qty), e.price])
        elif e.kind in ("called_away", "shares_changed") and e.price is not None and e.qty < 0:
            remaining = float(-e.qty)
            while remaining > 0 and lots:
                lot = lots[0]
                take = min(lot[0], remaining)
                pnl += (e.price - lot[1]) * take
                lot[0] -= take
                remaining -= take
                if lot[0] <= 0:
                    lots.pop(0)
    return pnl


def build_snapshot(
    symbol: str,
    *,
    marks: list[dict],
    share_position: dict | None,
    events: list[dict] | tuple[Event, ...],
    today: date,
) -> CampaignSnapshot:
    evs = events_from_rows(list(events)) if events and isinstance(events[0], dict) else tuple(events)  # type: ignore[arg-type]
    shares = int(round(_num((share_position or {}).get("qty")) or 0)) if share_position else 0
    avg_entry = _num((share_position or {}).get("avg_entry_price")) if share_position else None
    legs = open_legs_from_marks(symbol, marks, today)
    premiums = sum(e.cash_delta or 0.0 for e in evs if e.kind in OPTION_EVENTS)
    realized = premiums + realized_share_pnl(evs)
    basis = None
    if shares > 0 and avg_entry is not None:
        basis = (avg_entry * shares - premiums) / shares
    has_put = any(l.kind == "put" for l in legs)
    has_call = any(l.kind == "call" for l in legs)
    if has_put and has_call:
        phase = "mixed"
    elif has_put:
        phase = "short_put"
    elif has_call:
        phase = "covered_call"
    elif shares >= 100:
        phase = "assigned"
    else:
        phase = "cash"
    return CampaignSnapshot(
        shares=shares,
        shares_avg_entry=avg_entry,
        open_legs=legs,
        premiums_collected=premiums,
        realized_pnl=realized,
        cost_basis=basis,
        phase=phase,
    )
