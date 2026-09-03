"""Alpaca reports option positions one contract at a time. This groups them
back into the spreads they were opened as -- by underlying and expiry,
then classified by the shape of the legs -- so the widget can show "bull
put 740/745 x2" with one P&L instead of two rows that only mean something
together.

Pure over plain dicts (what OrderService._plain hands back), so it is
testable without the SDK.
"""

import math
from dataclasses import dataclass, field
from datetime import date, datetime

from app.options.occ import Kind, try_parse_occ
from app.services.market_clock import ET


def _num(value) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return 0.0
    return out if math.isfinite(out) else 0.0


@dataclass
class SpreadPositionLeg:
    symbol: str
    kind: Kind
    strike: float
    qty: int  # signed: long positive, short negative
    avg_entry_price: float
    current_price: float
    market_value: float
    unrealized_pl: float
    cost_basis: float

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "kind": self.kind,
            "strike": self.strike,
            "qty": self.qty,
            "avg_entry_price": self.avg_entry_price,
            "current_price": self.current_price,
            "market_value": self.market_value,
            "unrealized_pl": self.unrealized_pl,
            "cost_basis": self.cost_basis,
        }


@dataclass
class SpreadGroup:
    id: str
    underlying: str
    root: str
    expiry: date
    dte: int
    strategy: str  # long_call | long_put | bull_call | bear_put | bull_put | bear_call | iron_condor | custom | broken
    qty: int  # spreads held; for a broken group, what the smallest leg supports
    legs: list[SpreadPositionLeg] = field(default_factory=list)
    # Per-share net entry, signed like a ticket price: positive was paid
    # (debit), negative was received (credit).
    net_entry: float = 0.0
    market_value: float = 0.0
    unrealized_pl: float = 0.0
    broken: bool = False
    account: str = "paper"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "underlying": self.underlying,
            "root": self.root,
            "expiry": self.expiry.isoformat(),
            "dte": self.dte,
            "strategy": self.strategy,
            "qty": self.qty,
            "legs": [leg.to_dict() for leg in self.legs],
            "net_entry": self.net_entry,
            "market_value": self.market_value,
            "unrealized_pl": self.unrealized_pl,
            "broken": self.broken,
            "account": self.account,
        }


def classify(legs: list[SpreadPositionLeg]) -> tuple[str, int, bool]:
    """(strategy, spreads held, broken). Legs are expected in ascending
    strike order."""
    if not legs:
        return "broken", 0, True
    sizes = [abs(leg.qty) for leg in legs]
    qty = min(sizes)
    balanced = len(set(sizes)) == 1
    if len(legs) == 1:
        # One long contract is a position in its own right; one short
        # contract is the remains of a spread.
        leg = legs[0]
        if leg.qty > 0:
            return ("long_call" if leg.kind == "call" else "long_put"), qty, False
        return "broken", qty, True
    if not balanced:
        return "broken", qty, True
    if len(legs) == 2:
        lo, hi = legs
        if lo.kind == hi.kind and (lo.qty > 0) != (hi.qty > 0):
            long_is_lower = lo.qty > 0
            if lo.kind == "call":
                return ("bull_call" if long_is_lower else "bear_call"), qty, False
            return ("bull_put" if long_is_lower else "bear_put"), qty, False
        return "custom", qty, False
    if len(legs) == 4:
        puts = [leg for leg in legs if leg.kind == "put"]
        calls = [leg for leg in legs if leg.kind == "call"]
        if (
            len(puts) == 2
            and len(calls) == 2
            and puts[0].qty > 0
            and puts[1].qty < 0
            and calls[0].qty < 0
            and calls[1].qty > 0
            and puts[1].strike < calls[0].strike
        ):
            return "iron_condor", qty, False
    return "custom", qty, False


def group_spreads(positions: list[dict], account: str = "paper", today: date | None = None) -> list[SpreadGroup]:
    today = today or datetime.now(ET).date()
    buckets: dict[tuple[str, date], list[SpreadPositionLeg]] = {}
    roots: dict[tuple[str, date], str] = {}
    for position in positions or []:
        if (position.get("asset_class") or "").lower() != "us_option":
            continue
        parsed = try_parse_occ(position.get("symbol"))
        if parsed is None:
            continue
        qty = int(round(_num(position.get("qty"))))
        if qty == 0:
            continue
        leg = SpreadPositionLeg(
            symbol=parsed.symbol,
            kind=parsed.kind,
            strike=parsed.strike,
            qty=qty,
            avg_entry_price=_num(position.get("avg_entry_price")),
            current_price=_num(position.get("current_price")),
            market_value=_num(position.get("market_value")),
            unrealized_pl=_num(position.get("unrealized_pl")),
            cost_basis=_num(position.get("cost_basis")),
        )
        key = (parsed.underlying, parsed.expiry)
        buckets.setdefault(key, []).append(leg)
        roots.setdefault(key, parsed.root)

    groups: list[SpreadGroup] = []
    for (underlying, expiry), legs in sorted(buckets.items()):
        legs.sort(key=lambda leg: (leg.strike, leg.kind))
        strategy, qty, broken = classify(legs)
        # Net entry per share per spread: what was paid minus what was
        # received, scaled by how many spreads the leg quantity represents.
        net_entry = 0.0
        if qty > 0:
            for leg in legs:
                ratio = abs(leg.qty) / qty
                net_entry += (leg.avg_entry_price if leg.qty > 0 else -leg.avg_entry_price) * ratio
        groups.append(
            SpreadGroup(
                id=f"{underlying}:{expiry.isoformat()}",
                underlying=underlying,
                root=roots[(underlying, expiry)],
                expiry=expiry,
                dte=(expiry - today).days,
                strategy=strategy,
                qty=qty,
                legs=legs,
                net_entry=round(net_entry, 4),
                market_value=round(sum(leg.market_value for leg in legs), 2),
                unrealized_pl=round(sum(leg.unrealized_pl for leg in legs), 2),
                broken=broken,
                account=account,
            )
        )
    return groups
