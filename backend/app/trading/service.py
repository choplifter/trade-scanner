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
from app.trading.errors import LiveTradingRefused, OrderRejected, TradingDisabled
from app.trading.models import OrderTicket, ResolvedOrder, resolve_ticket

logger = logging.getLogger(__name__)


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
        """
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        try:
            query = QueryOrderStatus(status)
        except ValueError:
            raise OrderRejected(f"Unknown order status: {status}", field="status") from None
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
        )


def _number(value) -> float | None:
    """Alpaca's decimal strings -> float, for arithmetic on this side."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
