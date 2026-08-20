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
from datetime import datetime, timezone
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

# The ranges the balance curve offers, each mapped to the Alpaca period and
# timeframe that serve it.
#
# Both halves are decided here rather than accepted from the client, because
# the valid timeframe *depends on* the period: anything longer than 30 days
# must be "1D", and asking for "1H" over 1M comes back as a 400 ("invalid
# timeframe provided: 1H. Valid timeframe for days > 30 is 1D"). Letting a
# caller supply the pair would make that constraint the UI's problem, and a
# wrong combination would surface as a broker error rather than a refusal.
#
# "all" is Alpaca's own keyword for the account's lifetime. It is preferred
# over a large fixed period like "10A" because the latter pads the series out
# to its full nominal length -- see _curve_points on the zero padding.
_HISTORY_RANGES: dict[str, tuple[str, str]] = {
    "1D": ("1D", "5Min"),
    "1W": ("1W", "15Min"),
    "1M": ("1M", "1D"),
    "3M": ("3M", "1D"),
    "1Y": ("1A", "1D"),
    "ALL": ("all", "1D"),
}

# Timeframes that sample once per session, so the newest point is the last
# *completed* day and today is missing entirely. See portfolio_history.
_DAILY_TIMEFRAMES = frozenset({"1D"})


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

        request = GetOrdersRequest(status=query, limit=_ORDER_FETCH_LIMIT)
        return _plain(await asyncio.to_thread(self._clients.trading.get_orders, request))

    async def portfolio_history(self, range_key: str = "1M") -> dict:
        """The account equity curve, for one of the ranges in _HISTORY_RANGES.

        Two corrections are applied to what Alpaca returns, both of which
        change the shape of the plotted line:

        *Leading zeros are dropped.* A period reaching back past the account's
        inception is padded with equity=0.0 for every earlier session rather
        than truncated, so a one-month curve on a two-week-old account arrives
        as eleven zeros followed by the real series. Plotted raw that draws a
        line starting at $0 and leaping to the opening balance -- an enormous
        gain that never happened.

        *A daily series is extended to now.* The "1D" timeframe samples once
        per session and stops at the last *completed* one, so the curve would
        end at yesterday's close while the Account tab showed today's equity:
        the same quantity in two places, disagreeing by a day of P&L.
        Appending the account's own current equity closes that with a real
        value rather than an extrapolation.
        """
        from alpaca.trading.requests import GetPortfolioHistoryRequest

        key = (range_key or "").upper()
        try:
            period, timeframe = _HISTORY_RANGES[key]
        except KeyError:
            raise OrderRejected(
                f"Unknown balance range: {range_key!r}. "
                f"Expected one of {', '.join(_HISTORY_RANGES)}.",
                field="range",
            ) from None

        request = GetPortfolioHistoryRequest(period=period, timeframe=timeframe)
        history = await asyncio.to_thread(self._clients.trading.get_portfolio_history, request)
        points = _curve_points(history)

        if timeframe in _DAILY_TIMEFRAMES:
            equity = _number((await self.account()).get("equity"))
            if equity is not None:
                points = _with_live_point(points, equity)

        start = points[0]["equity"] if points else None
        end = points[-1]["equity"] if points else None
        change = None if start is None or end is None else end - start
        return {
            "range": key,
            "timeframe": timeframe,
            "points": points,
            "start_equity": start,
            "end_equity": end,
            "change": change,
            # Measured against the first *plotted* point rather than Alpaca's
            # base_value, which belongs to the untrimmed series and can sit
            # inside the zero-padded region -- dividing by a balance the
            # account never held.
            "change_pct": (change / start * 100.0) if change is not None and start else None,
        }

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


def _curve_points(history) -> list[dict]:
    """PortfolioHistory's parallel arrays -> plottable points, padding removed.

    Alpaca returns timestamp/equity/profit_loss/profit_loss_pct as separate
    lists to be read positionally. Only the *leading* run of zero-equity
    samples is dropped -- those are the pre-inception padding described in
    OrderService.portfolio_history. A zero after the account has held funds
    would be a real (if catastrophic) balance, so the scan stops at the first
    non-zero sample instead of filtering zeros out of the whole series.
    """
    timestamps = list(getattr(history, "timestamp", None) or [])
    equity = list(getattr(history, "equity", None) or [])
    profit_loss = list(getattr(history, "profit_loss", None) or [])
    profit_loss_pct = list(getattr(history, "profit_loss_pct", None) or [])

    first_funded = 0
    while first_funded < len(equity) and not equity[first_funded]:
        first_funded += 1

    def at(values: list, index: int) -> float | None:
        if index >= len(values) or values[index] is None:
            return None
        return float(values[index])

    points: list[dict] = []
    for i in range(first_funded, min(len(timestamps), len(equity))):
        value = at(equity, i)
        if value is None:
            continue
        pct = at(profit_loss_pct, i)
        points.append(
            {
                "t": int(timestamps[i]),
                "equity": value,
                "profit_loss": at(profit_loss, i),
                # Alpaca reports this as a fraction, the same way a position's
                # unrealized_plpc is a fraction -- see signedPct in the
                # frontend's TradingWidget. Converted once, here.
                "profit_loss_pct": None if pct is None else pct * 100.0,
            }
        )
    return points


def _with_live_point(points: list[dict], equity: float) -> list[dict]:
    """Append the account's current equity so a daily curve reaches today.

    Timestamped now rather than at the next session boundary: it is what the
    balance *is* at the moment of the request, and dating it forward would put
    a point in the future, which the chart would happily draw.
    """
    now = int(datetime.now(timezone.utc).timestamp())
    if points and now <= points[-1]["t"]:
        return points
    previous = points[-1]["equity"] if points else None
    change = None if previous is None else equity - previous
    return [
        *points,
        {
            "t": now,
            "equity": equity,
            "profit_loss": change,
            "profit_loss_pct": None if not previous else change / previous * 100.0,
        },
    ]


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
