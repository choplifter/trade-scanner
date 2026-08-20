"""Account state and order placement, over Alpaca's blocking TradingClient.

Read paths (account/positions/orders) are unguarded -- looking at an account
is harmless whichever account it is. Write paths are guarded twice, and the
guards live *here* rather than in the router so that a future caller (a Dash
page, a scheduled job, a test harness) cannot reach the broker without passing
them.

alpaca-py is a synchronous requests-based SDK, so every call goes through
asyncio.to_thread -- the same convention app.alpaca.universe uses for
get_all_assets/get_asset/get_corporate_announcements.
"""

import asyncio
import logging
from typing import Any

from app.alpaca.client import AlpacaClients
from app.core.config import Settings
from app.trading.errors import (
    LiveTradingRefused,
    OrderRejected,
    TradingDisabled,
    rejection_from_api_error,
)
from app.trading.models import OrderTicket, ResolvedOrder, resolve_ticket

logger = logging.getLogger(__name__)

# Statuses in which an order can still fill or still be cancelled -- i.e.
# what a "working orders" table means by open. Taken from Alpaca's order
# lifecycle rather than from its OPEN query filter, which omits `held`; see
# OrderService.orders for why that distinction cost a visible stop-loss.
_WORKING_STATUSES = frozenset(
    {
        "new",
        "accepted",
        "accepted_for_bidding",
        "pending_new",
        "partially_filled",
        "held",
        "pending_cancel",
        "pending_replace",
        "calculated",
        "suspended",
        "stopped",
    }
)

# Upper bound on the history pulled to find those working orders. Alpaca caps
# this at 500 per request; an account with more than 500 orders in its recent
# history has enough churn that paging here would be the wrong fix anyway.
_ORDER_FETCH_LIMIT = 500


def _plain(obj: Any) -> Any:
    """Alpaca SDK model -> JSON-safe plain data.

    The SDK returns pydantic models whose fields include Decimals, UUIDs and
    enums, none of which survive orjson untouched. model_dump(mode="json")
    handles all three; the fallbacks cover the SDK's occasional plain dicts
    and any field that slips through as a raw object.
    """
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, list):
        return [_plain(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        try:
            return dump(mode="json")
        except TypeError:  # older/oddly-typed models
            return {k: _plain(v) for k, v in dump().items()}
    return str(obj)


class OrderService:
    """Everything the dashboard needs from the trading side of Alpaca."""

    def __init__(self, clients: AlpacaClients, settings: Settings, engine=None) -> None:
        self._clients = clients
        self._settings = settings
        # The scanner engine already holds a live last price for every ranked
        # symbol, refreshed every poll tick. Reusing it costs nothing; the
        # Alpaca fallback below covers symbols outside the ranked views.
        self._engine = engine

    # --- read paths ---------------------------------------------------

    async def account(self) -> dict:
        return _plain(await asyncio.to_thread(self._clients.trading.get_account))

    async def positions(self) -> list[dict]:
        return _plain(await asyncio.to_thread(self._clients.trading.get_all_positions))

    async def orders(self, status: str = "open") -> list[dict]:
        """Orders by status. Defaults to open, which is what a working-orders
        table wants; "all" or "closed" cover the history view.

        "open" is NOT delegated to Alpaca's own OPEN filter, because that
        filter omits `held` -- and `held` is exactly where a bracket parks
        its stop-loss leg while the take-profit leg sits at `new`. Asking
        Alpaca for open orders on a filled bracket therefore returns the
        target and hides the stop, which made the UI report "no stop" for a
        position that had one (observed on QDEL: stop at 13.80, held, absent
        from the OPEN response while the 14.80 target came back `new`).

        So: fetch everything and keep the statuses that are still live. The
        list is bounded by _ORDER_FETCH_LIMIT rather than unbounded, since
        "all" on a busy account is mostly filled history nobody asked for.
        """
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        try:
            query = QueryOrderStatus(status)
        except ValueError:
            raise OrderRejected(f"Unknown order status: {status}", field="status") from None

        if query is QueryOrderStatus.OPEN:
            request = GetOrdersRequest(status=QueryOrderStatus.ALL, limit=_ORDER_FETCH_LIMIT)
            rows = _plain(await asyncio.to_thread(self._clients.trading.get_orders, request))
            return [o for o in rows if str(o.get("status", "")).lower() in _WORKING_STATUSES]

        request = GetOrdersRequest(status=query)
        return _plain(await asyncio.to_thread(self._clients.trading.get_orders, request))

    # --- guards -------------------------------------------------------

    def _assert_can_trade(self) -> None:
        """Every write path starts here.

        Two independent switches, both of which must be on. trading_enabled
        exists so that merging this feature changes nothing until someone
        deliberately turns it on; the paper check exists so that turning it on
        still cannot reach real money.
        """
        if not self._settings.trading_enabled:
            raise TradingDisabled(
                "Trading is switched off. Set TRADING_ENABLED=true in backend/.env to enable it."
            )
        if not self._settings.alpaca_paper:
            raise LiveTradingRefused(
                "Refusing to place an order against a live account. This build is "
                "paper-only: ALPACA_PAPER must be true."
            )

    # --- pricing ------------------------------------------------------

    async def reference_price(self, symbol: str) -> float | None:
        """The price a market order should be sized and bounds-checked at.

        Server-side by design. The client displays a price too, but letting a
        client-supplied number reach the ceilings would make them advisory --
        a wrong or stale price could size an order past every limit.
        """
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
            # Not fatal: resolve_ticket refuses cleanly when the price is
            # None, which is a better message than a 502 here would be.
            logger.warning("No reference price available for %s", symbol, exc_info=True)
            return None

    # --- preview ------------------------------------------------------

    async def preview(self, ticket: OrderTicket) -> ResolvedOrder:
        """What this ticket would become, without placing anything.

        Deliberately ungated: a preview is arithmetic, and being able to see
        the size and risk of an order you are not allowed to place is useful
        rather than dangerous.
        """
        account = await self.account()
        price = await self.reference_price(ticket.symbol.upper())
        return resolve_ticket(
            ticket,
            reference_price=price,
            equity=_number(account.get("equity")),
            buying_power=_number(account.get("buying_power")),
            max_qty=self._settings.trading_max_order_qty,
            max_notional=self._settings.trading_max_order_notional,
            max_notional_pct=self._settings.trading_max_order_notional_pct,
        )

    # --- write path ---------------------------------------------------

    async def submit(self, ticket: OrderTicket) -> dict:
        """Place an order. The only function here that can lose money.

        Guard first, before anything is built or fetched, so a refusal costs
        nothing and cannot be reached by a caller that skipped the route.
        """
        self._assert_can_trade()
        resolved = await self.preview(ticket)
        request = _build_request(resolved)

        try:
            order = await asyncio.to_thread(self._clients.trading.submit_order, request)
        except Exception as exc:
            rejection = rejection_from_api_error(exc)
            if rejection is not None:
                raise rejection from exc
            raise

        logger.info(
            "Submitted %s %s %s x%d (%s) client_order_id=%s",
            resolved.order_class,
            resolved.side,
            resolved.symbol,
            resolved.qty,
            resolved.order_type,
            resolved.client_order_id,
        )
        return _plain(order)

    async def cancel(self, order_id: str) -> None:
        self._assert_can_trade()
        try:
            await asyncio.to_thread(self._clients.trading.cancel_order_by_id, order_id)
        except Exception as exc:
            rejection = rejection_from_api_error(exc)
            if rejection is not None:
                raise rejection from exc
            raise

    async def close_position(self, symbol: str) -> dict:
        """Flatten one position at market, cancelling what holds it first.

        Deliberately per-symbol: a close-everything button is exactly the one
        hit by accident.

        The cancel step is not optional. A bracket or OTO entry leaves its
        stop resting against the whole position, and Alpaca counts those
        shares as held -- so closing without cancelling fails with
        "insufficient qty available for order (requested: 233, available: 0)",
        which reads like the position does not exist. Flattening has to mean
        flattening, so the resting orders go first.
        """
        self._assert_can_trade()
        symbol = symbol.upper()

        cancelled = await self._cancel_orders_for(symbol)

        try:
            order = await asyncio.to_thread(self._clients.trading.close_position, symbol)
        except Exception as exc:
            rejection = rejection_from_api_error(exc)
            if rejection is not None:
                raise rejection from exc
            raise
        result = _plain(order)
        if isinstance(result, dict):
            result["cancelled_orders"] = cancelled
        return result

    async def _cancel_orders_for(self, symbol: str) -> list[str]:
        """Cancel every working order on one symbol. Returns what was cancelled.

        Best-effort per order: one that has already filled or been cancelled
        between listing and cancelling is not a failure worth aborting the
        close for -- the close is the thing the user asked for.
        """
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        try:
            request = GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol])
            orders = await asyncio.to_thread(self._clients.trading.get_orders, request)
        except Exception:
            logger.exception("Could not list working orders for %s before closing", symbol)
            return []

        cancelled: list[str] = []
        for order in orders or []:
            order_id = str(getattr(order, "id", "") or "")
            if not order_id:
                continue
            try:
                await asyncio.to_thread(self._clients.trading.cancel_order_by_id, order_id)
                cancelled.append(order_id)
            except Exception:
                logger.warning(
                    "Could not cancel %s on %s before closing", order_id, symbol, exc_info=True
                )
        return cancelled


def _number(value) -> float | None:
    """Alpaca's decimal strings -> float, for arithmetic on this side."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_request(resolved):
    """ResolvedOrder -> the alpaca-py request object.

    Kept apart from submit() so the mapping is testable without a client:
    the bracket/OTO leg construction is the part most likely to be wrong,
    and it is pure.
    """
    from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
    from alpaca.trading.requests import (
        LimitOrderRequest,
        MarketOrderRequest,
        StopLossRequest,
        TakeProfitRequest,
    )

    kwargs = {
        "symbol": resolved.symbol,
        "qty": resolved.qty,
        "side": OrderSide.BUY if resolved.side == "buy" else OrderSide.SELL,
        "time_in_force": TimeInForce(resolved.time_in_force),
        "order_class": {
            "simple": OrderClass.SIMPLE,
            "oto": OrderClass.OTO,
            "bracket": OrderClass.BRACKET,
        }[resolved.order_class],
    }
    if resolved.client_order_id:
        kwargs["client_order_id"] = resolved.client_order_id
    if resolved.take_profit_price is not None:
        kwargs["take_profit"] = TakeProfitRequest(limit_price=resolved.take_profit_price)
    if resolved.stop_loss_price is not None:
        kwargs["stop_loss"] = StopLossRequest(stop_price=resolved.stop_loss_price)

    if resolved.order_type == "limit":
        return LimitOrderRequest(limit_price=resolved.limit_price, **kwargs)
    return MarketOrderRequest(**kwargs)
