"""The matching engine behind Simulation Mode.

Pricing/sizing/validation is entirely delegated to the real, pure
app.trading.models.resolve_ticket -- this module only ever receives an
already-ResolvedOrder and turns it into fills. No slippage, no partial
fills modeled: every fill is exact-price, full-quantity. That is an
accepted v1 approximation (see the plan), not an oversight.

Fill semantics:
- A market order, or a limit order already marketable (crosses the current
  price the way a real broker's would), fills instantly at submit time.
- A resting limit/stop/stop_limit entry, or a bracket's take-profit/
  stop-loss legs, sit until the background loop (sim/loop.py) sees a
  price cross their trigger.
- Bracket legs share an oco_group_id; filling one cancels its sibling in
  the same tick. A stop_limit triggers and fills at its limit_price in one
  step (a real one can trigger and then fail to fill on a gap -- not
  modeled).

Position/cash bookkeeping mirrors app.trading.trades._OpenTrip's
entry/exit/flip handling, one fill at a time (sim applies each fill the
instant it happens, rather than replaying a fill log after the fact).
"""

import uuid
from datetime import UTC, datetime

from app.trading.errors import OrderRejected
from app.trading.models import ResolvedOrder
from app.trading.sim.store import SimStore

# Same tolerance trades.py's _OpenTrip uses -- fills are floats, and a
# position that nets to 1e-10 shares is flat, not a rounding artifact worth
# keeping open.
_FLAT = 1e-9


def _is_marketable(resolved: ResolvedOrder, reference_price: float | None) -> bool:
    if reference_price is None:
        return False
    """A limit already on the fillable side of the market -- same test
    models._marketable_warning uses to decide whether to warn the ticket."""
    if resolved.order_type != "limit":
        return False
    if resolved.side == "buy":
        return resolved.limit_price >= reference_price
    return resolved.limit_price <= reference_price


def _crosses(order: dict, price: float) -> bool:
    """Whether a live price has reached a resting order's trigger."""
    order_type = order["order_type"]
    side = order["side"]
    if order_type == "limit":
        limit = order["limit_price"]
        return price <= limit if side == "buy" else price >= limit
    if order_type in ("stop", "stop_limit"):
        stop = order["stop_price"]
        return price >= stop if side == "buy" else price <= stop
    return False


def _fill_price(order: dict) -> float:
    """The price a crossed resting order fills at -- its own trigger/limit,
    not the live price that crossed it (no slippage modeled)."""
    if order["order_type"] in ("limit", "stop_limit"):
        return order["limit_price"]
    return order["stop_price"]


def _apply_fill_to_position(
    position: dict | None,
    *,
    symbol: str,
    order_id: str,
    side: str,
    qty: float,
    price: float,
    now: datetime,
    initial_stop: float | None,
) -> tuple[dict | None, dict | None]:
    """Pure arithmetic: the position after one fill, and the sim_trades row
    to record if any portion of the fill closed it back to flat.

    Three exhaustive cases: no existing position (the fill opens one),
    same-direction (the fill adds, weighted-averaging the entry), or
    opposite-direction (the fill reduces/closes it, and if the fill is
    bigger than the position, flips through flat into a fresh position on
    the other side -- the same thing a real account does with a plain
    sell-when-long order past the held quantity).
    """
    delta = qty if side == "buy" else -qty

    if position is None:
        return _open_fresh(symbol, order_id, delta, price, now, initial_stop), None

    signed_old = position["qty"] if position["side"] == "long" else -position["qty"]

    if (signed_old > 0) == (delta > 0):
        new_qty = position["qty"] + qty
        new_avg = (position["avg_entry_price"] * position["qty"] + price * qty) / new_qty
        return {**position, "qty": new_qty, "avg_entry_price": new_avg}, None

    closing_qty = min(qty, abs(signed_old))
    exit_qty = position["exit_qty"] + closing_qty
    exit_value = position["exit_value"] + closing_qty * price
    exit_order_ids = list(position["exit_order_ids"])
    if order_id not in exit_order_ids:
        exit_order_ids.append(order_id)
    remaining = abs(signed_old) - closing_qty

    trade_row = None
    if remaining > _FLAT:
        new_position: dict | None = {
            **position,
            "qty": remaining,
            "exit_qty": exit_qty,
            "exit_value": exit_value,
            "exit_order_ids": exit_order_ids,
            "fill_count": position["fill_count"] + 1,
        }
    else:
        entry_avg = position["avg_entry_price"]
        exit_avg = exit_value / exit_qty
        direction = 1.0 if position["side"] == "long" else -1.0
        pnl = (exit_avg - entry_avg) * exit_qty * direction
        pnl_pct = (pnl / (entry_avg * exit_qty) * 100.0) if entry_avg > 0 else None
        stop = position.get("initial_stop")
        risk_per_share = None
        r_multiple = None
        if stop is not None:
            risk_per_share = abs(entry_avg - stop)
            if risk_per_share > _FLAT:
                r_multiple = pnl / (risk_per_share * exit_qty)
            else:
                risk_per_share = None
        trade_row = {
            "id": f"{symbol}:{position['entry_order_id']}:0",
            "symbol": symbol,
            "side": position["side"],
            "opened_at": position["opened_at"],
            "closed_at": now.isoformat(),
            "qty": exit_qty,
            "entry_avg": entry_avg,
            "exit_avg": exit_avg,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "initial_stop": stop,
            "risk_per_share": risk_per_share,
            "r_multiple": r_multiple,
            "entry_order_id": position["entry_order_id"],
            "exit_order_ids": exit_order_ids,
            "fill_count": position["fill_count"] + 1,
        }
        new_position = None

    leftover = qty - closing_qty
    if leftover > _FLAT:
        # The fill flipped straight through flat -- the excess opens a
        # fresh position on the other side, same as a real account.
        flip_delta = leftover if delta > 0 else -leftover
        new_position = _open_fresh(symbol, order_id, flip_delta, price, now, initial_stop)

    return new_position, trade_row


def _open_fresh(
    symbol: str, order_id: str, delta: float, price: float, now: datetime, initial_stop: float | None
) -> dict:
    return {
        "symbol": symbol,
        "side": "long" if delta > 0 else "short",
        "qty": abs(delta),
        "avg_entry_price": price,
        "opened_at": now.isoformat(),
        "entry_order_id": order_id,
        "initial_stop": initial_stop,
        "exit_qty": 0.0,
        "exit_value": 0.0,
        "exit_order_ids": [],
        "fill_count": 1,
    }


class SimBroker:
    def __init__(self, store: SimStore, user_id: int):
        self._store = store
        self._user_id = user_id

    async def submit(self, resolved: ResolvedOrder, *, reference_price: float | None) -> dict:
        now = datetime.now(UTC)
        order_id = str(uuid.uuid4())
        fills_now = resolved.order_type == "market" or _is_marketable(resolved, reference_price)

        entry_row = {
            "id": order_id,
            "parent_id": None,
            "oco_group_id": None,
            "leg_role": None if resolved.order_class == "simple" else "entry",
            "client_order_id": resolved.client_order_id,
            "symbol": resolved.symbol,
            "side": resolved.side,
            "order_type": resolved.order_type,
            "order_class": resolved.order_class,
            "qty": resolved.qty,
            "filled_qty": 0,
            "limit_price": resolved.limit_price,
            "stop_price": resolved.stop_price,
            "time_in_force": resolved.time_in_force,
            "status": "new",
            "filled_avg_price": None,
            "submitted_at": now.isoformat(),
            "filled_at": None,
            "canceled_at": None,
        }
        await self._store.insert_order(self._user_id, entry_row)

        oco_group = (
            str(uuid.uuid4())
            if (resolved.take_profit_price is not None and resolved.stop_loss_price is not None)
            else None
        )
        exit_side = "sell" if resolved.side == "buy" else "buy"
        if resolved.take_profit_price is not None:
            await self._store.insert_order(
                self._user_id,
                {
                    "id": str(uuid.uuid4()),
                    "parent_id": order_id,
                    "oco_group_id": oco_group,
                    "leg_role": "take_profit",
                    "client_order_id": None,
                    "symbol": resolved.symbol,
                    "side": exit_side,
                    "order_type": "limit",
                    "order_class": resolved.order_class,
                    "qty": resolved.qty,
                    "filled_qty": 0,
                    "limit_price": resolved.take_profit_price,
                    "stop_price": None,
                    "time_in_force": resolved.time_in_force,
                    "status": "held",
                    "filled_avg_price": None,
                    "submitted_at": now.isoformat(),
                    "filled_at": None,
                    "canceled_at": None,
                }
            )
        if resolved.stop_loss_price is not None:
            await self._store.insert_order(
                self._user_id,
                {
                    "id": str(uuid.uuid4()),
                    "parent_id": order_id,
                    "oco_group_id": oco_group,
                    "leg_role": "stop_loss",
                    "client_order_id": None,
                    "symbol": resolved.symbol,
                    "side": exit_side,
                    "order_type": "stop",
                    "order_class": resolved.order_class,
                    "qty": resolved.qty,
                    "filled_qty": 0,
                    "limit_price": None,
                    "stop_price": resolved.stop_loss_price,
                    "time_in_force": resolved.time_in_force,
                    "status": "held",
                    "filled_avg_price": None,
                    "submitted_at": now.isoformat(),
                    "filled_at": None,
                    "canceled_at": None,
                }
            )

        if fills_now:
            await self._fill_order(entry_row, reference_price, now)

        return await self._store.get_order(self._user_id, order_id)

    async def _fill_order(self, order: dict, fill_price: float, now: datetime) -> None:
        await self._store.update_order(
            self._user_id,
            order["id"],
            status="filled",
            filled_qty=order["qty"],
            filled_avg_price=fill_price,
            filled_at=now.isoformat(),
        )
        if order.get("oco_group_id"):
            await self._store.cancel_oco_siblings(self._user_id, order["oco_group_id"], order["id"], now)

        is_entry = order.get("leg_role") in (None, "entry")
        initial_stop = None
        if is_entry:
            await self._store.activate_children(self._user_id, order["id"])
            initial_stop = await self._store.child_stop_price(self._user_id, order["id"])

        position = await self._store.get_position(self._user_id, order["symbol"])
        new_position, trade_row = _apply_fill_to_position(
            position,
            symbol=order["symbol"],
            order_id=order["id"],
            side=order["side"],
            qty=order["qty"],
            price=fill_price,
            now=now,
            initial_stop=initial_stop,
        )
        if new_position is None:
            if position is not None:
                await self._store.delete_position(self._user_id, order["symbol"])
        else:
            await self._store.upsert_position(self._user_id, new_position)
        if trade_row is not None:
            await self._store.insert_trade(self._user_id, trade_row)

        cash_delta = order["qty"] * fill_price if order["side"] == "sell" else -(order["qty"] * fill_price)
        await self._store.add_cash(self._user_id, cash_delta)

    async def check_fills(self, prices: dict[str, float]) -> None:
        now = datetime.now(UTC)
        working = await self._store.working_orders_by_symbol(self._user_id)
        skip: set[str] = set()
        for symbol, orders in working.items():
            price = prices.get(symbol)
            if price is None:
                continue
            for order in orders:
                if order["id"] in skip or order["status"] != "new":
                    continue
                if not _crosses(order, price):
                    continue
                await self._fill_order(order, _fill_price(order), now)
                if order.get("oco_group_id"):
                    skip.update(
                        o["id"]
                        for group in working.values()
                        for o in group
                        if o.get("oco_group_id") == order["oco_group_id"]
                    )

    async def cancel(self, order_id: str) -> None:
        order = await self._store.get_order(self._user_id, order_id)
        if order is None or order["status"] not in ("new", "held"):
            raise OrderRejected("No such working order.", field="order_id")
        now = datetime.now(UTC)
        await self._store.update_order(self._user_id, order_id, status="canceled", canceled_at=now.isoformat())
        if order.get("oco_group_id"):
            await self._store.cancel_oco_siblings(self._user_id, order["oco_group_id"], order_id, now)
        if order.get("leg_role") in (None, "entry"):
            await self._store.cancel_children(self._user_id, order_id, now)

    async def replace_price(self, order_id: str, *, stop_price: float | None, limit_price: float | None) -> dict:
        fields: dict = {}
        if stop_price is not None:
            fields["stop_price"] = stop_price
        if limit_price is not None:
            fields["limit_price"] = limit_price
        await self._store.update_order(self._user_id, order_id, **fields)
        return await self._store.get_order(self._user_id, order_id)

    async def close_position(self, symbol: str, qty: float | None, *, reference_price: float) -> dict:
        position = await self._store.get_position(self._user_id, symbol)
        if position is None:
            raise OrderRejected(f"No open position in {symbol}.", field="symbol")

        now = datetime.now(UTC)
        working = (await self._store.working_orders_by_symbol(self._user_id)).get(symbol, [])
        cancelled: list[str] = []
        for order in working:
            await self._store.update_order(self._user_id, order["id"], status="canceled", canceled_at=now.isoformat())
            cancelled.append(order["id"])

        position_qty = position["qty"]
        closing_qty = position_qty if qty is None or qty >= position_qty else qty
        if closing_qty <= 0:
            raise OrderRejected("Quantity must be positive.", field="qty")
        closing_side = "sell" if position["side"] == "long" else "buy"

        order_id = str(uuid.uuid4())
        order_row = {
            "id": order_id,
            "parent_id": None,
            "oco_group_id": None,
            "leg_role": None,
            "client_order_id": None,
            "symbol": symbol,
            "side": closing_side,
            "order_type": "market",
            "order_class": "simple",
            "qty": closing_qty,
            "filled_qty": 0,
            "limit_price": None,
            "stop_price": None,
            "time_in_force": "day",
            "status": "new",
            "filled_avg_price": None,
            "submitted_at": now.isoformat(),
            "filled_at": None,
            "canceled_at": None,
        }
        await self._store.insert_order(self._user_id, order_row)
        await self._fill_order(order_row, reference_price, now)

        result = await self._store.get_order(self._user_id, order_id)
        result["cancelled_orders"] = cancelled
        # Re-arming a partial close's remaining stop/target (what the real
        # OrderService does) is out of v1 scope for the simulator -- a
        # partial close here just leaves the remainder unprotected, same as
        # cancelling everything and not replacing it.
        result["rearmed_orders"] = []
        result["stop_lost"] = bool(cancelled) and (qty is not None and qty < position_qty)
        return result
