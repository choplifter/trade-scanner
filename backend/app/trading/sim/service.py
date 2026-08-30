"""Router-facing facade for Simulation Mode.

Method names mirror app.trading.service.OrderService's, and every response
this produces is shaped -- field names, and numeric fields as Alpaca-style
decimal strings -- to match the real Account/Position/Order types exactly.
That compatibility is the entire point: the frontend's existing types,
useTrading, TradingContext and ChartWidget's position-line drawing all work
against sim data with zero changes, because they never see the difference.

Never touches AlpacaClients.trading (the broker client) -- only
AlpacaClients.data (market data), for live prices, the same client
reference_price()/day_high() already use ungated. Sizing/validation is the
same pure resolve_ticket() the real service uses, so a sim order is priced
and bounds-checked identically to a real one.
"""

import asyncio
import logging
from datetime import UTC, datetime

from app.alpaca.client import AlpacaClients
from app.core.config import Settings
from app.trading.errors import OrderRejected
from app.trading.models import OrderTicket, ResolvedOrder, resolve_ticket
from app.trading.service import _validate_stop_replacement, _validate_target_replacement
from app.trading.sim.broker import SimBroker
from app.trading.sim.store import SimStore
from app.trading.trades import bucket_by_day, in_period, period_start, summarize

logger = logging.getLogger(__name__)


def _money(value: float | None) -> str | None:
    """Plain float -> Alpaca-style decimal string, for the fields the
    frontend parses with num()."""
    if value is None:
        return None
    return f"{value:.2f}"


def _qty_str(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return str(value)


def _number(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _public_order(row: dict) -> dict:
    """sim_orders row -> the Order shape the frontend expects. Bracket legs
    are returned flat (legs always null) rather than nested under their
    parent -- exitsForPosition's withLegs() already treats a flat list and
    a nested one the same way (`o.legs ?? []`), so flat is simplest."""
    return {
        "id": row["id"],
        "symbol": row["symbol"],
        "side": row["side"],
        "order_type": row["order_type"],
        "qty": _qty_str(row["qty"]),
        "filled_qty": _qty_str(row["filled_qty"]),
        "limit_price": _money(row["limit_price"]) if row["limit_price"] is not None else None,
        "stop_price": _money(row["stop_price"]) if row["stop_price"] is not None else None,
        "filled_avg_price": _money(row["filled_avg_price"]) if row["filled_avg_price"] is not None else None,
        "status": row["status"],
        "time_in_force": row["time_in_force"],
        "submitted_at": row["submitted_at"],
        "created_at": row["submitted_at"],
        "filled_at": row["filled_at"],
        "legs": None,
    }


class SimOrderService:
    def __init__(
        self,
        clients: AlpacaClients,
        settings: Settings,
        store: SimStore,
        user_id: int,
        engine=None,
        replay=None,
    ) -> None:
        """`engine` is the live ScannerEngine (optional fast-path cache for
        reference_price/day_high, falls through to a fresh Alpaca call --
        see below). `replay` is `(ReplayEngine, as_of)` when this user's
        clock is currently in an active history-replay session (see
        app.replay); when set it takes over pricing *entirely*, bypassing
        both `engine` and the live Alpaca fallback below, since a replay
        fill must never blend in a live price -- see reference_price/day_high.
        """
        self._clients = clients
        self._settings = settings
        self._store = store
        self._user_id = user_id
        self._engine = engine
        self._replay = replay
        self._broker = SimBroker(store, user_id)

    # --- read paths ---------------------------------------------------

    async def account(self) -> dict:
        # Lazily created on first touch -- users are created after the app
        # starts, so there's no startup moment to seed every account at once.
        await self._store.ensure_account(self._user_id, self._settings.trading_sim_starting_cash)
        row = await self._store.get_account_row(self._user_id)
        positions = await self._store.list_positions(self._user_id)
        cash = row["cash"]
        equity = cash
        long_value = 0.0
        short_value = 0.0
        for p in positions:
            price = await self.reference_price(p["symbol"])
            if price is None:
                price = p["avg_entry_price"]
            value = p["qty"] * price
            if p["side"] == "long":
                equity += value
                long_value += value
            else:
                equity -= value
                short_value += value
        return {
            "account_number": "SIM-000001",
            "status": "ACTIVE",
            "currency": "USD",
            "cash": _money(cash),
            "equity": _money(equity),
            "last_equity": _money(equity),
            # No margin modeled -- buying power is exactly the cash on hand.
            "buying_power": _money(max(cash, 0.0)),
            "portfolio_value": _money(equity),
            "long_market_value": _money(long_value),
            "short_market_value": _money(-short_value),
            "daytrade_count": 0,
            "pattern_day_trader": False,
            "trading_blocked": False,
            "account_blocked": False,
            "shorting_enabled": True,
        }

    async def positions(self) -> list[dict]:
        rows = await self._store.list_positions(self._user_id)
        out = []
        for p in rows:
            price = await self.reference_price(p["symbol"])
            current = price if price is not None else p["avg_entry_price"]
            qty = p["qty"]
            avg = p["avg_entry_price"]
            direction = 1.0 if p["side"] == "long" else -1.0
            unrealized = (current - avg) * qty * direction
            cost_basis = qty * avg
            market_value = qty * current
            plpc = (unrealized / cost_basis) if cost_basis > 0 else None
            plpc_str = f"{plpc:.6f}" if plpc is not None else None
            out.append(
                {
                    "symbol": p["symbol"],
                    # Alpaca reports a short as a negative quantity -- see
                    # OrderService.close_position's docstring.
                    "qty": _qty_str(qty if p["side"] == "long" else -qty),
                    "side": p["side"],
                    "avg_entry_price": _money(avg),
                    "current_price": _money(current),
                    "market_value": _money(market_value if p["side"] == "long" else -market_value),
                    "cost_basis": _money(cost_basis),
                    "unrealized_pl": _money(unrealized),
                    "unrealized_plpc": plpc_str,
                    # Sim doesn't track a session-open baseline separately --
                    # intraday P&L is reported the same as total unrealized.
                    "unrealized_intraday_pl": _money(unrealized),
                    "unrealized_intraday_plpc": plpc_str,
                    "asset_id": f"sim-{p['symbol']}",
                }
            )
        return out

    async def orders(self, status: str = "open") -> list[dict]:
        rows = await self._store.list_orders(self._user_id, status)
        return [_public_order(r) for r in rows]

    async def trades(self, range_key: str = "all") -> dict:
        try:
            start = period_start(range_key)
        except ValueError as exc:
            raise OrderRejected(str(exc), field="range") from None
        all_trades = await self._store.list_trades(self._user_id)
        selected = in_period(all_trades, start)
        positions = await self._store.list_positions(self._user_id)
        return {
            "range": (range_key or "all").lower(),
            "period_start": start.isoformat() if start else None,
            "trades": selected,
            "summary": summarize(selected),
            "buckets": bucket_by_day(selected),
            "open_symbols": sorted(p["symbol"] for p in positions),
        }

    async def portfolio_history(self, range_key: str = "1M") -> dict:
        """A minimal 2-point curve (reset -> now) rather than a logged
        equity-history table -- see the plan's v1 scope trims. Upgrading to
        real periodic snapshots is an isolated follow-up if wanted later."""
        account_row = await self._store.get_account_row(self._user_id)
        acct = await self.account()
        end_equity = _number(acct["equity"])
        start_equity = account_row["starting_cash"]
        start_t = int(datetime.fromisoformat(account_row["reset_at"]).timestamp())
        now_t = int(datetime.now(UTC).timestamp())

        points = [{"t": start_t, "equity": start_equity, "profit_loss": 0.0, "profit_loss_pct": 0.0}]
        if now_t > start_t and end_equity is not None:
            change = end_equity - start_equity
            points.append(
                {
                    "t": now_t,
                    "equity": end_equity,
                    "profit_loss": change,
                    "profit_loss_pct": (change / start_equity * 100.0) if start_equity else None,
                }
            )
        change = points[-1]["equity"] - points[0]["equity"]
        return {
            "range": (range_key or "1M").upper(),
            "timeframe": "live",
            "points": points,
            "start_equity": points[0]["equity"],
            "end_equity": points[-1]["equity"],
            "change": change,
            "change_pct": (change / points[0]["equity"] * 100.0) if points[0]["equity"] else None,
        }

    # --- pricing --------------------------------------------------------
    # Duplicated from OrderService rather than imported, so this file makes
    # zero edits to trading/service.py -- see the plan for why that file
    # stays untouched.

    async def reference_price(self, symbol: str) -> float | None:
        if self._replay is not None:
            replay_engine, as_of = self._replay
            return replay_engine.reference_price(symbol, as_of)

        row = getattr(self._engine, "rows", {}).get(symbol) if self._engine else None
        if row is not None and getattr(row, "last_price", 0):
            return float(row.last_price)

        from alpaca.data.requests import StockLatestTradeRequest

        try:
            request = StockLatestTradeRequest(symbol_or_symbols=symbol, feed=self._clients.feed)
            latest = await asyncio.to_thread(self._clients.data.get_stock_latest_trade, request)
            trade = latest.get(symbol) if isinstance(latest, dict) else None
            return float(trade.price) if trade and trade.price else None
        except Exception:
            logger.warning("No reference price available for %s (sim)", symbol, exc_info=True)
            return None

    async def day_high(self, symbol: str) -> float | None:
        if self._replay is not None:
            replay_engine, as_of = self._replay
            return replay_engine.day_high(symbol, as_of)

        row = getattr(self._engine, "rows", {}).get(symbol) if self._engine else None
        if row is not None and getattr(row, "day_high", None):
            return float(row.day_high)

        from alpaca.data.requests import StockSnapshotRequest

        try:
            request = StockSnapshotRequest(symbol_or_symbols=symbol, feed=self._clients.feed)
            snapshots = await asyncio.to_thread(self._clients.data.get_stock_snapshot, request)
            snap = snapshots.get(symbol) if isinstance(snapshots, dict) else None
            daily_bar = snap.daily_bar if snap else None
            return float(daily_bar.high) if daily_bar and daily_bar.high else None
        except Exception:
            logger.warning("No day-high available for %s (sim)", symbol, exc_info=True)
            return None

    # --- preview / write path -------------------------------------------

    async def _resolve(self, ticket: OrderTicket) -> tuple[ResolvedOrder, float | None]:
        account = await self.account()
        price = await self.reference_price(ticket.symbol.upper())
        resolved = resolve_ticket(
            ticket,
            reference_price=price,
            equity=_number(account.get("equity")),
            buying_power=_number(account.get("buying_power")),
            max_qty=self._settings.trading_max_order_qty,
            max_notional=self._settings.trading_max_order_notional,
            max_notional_pct=self._settings.trading_max_order_notional_pct,
        )
        return resolved, price

    async def preview(self, ticket: OrderTicket) -> ResolvedOrder:
        resolved, _ = await self._resolve(ticket)
        return resolved

    async def submit(self, ticket: OrderTicket) -> dict:
        resolved, price = await self._resolve(ticket)
        return _public_order(await self._broker.submit(resolved, reference_price=price))

    async def cancel(self, order_id: str) -> None:
        await self._broker.cancel(order_id)

    async def replace_stop(self, order_id: str, symbol: str, stop_price: float) -> dict:
        symbol = symbol.upper()
        order = await self._store.get_order(self._user_id, order_id)
        if order is None:
            raise OrderRejected("No such order.", field="order_id")
        price = await self.reference_price(symbol)
        _validate_stop_replacement(order, symbol, stop_price, price)
        return _public_order(await self._broker.replace_price(order_id, stop_price=stop_price, limit_price=None))

    async def replace_target(self, order_id: str, symbol: str, limit_price: float) -> dict:
        symbol = symbol.upper()
        order = await self._store.get_order(self._user_id, order_id)
        if order is None:
            raise OrderRejected("No such order.", field="order_id")
        price = await self.reference_price(symbol)
        _validate_target_replacement(order, symbol, limit_price, price)
        return _public_order(await self._broker.replace_price(order_id, stop_price=None, limit_price=limit_price))

    async def close_position(self, symbol: str, qty: float | None = None) -> dict:
        symbol = symbol.upper()
        price = await self.reference_price(symbol)
        if price is None:
            raise OrderRejected(f"No current price available for {symbol}; cannot close.", field="symbol")
        result = await self._broker.close_position(symbol, qty, reference_price=price)
        public = _public_order(result)
        public["cancelled_orders"] = result["cancelled_orders"]
        public["rearmed_orders"] = result["rearmed_orders"]
        public["stop_lost"] = result["stop_lost"]
        return public

    async def reset(self) -> dict:
        await self._store.reset(self._user_id, self._settings.trading_sim_starting_cash)
        return await self.account()
