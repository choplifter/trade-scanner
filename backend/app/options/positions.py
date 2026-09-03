"""Alpaca reports option positions one contract at a time. This groups them
back into the structures they were opened as -- so the widget can show
"bull put 740/745 x2" with one P&L instead of two rows that only mean
something together.

Grouping runs per underlying. Within one underlying, the two-expiry
structures (calendar/diagonal: a short leg in a nearer expiry and a long
leg of the same kind in a later one) are paired off first, then covered
calls (a short call backed by 100 shares per contract) and cash-secured
puts (a lone short put -- Alpaca would not let it exist uncovered), and
whatever is left is classified per expiry by the shape of its legs.

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
    expiry: date | None = None
    # The OCC root (SPY1 after a corporate action), per leg: one
    # underlying can hold contracts under two roots.
    root: str = ""

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
            "expiry": self.expiry.isoformat() if self.expiry else None,
        }


@dataclass
class SpreadGroup:
    id: str
    underlying: str
    root: str
    expiry: date
    dte: int
    # One of the ticket strategies, or custom | broken.
    strategy: str
    qty: int  # structures held; for a broken group, what the smallest leg supports
    legs: list[SpreadPositionLeg] = field(default_factory=list)
    # Per-share net entry, signed like a ticket price: positive was paid
    # (debit), negative was received (credit).
    net_entry: float = 0.0
    market_value: float = 0.0
    unrealized_pl: float = 0.0
    broken: bool = False
    account: str = "paper"
    # The later expiry of a calendar/diagonal.
    long_expiry: date | None = None
    # Shares backing a covered call.
    shares: int = 0

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
            "long_expiry": self.long_expiry.isoformat() if self.long_expiry else None,
            "shares": self.shares,
        }


def classify(legs: list[SpreadPositionLeg]) -> tuple[str, int, bool]:
    """(strategy, structures held, broken) for legs of ONE expiry, in
    ascending (strike, kind) order."""
    if not legs:
        return "broken", 0, True
    sizes = [abs(leg.qty) for leg in legs]
    qty = min(sizes)
    balanced = len(set(sizes)) == 1
    if len(legs) == 1:
        # One long contract is a position in its own right; one short put
        # is cash-secured (Alpaca allows no naked writing at level 3); one
        # short call without shares is the remains of a spread.
        leg = legs[0]
        if leg.qty > 0:
            return ("long_call" if leg.kind == "call" else "long_put"), qty, False
        if leg.kind == "put":
            return "cash_secured_put", qty, False
        return "broken", qty, True
    if len(legs) == 2:
        lo, hi = legs
        if lo.kind == hi.kind and balanced and (lo.qty > 0) != (hi.qty > 0):
            long_is_lower = lo.qty > 0
            if lo.kind == "call":
                return ("bull_call" if long_is_lower else "bear_call"), qty, False
            return ("bull_put" if long_is_lower else "bear_put"), qty, False
        if lo.kind != hi.kind and balanced and lo.qty > 0 and hi.qty > 0:
            put = lo if lo.kind == "put" else hi
            call = hi if put is lo else lo
            if put.strike == call.strike:
                return "long_straddle", qty, False
            if put.strike < call.strike:
                return "long_strangle", qty, False
        if not balanced:
            return "broken", qty, True
        return "custom", qty, False
    if len(legs) == 3:
        lo, mid, hi = legs
        if (
            lo.kind == mid.kind == hi.kind
            and lo.qty > 0
            and hi.qty > 0
            and mid.qty < 0
            and abs(lo.qty) == abs(hi.qty)
            and abs(mid.qty) == 2 * abs(lo.qty)
        ):
            return ("call_butterfly" if lo.kind == "call" else "put_butterfly"), abs(lo.qty), False
        return "broken", qty, True
    if len(legs) == 4:
        puts = [leg for leg in legs if leg.kind == "put"]
        calls = [leg for leg in legs if leg.kind == "call"]
        if (
            len(puts) == 2
            and len(calls) == 2
            and balanced
            and puts[0].qty > 0
            and puts[1].qty < 0
            and calls[0].qty < 0
            and calls[1].qty > 0
        ):
            if puts[1].strike == calls[0].strike:
                return "iron_butterfly", qty, False
            if puts[1].strike < calls[0].strike:
                return "iron_condor", qty, False
    if not balanced:
        return "broken", qty, True
    return "custom", qty, False


def _leg_from_position(position: dict) -> SpreadPositionLeg | None:
    parsed = try_parse_occ(position.get("symbol"))
    if parsed is None:
        return None
    qty = int(round(_num(position.get("qty"))))
    if qty == 0:
        return None
    return SpreadPositionLeg(
        symbol=parsed.symbol,
        kind=parsed.kind,
        strike=parsed.strike,
        qty=qty,
        avg_entry_price=_num(position.get("avg_entry_price")),
        current_price=_num(position.get("current_price")),
        market_value=_num(position.get("market_value")),
        unrealized_pl=_num(position.get("unrealized_pl")),
        cost_basis=_num(position.get("cost_basis")),
        expiry=parsed.expiry,
        root=parsed.root,
    )


def _group(
    underlying: str,
    root: str,
    expiry: date,
    legs: list[SpreadPositionLeg],
    strategy: str,
    qty: int,
    broken: bool,
    today: date,
    account: str,
    *,
    long_expiry: date | None = None,
    shares: int = 0,
    id_suffix: str = "",
) -> SpreadGroup:
    # Net entry per share per structure: what was paid minus what was
    # received, scaled by how many structures the leg quantity represents.
    net_entry = 0.0
    if qty > 0:
        for leg in legs:
            ratio = abs(leg.qty) / qty
            net_entry += (leg.avg_entry_price if leg.qty > 0 else -leg.avg_entry_price) * ratio
    return SpreadGroup(
        id=f"{underlying}:{expiry.isoformat()}{id_suffix}",
        underlying=underlying,
        root=root,
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
        long_expiry=long_expiry,
        shares=shares,
    )


def _pair_time_spreads(legs: list[SpreadPositionLeg]) -> list[tuple[SpreadPositionLeg, SpreadPositionLeg]]:
    """Calendar/diagonal pairs: a short leg and a long leg of the same kind
    and quantity, the long one in a later expiry. Greedy, nearest later
    expiry first; each leg is used once."""
    pairs: list[tuple[SpreadPositionLeg, SpreadPositionLeg]] = []
    shorts = [leg for leg in legs if leg.qty < 0]
    longs = [leg for leg in legs if leg.qty > 0]
    used: set[str] = set()
    for short in sorted(shorts, key=lambda leg: (leg.expiry or date.min, leg.strike)):
        candidates = [
            leg
            for leg in longs
            if leg.symbol not in used
            and leg.kind == short.kind
            and leg.qty == -short.qty
            and leg.expiry is not None
            and short.expiry is not None
            and leg.expiry > short.expiry
        ]
        if not candidates:
            continue
        long = min(candidates, key=lambda leg: (leg.expiry, abs(leg.strike - short.strike)))  # type: ignore[arg-type]
        used.add(long.symbol)
        used.add(short.symbol)
        pairs.append((short, long))
    return pairs


def group_spreads(
    positions: list[dict],
    account: str = "paper",
    today: date | None = None,
    equity_positions: list[dict] | None = None,
) -> list[SpreadGroup]:
    today = today or datetime.now(ET).date()
    by_underlying: dict[str, list[SpreadPositionLeg]] = {}
    for position in positions or []:
        if (position.get("asset_class") or "").lower() != "us_option":
            continue
        leg = _leg_from_position(position)
        if leg is None:
            continue
        parsed = try_parse_occ(position.get("symbol"))
        assert parsed is not None
        by_underlying.setdefault(parsed.underlying, []).append(leg)

    shares_held: dict[str, int] = {}
    for position in equity_positions or []:
        if (position.get("asset_class") or "us_equity").lower() != "us_equity":
            continue
        symbol = (position.get("symbol") or "").upper()
        qty = int(round(_num(position.get("qty"))))
        if symbol and qty > 0:
            shares_held[symbol] = shares_held.get(symbol, 0) + qty

    groups: list[SpreadGroup] = []
    for underlying, legs in sorted(by_underlying.items()):
        remaining = list(legs)

        # Two-expiry pairs first.
        for short, long in _pair_time_spreads(remaining):
            remaining = [leg for leg in remaining if leg.symbol not in (short.symbol, long.symbol)]
            strategy = "calendar" if short.strike == long.strike else "diagonal"
            assert short.expiry is not None and long.expiry is not None
            groups.append(
                _group(
                    underlying, short.root, short.expiry, [short, long], strategy, abs(short.qty), False, today, account,
                    long_expiry=long.expiry, id_suffix=f":{strategy}:{long.expiry.isoformat()}:{short.strike:g}",
                )
            )

        # Covered calls: a short call with 100 shares per contract behind it
        # (shares are consumed across contracts, nearest expiry first).
        free_shares = shares_held.get(underlying, 0)
        for leg in sorted((l for l in remaining if l.kind == "call" and l.qty < 0), key=lambda l: (l.expiry or date.min, l.strike)):
            contracts = -leg.qty
            if free_shares >= 100 * contracts and leg.expiry is not None:
                free_shares -= 100 * contracts
                remaining = [l for l in remaining if l.symbol != leg.symbol]
                groups.append(
                    _group(
                        underlying, leg.root, leg.expiry, [leg], "covered_call", contracts, False, today, account,
                        shares=100 * contracts, id_suffix=f":cc:{leg.strike:g}",
                    )
                )

        # The rest, per expiry, by shape.
        per_expiry: dict[date, list[SpreadPositionLeg]] = {}
        for leg in remaining:
            if leg.expiry is not None:
                per_expiry.setdefault(leg.expiry, []).append(leg)
        for expiry, expiry_legs in sorted(per_expiry.items()):
            expiry_legs.sort(key=lambda leg: (leg.strike, leg.kind))
            strategy, qty, broken = classify(expiry_legs)
            groups.append(
                _group(underlying, expiry_legs[0].root, expiry, expiry_legs, strategy, qty, broken, today, account)
            )
    groups.sort(key=lambda g: (g.underlying, g.expiry, g.id))
    return groups
