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
contract still held past its expiry is settled the way a real account
settles it (app.trading.sim.settlement): out of the money it expires, an
in-the-money short put is assigned into 100 shares per contract at the
strike, an in-the-money short call behind held shares calls them away at
the strike, and everything else is settled in cash at intrinsic value.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time

from app.options.chain import LegQuote
from app.options.occ import try_parse_occ
from app.options.pricing import option_slippage
from app.options.quote_source import QuoteSource
from app.services.market_clock import ET
from app.trading.errors import OrderRejected
from app.trading.sim.broker import _apply_fill_to_position
from app.trading.sim.options_store import ASSIGNED, CANCELED, EXPIRED, FILLED, WORKING, SimOptionsStore
from app.trading.sim.settlement import Settlement, decide_settlement
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

    async def _apply_share_fill(self, underlying: str, order_id: str, side: str, qty: int, price: float, now: datetime) -> None:
        """The stock side of an assignment or a call-away: `qty` shares of
        `underlying` bought or sold at `price` (the strike), against the sim
        stock book -- the same arithmetic the equity broker uses, so an
        assigned lot adds to or averages into a held position exactly as a
        bought one would, and shares called away close their round trip in
        sim_trades with multiplier 1. A sim_orders row keeps the fill visible
        in the Orders list under the settlement order's id."""
        position = await self._sim_store.get_position(self._user_id, underlying)
        new_position, trade_row = _apply_fill_to_position(
            position,
            symbol=underlying,
            order_id=order_id,
            side=side,
            qty=float(qty),
            price=price,
            now=now.astimezone(UTC),
            initial_stop=None,
        )
        if new_position is None:
            if position is not None:
                await self._sim_store.delete_position(self._user_id, underlying)
        else:
            await self._sim_store.upsert_position(self._user_id, new_position)
        if trade_row is not None:
            trade_row["multiplier"] = 1
            await self._sim_store.insert_trade(self._user_id, trade_row)
        stamp = _utc_iso(now)
        await self._sim_store.insert_order(
            self._user_id,
            {
                "id": order_id,
                "parent_id": None,
                "oco_group_id": None,
                "leg_role": None,
                "client_order_id": None,
                "symbol": underlying,
                "side": side,
                "order_type": "market",
                "order_class": "simple",
                "qty": float(qty),
                "filled_qty": float(qty),
                "limit_price": None,
                "stop_price": None,
                "time_in_force": "day",
                "status": FILLED,
                "filled_avg_price": price,
                "submitted_at": stamp,
                "filled_at": stamp,
                "canceled_at": None,
            },
        )
        money = qty * price
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
        """Settle every contract past its expiry (16:00 ET on the expiry day)
        against the underlying's price, the way a real account is settled
        -- see app.trading.sim.settlement for the rules. Out of the money
        the contract expires; an in-the-money short put is assigned into
        shares at the strike, an in-the-money short call behind held shares
        calls them away, everything else is settled in cash at intrinsic.

        Returns the settlement orders written to the options book. Each
        carries a non-persisted "settlement" entry ({outcome, share_qty,
        share_price, share_order_id}) so a caller (the playbook runner, a
        log) can say what happened without re-deriving it."""
        positions = await self._store.list_positions(self._user_id)
        if not positions:
            return []
        now_et = now.astimezone(ET)
        settled: list[dict] = []
        # Short calls are settled after short puts on the same underlying so
        # a put assigned today does not cover a call expiring today: the two
        # are settled against the shares held going into the close.
        def settle_order(p: dict) -> int:
            parsed = try_parse_occ(p["symbol"])
            if p["side"] == "long" or parsed is None:
                return 0
            return 1 if parsed.kind == "put" else 2

        ordered = sorted(positions, key=settle_order)
        for position in ordered:
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
            shares = await self._sim_store.get_position(self._user_id, occ.underlying)
            shares_long = int(round(shares["qty"])) if shares is not None and shares["side"] == "long" else 0
            contracts = int(round(position["qty"]))
            for settlement in decide_settlement(
                side=position["side"], kind=occ.kind, strike=occ.strike, spot=float(spot), contracts=contracts, shares_long=shares_long
            ):
                order = await self._settle_one(occ, position["side"], settlement, quote_source.feed, now)
                settled.append(order)
                logger.info(
                    "Sim options settlement: %s x%d %s at %.2f (spot %.2f%s)",
                    occ.symbol, settlement.contracts, settlement.outcome, settlement.option_price, spot,
                    f", {settlement.share_side} {settlement.share_qty} shares at {settlement.share_price:.2f}" if settlement.moves_shares else "",
                )
                if settlement.moves_shares:
                    shares_long += settlement.share_qty if settlement.share_side == "buy" else -settlement.share_qty
        return settled

    async def _settle_one(self, occ, held_side: str, settlement: Settlement, feed: str, now: datetime) -> dict:
        """One settlement outcome written to the books: the option leg closed
        at the settlement's price, the shares moved if it moves any."""
        side = "sell" if held_side == "long" else "buy"
        value = settlement.option_price
        prefix = "assign" if settlement.moves_shares else "expiry"
        order_id = f"{prefix}:{occ.symbol}:{uuid.uuid4().hex[:8]}"
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
            "id": order_id,
            "client_order_id": None,
            "underlying": occ.underlying,
            # "expiry" for an out-of-the-money contract keeps older rows
            # meaningful; the other outcomes name themselves.
            "strategy": "expiry" if settlement.outcome == "expired" else settlement.outcome,
            "direction": "credit" if side == "sell" else "debit",
            "qty": settlement.contracts,
            "filled_qty": settlement.contracts,
            "limit_price": None,
            "legs": [leg.to_json()],
            "status": ASSIGNED if settlement.moves_shares else EXPIRED,
            "net_fill_price": value if side == "buy" else -value,
            "source": feed,
            "submitted_at": _utc_iso(now),
            "filled_at": _utc_iso(now),
            "canceled_at": None,
            "as_of": now.isoformat(),
        }
        await self._store.insert_order(self._user_id, order)
        await self._apply_leg_fill(occ.symbol, order_id, side, settlement.contracts, value, now)
        share_order_id = None
        if settlement.moves_shares and settlement.share_side and settlement.share_price is not None:
            share_order_id = f"{order_id}:shares"
            await self._apply_share_fill(
                occ.underlying, share_order_id, settlement.share_side, settlement.share_qty, settlement.share_price, now
            )
            account = await self._sim_store.get_account_row(self._user_id)
            if account is not None and account["cash"] < 0:
                logger.warning(
                    "Sim options settlement: cash is negative (%.2f) after %s of %d %s shares at %.2f",
                    account["cash"], settlement.outcome, settlement.share_qty, occ.underlying, settlement.share_price,
                )
        order["settlement"] = {
            "outcome": settlement.outcome,
            "share_side": settlement.share_side,
            "share_qty": settlement.share_qty,
            "share_price": settlement.share_price,
            "share_order_id": share_order_id,
        }
        return order
