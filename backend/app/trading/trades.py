"""Round trips: which position made or lost what.

Alpaca has no "closed positions" endpoint and no realized P&L per trade --
a position that goes flat simply disappears from /positions, and what is
left behind is a list of fills. This module turns those fills back into
trades: per symbol, the first fill from flat opens a round trip, fills
accumulate while the position is non-zero, and the fill that brings it
back to flat closes it. A fill that goes *through* flat (selling 150 of a
100-share long) closes one trip and opens the next with the remainder.

Pure: the broker I/O lives in OrderService.sync_trades and persistence in
TradeStore, so the pairing can be tested on hand-written fills.

The R multiple is measured against the entry order's *initial* stop-loss
leg -- the same unit the strategy backtests report expectancy in, so live
results and backtests read side by side. A stop that was moved later
(break-even, trailing) does not change the risk that was taken.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

# Fills are reported in shares, but as strings from the broker; anything
# below this is float noise from the arithmetic, not a position.
_FLAT = 1e-9


@dataclass(frozen=True)
class Fill:
    symbol: str
    side: str  # "buy" | "sell"
    qty: float
    price: float
    at: datetime
    order_id: str
    # The stop-loss leg attached to the order this fill came from, if any.
    # Only meaningful on an entry; carried on every fill because the pairing
    # does not know which fills are entries until it has walked them.
    stop_loss_price: float | None = None


@dataclass
class Trade:
    symbol: str
    side: str  # "long" | "short"
    opened_at: datetime
    closed_at: datetime
    qty: float
    entry_avg: float
    exit_avg: float
    pnl: float
    pnl_pct: float | None
    initial_stop: float | None
    risk_per_share: float | None
    r_multiple: float | None
    entry_order_id: str
    exit_order_ids: list[str]
    fill_count: int
    # Which trip this is among those the entry order opened. 0 normally; a
    # fill that flipped the position opens a second trip from the same
    # order, and that one is 1. Part of the id so the two do not collide.
    leg: int = 0

    @property
    def id(self) -> str:
        return f"{self.symbol}:{self.entry_order_id}:{self.leg}"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "side": self.side,
            "opened_at": self.opened_at.isoformat(),
            "closed_at": self.closed_at.isoformat(),
            "qty": self.qty,
            "entry_avg": self.entry_avg,
            "exit_avg": self.exit_avg,
            "pnl": self.pnl,
            "pnl_pct": self.pnl_pct,
            "initial_stop": self.initial_stop,
            "risk_per_share": self.risk_per_share,
            "r_multiple": self.r_multiple,
            "entry_order_id": self.entry_order_id,
            "exit_order_ids": list(self.exit_order_ids),
            "fill_count": self.fill_count,
        }


@dataclass
class _OpenTrip:
    symbol: str
    side: str
    opened_at: datetime
    entry_order_id: str
    initial_stop: float | None
    leg: int
    entry_qty: float = 0.0
    entry_value: float = 0.0
    exit_qty: float = 0.0
    exit_value: float = 0.0
    exit_order_ids: list[str] = field(default_factory=list)
    fill_count: int = 0
    last_at: datetime | None = None

    @property
    def open_qty(self) -> float:
        return self.entry_qty - self.exit_qty

    def add_entry(self, qty: float, price: float, at: datetime) -> None:
        self.entry_qty += qty
        self.entry_value += qty * price
        self.fill_count += 1
        self.last_at = at

    def add_exit(self, qty: float, price: float, order_id: str, at: datetime) -> None:
        self.exit_qty += qty
        self.exit_value += qty * price
        if order_id not in self.exit_order_ids:
            self.exit_order_ids.append(order_id)
        self.fill_count += 1
        self.last_at = at

    def finish(self) -> Trade:
        qty = self.entry_qty
        entry_avg = self.entry_value / qty
        exit_avg = self.exit_value / self.exit_qty
        direction = 1.0 if self.side == "long" else -1.0
        pnl = (exit_avg - entry_avg) * qty * direction
        pnl_pct = pnl / (entry_avg * qty) * 100.0 if entry_avg > 0 else None
        risk_per_share = None
        r_multiple = None
        if self.initial_stop is not None:
            risk_per_share = abs(entry_avg - self.initial_stop)
            if risk_per_share > _FLAT:
                r_multiple = pnl / (risk_per_share * qty)
            else:
                risk_per_share = None
        return Trade(
            symbol=self.symbol,
            side=self.side,
            opened_at=self.opened_at,
            closed_at=self.last_at or self.opened_at,
            qty=qty,
            entry_avg=entry_avg,
            exit_avg=exit_avg,
            pnl=pnl,
            pnl_pct=pnl_pct,
            initial_stop=self.initial_stop,
            risk_per_share=risk_per_share,
            r_multiple=r_multiple,
            entry_order_id=self.entry_order_id,
            exit_order_ids=list(self.exit_order_ids),
            fill_count=self.fill_count,
            leg=self.leg,
        )


def round_trips(fills: list[Fill]) -> tuple[list[Trade], dict[str, float]]:
    """Pair fills into closed trades.

    Returns the closed trips in closing order, and the symbols still open
    with their signed quantity (positive long, negative short) -- those are
    positions, and belong in the positions table until they are flat.
    """
    by_symbol: dict[str, list[Fill]] = {}
    for f in fills:
        by_symbol.setdefault(f.symbol, []).append(f)

    closed: list[Trade] = []
    still_open: dict[str, float] = {}
    for symbol, symbol_fills in by_symbol.items():
        # Time first; order id second so two fills with the same timestamp
        # (a bracket's entry and an instant stop-out) pair the same way on
        # every run.
        symbol_fills.sort(key=lambda f: (f.at, f.order_id))
        legs_opened: dict[str, int] = {}
        current: _OpenTrip | None = None

        for fill in symbol_fills:
            remaining = fill.qty
            while remaining > _FLAT:
                if current is None:
                    leg = legs_opened.get(fill.order_id, 0)
                    legs_opened[fill.order_id] = leg + 1
                    current = _OpenTrip(
                        symbol=symbol,
                        side="long" if fill.side == "buy" else "short",
                        opened_at=fill.at,
                        entry_order_id=fill.order_id,
                        initial_stop=fill.stop_loss_price,
                        leg=leg,
                    )
                adds = (fill.side == "buy") == (current.side == "long")
                if adds:
                    current.add_entry(remaining, fill.price, fill.at)
                    remaining = 0.0
                    continue
                take = min(remaining, current.open_qty)
                current.add_exit(take, fill.price, fill.order_id, fill.at)
                remaining -= take
                if current.open_qty <= _FLAT:
                    closed.append(current.finish())
                    current = None

        if current is not None:
            signed = current.open_qty if current.side == "long" else -current.open_qty
            still_open[symbol] = signed

    closed.sort(key=lambda t: t.closed_at)
    return closed, still_open


def summarize(trades: list[dict]) -> dict:
    """Aggregate what a list of trade dicts (Trade.to_dict or store rows)
    adds up to. Percentages are percentages; R figures cover only the
    trades that had an initial stop to measure against."""
    pnls = [float(t["pnl"]) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    rs = [float(t["r_multiple"]) for t in trades if t.get("r_multiple") is not None]
    gross_loss = -sum(losses)
    decided = len(wins) + len(losses)
    return {
        "count": len(pnls),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / decided * 100.0) if decided else None,
        "total_pnl": sum(pnls),
        "avg_win": (sum(wins) / len(wins)) if wins else None,
        "avg_loss": (sum(losses) / len(losses)) if losses else None,
        "profit_factor": (sum(wins) / gross_loss) if gross_loss > 0 else None,
        "r_count": len(rs),
        "avg_r": (sum(rs) / len(rs)) if rs else None,
        "total_r": sum(rs) if rs else None,
    }


# --- from the broker's order dumps --------------------------------------


def _number(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _when(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        stamp = value
    else:
        try:
            stamp = datetime.fromisoformat(str(value))
        except ValueError:
            return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)


def _stop_leg_price(order: dict) -> float | None:
    """The stop-loss leg's price on a bracket/OTO parent, or None."""
    for leg in order.get("legs") or []:
        if not isinstance(leg, dict):
            continue
        if "stop" in str(leg.get("order_type", "")).lower():
            price = _number(leg.get("stop_price"))
            if price is not None:
                return price
    return None


def fills_from_orders(orders: list[dict]) -> list[Fill]:
    """Every filled order -- parents and their legs -- as one Fill each.

    Asked for with nested=True so a bracket's legs arrive under their
    parent, which is the only way to know which stop belonged to which
    entry. Legs are then flattened into the list in their own right (a
    filled stop leg is the exit), de-duplicated by id in case the broker
    also listed them at the top level. Partial fills of one order collapse
    into its average price; the total value is exact either way.
    """
    seen: set[str] = set()
    fills: list[Fill] = []

    def visit(order: dict) -> None:
        order_id = str(order.get("id", "") or "")
        if not order_id or order_id in seen:
            return
        seen.add(order_id)
        qty = _number(order.get("filled_qty"))
        price = _number(order.get("filled_avg_price"))
        at = _when(order.get("filled_at"))
        side = str(order.get("side", "")).lower()
        symbol = str(order.get("symbol", "")).upper()
        if qty and qty > 0 and price and at and side in ("buy", "sell") and symbol:
            fills.append(
                Fill(
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    price=price,
                    at=at,
                    order_id=order_id,
                    stop_loss_price=_stop_leg_price(order),
                )
            )
        for leg in order.get("legs") or []:
            if isinstance(leg, dict):
                visit(leg)

    for order in orders:
        if isinstance(order, dict):
            visit(order)
    return fills
