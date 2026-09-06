"""A campaign's state, rebuilt from the books rather than stored: the shares
held, the legs open with their marks (short legs and, for a poor man's
wheel, the long call standing in for shares), and the running numbers --
the premiums collected, the cost basis, the realized P&L -- summed from
the events the runner recorded. Pure, so the runner and the backtest share
it and a test can feed it dicts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from app.options.occ import try_parse_occ
from app.playbooks.context import Event, OpenLeg

# The short side: what "premiums" means. Credits positive, buybacks negative.
OPTION_EVENTS = {"sold_put", "sold_call", "closed", "rolled", "expired", "cash_settled", "assigned", "called_away"}
# The long side: a long call bought (cash negative) and sold again -- by a
# close, a roll's closing half, or the intrinsic paid out at expiry.
LONG_EVENTS = {"bought_call", "sold_long"}


@dataclass(frozen=True)
class CampaignSnapshot:
    shares: int
    shares_avg_entry: float | None
    open_legs: tuple[OpenLeg, ...]
    premiums_collected: float
    realized_pnl: float
    cost_basis: float | None
    phase: str
    long_legs: tuple[OpenLeg, ...] = ()
    long_pnl: float = 0.0

    def to_dict(self) -> dict:
        return {
            "shares": self.shares,
            "shares_avg_entry": self.shares_avg_entry,
            "open_legs": [
                {
                    "occ": l.occ, "side": l.side, "kind": l.kind, "strike": l.strike, "expiry": l.expiry.isoformat(), "dte": l.dte,
                    "qty": l.qty, "entry_credit": l.entry_credit, "mark": l.mark, "profit_pct": l.profit_pct, "delta": l.delta,
                }
                for l in (*self.open_legs, *self.long_legs)
            ],
            "premiums_collected": round(self.premiums_collected, 2),
            "realized_pnl": round(self.realized_pnl, 2),
            "long_pnl": round(self.long_pnl, 2),
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


def legs_from_marks(symbol: str, marks: list[dict], today: date) -> tuple[tuple[OpenLeg, ...], tuple[OpenLeg, ...]]:
    """The campaign's (short legs, long legs) from positions in Alpaca's
    shape (signed qty as a string, money per share). A short leg's profit
    is the share of the credit earned; a long leg's the gain on the debit."""
    shorts: list[OpenLeg] = []
    longs: list[OpenLeg] = []
    for m in marks:
        occ = try_parse_occ(str(m.get("symbol", "")))
        if occ is None or occ.underlying != symbol.upper():
            continue
        qty = int(round(_num(m.get("qty")) or 0))
        if qty == 0:
            continue
        entry = _num(m.get("avg_entry_price")) or 0.0
        mark = _num(m.get("current_price"))
        delta = _num(m.get("delta"))
        if qty < 0:
            profit = ((entry - mark) / entry * 100.0) if mark is not None and entry > 0 else None
        else:
            profit = ((mark - entry) / entry * 100.0) if mark is not None and entry > 0 else None
        leg = OpenLeg(
            occ=occ.symbol,
            kind=occ.kind,
            strike=float(occ.strike),
            expiry=occ.expiry,
            dte=(occ.expiry - today).days,
            qty=abs(qty),
            entry_credit=entry,
            mark=mark,
            profit_pct=profit,
            delta=delta,
            side="short" if qty < 0 else "long",
        )
        (shorts if qty < 0 else longs).append(leg)

    def key(l: OpenLeg):
        return (l.expiry, l.strike)

    shorts.sort(key=key)
    longs.sort(key=key)
    return tuple(shorts), tuple(longs)


def open_legs_from_marks(symbol: str, marks: list[dict], today: date) -> tuple[OpenLeg, ...]:
    """The campaign's short legs (see legs_from_marks)."""
    return legs_from_marks(symbol, marks, today)[0]


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


def phase_of(*, shares: int, has_put: bool, has_call: bool, has_long_call: bool) -> str:
    if has_put and has_call:
        return "mixed"
    if has_put:
        return "short_put"
    if has_long_call and has_call:
        return "diagonal"
    if has_long_call:
        return "long_call"
    if has_call:
        return "covered_call"
    if shares >= 100:
        return "assigned"
    return "cash"


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
    legs, longs = legs_from_marks(symbol, marks, today)
    premiums = sum(e.cash_delta or 0.0 for e in evs if e.kind in OPTION_EVENTS)
    long_pnl = sum(e.cash_delta or 0.0 for e in evs if e.kind in LONG_EVENTS)
    realized = premiums + realized_share_pnl(evs) + long_pnl
    long_calls = [l for l in longs if l.kind == "call"]
    basis = None
    if shares > 0 and avg_entry is not None:
        basis = (avg_entry * shares - premiums) / shares
    elif long_calls:
        # The long call stands in for the shares: its strike plus what it
        # cost, less the premiums collected, per share it controls.
        contracts = sum(l.qty for l in long_calls)
        debit = sum(l.entry_credit * 100 * l.qty for l in long_calls)
        basis = min(l.strike for l in long_calls) + (debit - premiums) / (100 * contracts)
    phase = phase_of(
        shares=shares,
        has_put=any(l.kind == "put" for l in legs),
        has_call=any(l.kind == "call" for l in legs),
        has_long_call=bool(long_calls),
    )
    return CampaignSnapshot(
        shares=shares,
        shares_avg_entry=avg_entry,
        open_legs=legs,
        premiums_collected=premiums,
        realized_pnl=realized,
        cost_basis=basis,
        phase=phase,
        long_legs=longs,
        long_pnl=long_pnl,
    )
