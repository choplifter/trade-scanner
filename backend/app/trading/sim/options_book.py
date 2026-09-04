"""The matching engine of the simulated options book -- SimBroker for
packages of contracts.

A package (one contract, a vertical, a condor...) fills as one thing at
the *natural* price: every bought leg at its ask, every sold leg at its
bid, the way a marketable order actually fills. A limit rests when the
natural is worse than it and is checked again on every tick of whichever
loop owns the user's clock (app.trading.sim.loop live, app.replay.loop
in a replay). No partial fills. Where a leg has no quote on the side it
needs (a one-sided live quote, or a replayed bar close -- see
app.replay.options_engine), its mid or last plus a slippage stands in
(app.options.pricing.option_slippage).

Positions are per contract, in the exact shape the equity broker keeps,
so app.trading.sim.broker._apply_fill_to_position -- the pure arithmetic
of entries, exits and flips -- is reused unchanged; the contract
multiplier is applied to cash and to the round trip's P&L here. A
contract still held past its expiry is settled at intrinsic value against
the underlying (no assignment into shares).
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time

from app.options.chain import LegQuote
from app.options.occ import try_parse_occ
from app.options.payoff import intrinsic
from app.options.pricing import option_slippage
from app.options.quote_source import QuoteSource
from app.services.market_clock import ET
from app.trading.errors import OrderRejected
from app.trading.sim.broker import _apply_fill_to_position
from app.trading.sim.options_store import CANCELED, EXPIRED, FILLED, WORKING, SimOptionsStore
from app.trading.sim.store import SimStore

logger = logging.getLogger(__name__)

CONTRACT_MULTIPLIER = 100
# Contracts settle at the close of their expiry day.
SETTLE_AT = time(16, 0)


@dataclass
class BookLeg:
    """One leg of a package as the book stores it (a SpreadLeg without its
    quotes; the fill price joins it once filled)."""

    symbol: str
    kind: str
    strike: float
    expiry: date
    side: str
    ratio_qty: int = 1
    position_intent: str = "buy_to_open"
    fill_price: float | None = None

    @classmethod
    def from_spread_leg(cls, leg) -> "BookLeg":
        return cls(
            symbol=leg.symbol,
            kind=leg.kind,
            strike=float(leg.strike),
            expiry=leg.expiry,
            side=leg.side,
            ratio_qty=int(getattr(leg, "ratio_qty", 1) or 1),
            position_intent=getattr(leg, "position_intent", "buy_to_open" if leg.side == "buy" else "sell_to_open"),
        )

    @classmethod
    def from_json(cls, d: dict) -> "BookLeg":
        expiry = d.get("expiry")
        return cls(
            symbol=d["symbol"],
            kind=d.get("kind", "call"),
            strike=float(d.get("strike") or 0),
            expiry=date.fromisoformat(expiry) if isinstance(expiry, str) else expiry,
            side=d["side"],
            ratio_qty=int(d.get("ratio_qty") or 1),
            position_intent=d.get("position_intent") or ("buy_to_open" if d["side"] == "buy" else "sell_to_open"),
            fill_price=d.get("fill_price"),
        )

    def to_json(self) -> dict:
        return {
            "symbol": self.symbol,
            "kind": self.kind,
            "strike": self.strike,
            "expiry": self.expiry.isoformat() if isinstance(self.expiry, date) else self.expiry,
            "side": self.side,
            "ratio_qty": self.ratio_qty,
            "position_intent": self.position_intent,
            "fill_price": self.fill_price,
        }


def leg_fill_price(quote: LegQuote | None, side: str) -> float | None:
    """What one leg fills at: the ask when buying, the bid when selling;
    without that side, the mid (else the last) a slippage the wrong way."""
    if quote is None:
        return None
    if side == "buy":
        if quote.ask is not None and quote.ask > 0:
            return round(float(quote.ask), 4)
    elif quote.bid is not None and quote.bid > 0:
        return round(float(quote.bid), 4)
    base = quote.mid if quote.mid is not None and quote.mid > 0 else quote.last
    if base is None or base <= 0:
        return None
    slip = option_slippage(float(base))
    if side == "buy":
        return round(float(base) + slip, 4)
    return round(max(0.01, float(base) - slip), 4)


def net_fill(legs: list[BookLeg], quotes: dict[str, LegQuote]) -> tuple[float | None, dict[str, float]]:
    """The package's natural fill: (signed net per spread -- positive paid,
    negative received -- and each leg's price). None when a leg cannot be
    priced: a package with a hole in it does not fill."""
    total = 0.0
    per_leg: dict[str, float] = {}
    for leg in legs:
        price = leg_fill_price(quotes.get(leg.symbol), leg.side)
        if price is None:
            return None, {}
        per_leg[leg.symbol] = price
        total += (price if leg.side == "buy" else -price) * leg.ratio_qty
    return round(total, 4), per_leg


# Half a cent: a limit typed from a natural shown in cents must fill at
# that natural, whatever the fourth decimal of the legs' prices says.
_LIMIT_TOLERANCE = 0.005


def limit_allows(direction: str, limit_price: float | None, signed_net: float) -> bool:
    """Whether the natural net is within the limit: a debit pays at most
    the limit, a credit receives at least it. No limit is a market order."""
    if limit_price is None:
        return True
    if direction == "debit":
        return signed_net <= limit_price + _LIMIT_TOLERANCE
    return -signed_net >= limit_price - _LIMIT_TOLERANCE


def _utc_iso(now: datetime) -> str:
    return now.astimezone(UTC).isoformat()


class SimOptionsBook:
    def __init__(self, store: SimOptionsStore, sim_store: SimStore, user_id: int) -> None:
        self._store = store
        self._sim_store = sim_store
        self._user_id = user_id

    # --- submit ---------------------------------------------------------------

    async def submit(
        self,
        *,
        legs: list,
        qty: int,
        direction: str,
        limit_price: float | None,
        underlying: str,
        strategy: str,
        client_order_id: str | None,
        quotes: dict[str, LegQuote],
        now: datetime,
        source: str,
        as_of: datetime | None = None,
    ) -> dict:
        """Book a package: fills at once when the natural is within the
        limit, rests otherwise. `legs` are SpreadLegs or BookLegs."""
        if qty <= 0:
            raise OrderRejected("Quantity must be positive.", field="qty")
        book_legs = [leg if isinstance(leg, BookLeg) else BookLeg.from_spread_leg(leg) for leg in legs]
        if not book_legs:
            raise OrderRejected("A package needs at least one leg.", field="legs")
        order = {
            "id": str(uuid.uuid4()),
            "client_order_id": client_order_id,
            "underlying": underlying.upper(),
            "strategy": strategy,
            "direction": direction,
            "qty": int(qty),
            "filled_qty": 0,
            "limit_price": round(limit_price, 4) if limit_price is not None else None,
            "legs": [leg.to_json() for leg in book_legs],
            "status": WORKING,
            "net_fill_price": None,
            "source": source,
            "submitted_at": _utc_iso(now),
            "filled_at": None,
            "canceled_at": None,
            "as_of": as_of.isoformat() if as_of is not None else None,
        }
        await self._store.insert_order(self._user_id, order)
        await self._try_fill(order, quotes, now)
        return await self._store.get_order(self._user_id, order["id"])  # type: ignore[return-value]

    async def _try_fill(self, order: dict, quotes: dict[str, LegQuote], now: datetime) -> bool:
        legs = [BookLeg.from_json(d) for d in order["legs"]]
        signed_net, per_leg = net_fill(legs, quotes)
        if signed_net is None or not limit_allows(order["direction"], order.get("limit_price"), signed_net):
            return False
        await self._fill(order, legs, per_leg, signed_net, now)
        return True

    async def _fill(self, order: dict, legs: list[BookLeg], per_leg: dict[str, float], signed_net: float, now: datetime) -> None:
        qty = int(order["qty"])
        for leg in legs:
            price = per_leg[leg.symbol]
            contracts = qty * leg.ratio_qty
            await self._apply_leg_fill(leg.symbol, order["id"], leg.side, contracts, price, now)
            leg.fill_price = price
        await self._store.update_order(
            self._user_id,
            order["id"],
            status=FILLED,
            filled_qty=qty,
            net_fill_price=signed_net,
            filled_at=_utc_iso(now),
            legs=[leg.to_json() for leg in legs],
        )
        logger.info(
            "Sim options fill: %s %s x%d %s net %+.2f (%s)",
            order.get("strategy"), order.get("underlying"), qty, "/".join(l.symbol for l in legs), signed_net, order.get("source"),
        )

    async def _apply_leg_fill(self, symbol: str, order_id: str, side: str, contracts: int, price: float, now: datetime) -> None:
        position = await self._store.get_position(self._user_id, symbol)
        new_position, trade_row = _apply_fill_to_position(
            position,
            symbol=symbol,
            order_id=order_id,
            side=side,
            qty=float(contracts),
            price=price,
            now=now.astimezone(UTC),
            initial_stop=None,
        )
        if new_position is None:
            if position is not None:
                await self._store.delete_position(self._user_id, symbol)
        else:
            await self._store.upsert_position(self._user_id, new_position)
        if trade_row is not None:
            # The round trip in dollars: per share x 100, like the real
            # trade record (app.trading.trades.contract_multiplier).
            trade_row["pnl"] = round(trade_row["pnl"] * CONTRACT_MULTIPLIER, 2)
            trade_row["multiplier"] = CONTRACT_MULTIPLIER
            await self._sim_store.insert_trade(self._user_id, trade_row)
        money = contracts * price * CONTRACT_MULTIPLIER
        await self._sim_store.add_cash(self._user_id, money if side == "sell" else -money)

    # --- resting orders --------------------------------------------------------

    async def check_fills(self, quote_source: QuoteSource, now: datetime) -> list[dict]:
        """Price every resting package against the source and fill the ones
        whose natural is now within their limit."""
        working = await self._store.working_orders(self._user_id)
        if not working:
            return []
        symbols = sorted({leg["symbol"] for order in working for leg in order["legs"]})
        try:
            quotes = await quote_source.leg_quotes(symbols)
        except Exception:
            logger.exception("Sim options fill check: quote fetch failed")
            return []
        filled: list[dict] = []
        for order in working:
            if await self._try_fill(order, quotes, now):
                filled.append(await self._store.get_order(self._user_id, order["id"]))  # type: ignore[arg-type]
        return filled

    async def cancel(self, order_id: str, now: datetime) -> dict:
        order = await self._store.get_order(self._user_id, order_id)
        if order is None or order["status"] != WORKING:
            raise OrderRejected("No such working order.", field="order_id")
        await self._store.update_order(self._user_id, order_id, status=CANCELED, canceled_at=_utc_iso(now))
        return await self._store.get_order(self._user_id, order_id)  # type: ignore[return-value]

    # --- expiry ----------------------------------------------------------------

    async def settle_expired(self, quote_source: QuoteSource, now: datetime) -> list[dict]:
        """Close every contract past its expiry (16:00 ET on the expiry day)
        at intrinsic value against the underlying's price -- what an
        exercised or expired contract is worth, without the shares."""
        positions = await self._store.list_positions(self._user_id)
        if not positions:
            return []
        now_et = now.astimezone(ET)
        settled: list[dict] = []
        for position in positions:
            occ = try_parse_occ(position["symbol"])
            if occ is None:
                continue
            past = occ.expiry < now_et.date() or (occ.expiry == now_et.date() and now_et.time() >= SETTLE_AT)
            if not past:
                continue
            try:
                spot = await quote_source.spot(occ.underlying)
            except Exception:
                logger.exception("Sim options settlement: no spot for %s", occ.underlying)
                continue
            if spot is None:
                continue
            value = round(intrinsic(occ.kind, float(spot), occ.strike), 4)
            side = "sell" if position["side"] == "long" else "buy"
            contracts = int(round(position["qty"]))
            leg = BookLeg(
                symbol=occ.symbol,
                kind=occ.kind,
                strike=occ.strike,
                expiry=occ.expiry,
                side=side,
                ratio_qty=1,
                position_intent="sell_to_close" if side == "sell" else "buy_to_close",
                fill_price=value,
            )
            order = {
                "id": f"expiry:{occ.symbol}:{uuid.uuid4().hex[:8]}",
                "client_order_id": None,
                "underlying": occ.underlying,
                "strategy": "expiry",
                "direction": "credit" if side == "sell" else "debit",
                "qty": contracts,
                "filled_qty": contracts,
                "limit_price": None,
                "legs": [leg.to_json()],
                "status": EXPIRED,
                "net_fill_price": value if side == "buy" else -value,
                "source": quote_source.feed,
                "submitted_at": _utc_iso(now),
                "filled_at": _utc_iso(now),
                "canceled_at": None,
                "as_of": now.isoformat(),
            }
            await self._store.insert_order(self._user_id, order)
            await self._apply_leg_fill(occ.symbol, order["id"], side, contracts, value, now)
            settled.append(order)
            logger.info("Sim options settlement: %s x%d at intrinsic %.2f (spot %.2f)", occ.symbol, contracts, value, spot)
        return settled
