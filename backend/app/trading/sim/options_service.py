"""The options service over the simulated book -- what /api/trading/sim/
options serves, and what the loops call to fill, settle and trigger.

A subclass of the real OptionsService: every read that does not need a
broker (the chain, the preview's pricing, risk, warnings and payoff, the
close preview) is inherited as is, over an injected QuoteSource -- Alpaca
now in Simulation mode, the replayed moment in a history replay. What the
real service asks the broker for (the account, the positions, the order)
comes from the sim stores here: the equity SimOrderService for cash and
shares, SimOptionsStore/SimOptionsBook for contracts and packages.

Positions are handed to the same classifier the live widget uses
(app.options.positions.group_spreads) in the shape Alpaca's positions
come in, so a sim bull put shows as "bull put x2" with one P&L exactly
like a paper one.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from app.alpaca.client import AlpacaClients
from app.core.config import Settings
from app.options.chain_fetch import ChainCache
from app.options.models import STRATEGY_LABELS, CloseLeg, CloseSpreadRequest, ResolvedSpread, SpreadTicket
from app.options.occ import try_parse_occ
from app.options.positions import SpreadGroup, group_spreads
from app.options.pricing import alpaca_limit
from app.options.quote_source import LiveQuoteSource, QuoteSource, ReplayQuoteSource
from app.options.service import OptionsService
from app.options.trigger_store import TriggerStore
from app.replay.options_engine import STALE_AFTER, ReplayOptionsEngine, ReplayOptionsEngineCache
from app.trading.errors import OrderRejected
from app.trading.sim.options_book import CONTRACT_MULTIPLIER, SimOptionsBook
from app.trading.sim.options_store import SimOptionsStore
from app.trading.sim.service import SimOrderService, _money, _number, _qty_str
from app.trading.sim.store import SimStore

logger = logging.getLogger(__name__)

SIM_ACCOUNT = "sim"
# The simulated book approves everything the ticket offers.
SIM_OPTIONS_LEVEL = 3


def public_option_order(row: dict) -> dict:
    """A sim_option_orders row in the Order shape the frontend already
    renders for Alpaca's multi-leg orders (legs nested), plus the book's
    own fields (strategy, direction, net fill, source)."""
    legs = row.get("legs") or []
    qty = int(row["qty"])
    net = row.get("net_fill_price")
    limit = row.get("limit_price")
    single = len(legs) == 1
    return {
        "id": row["id"],
        "client_order_id": row.get("client_order_id"),
        "symbol": legs[0]["symbol"] if single else row["underlying"],
        "asset_class": "us_option",
        "side": (legs[0]["side"] if single else ("buy" if row["direction"] == "debit" else "sell")),
        "order_type": "limit" if limit is not None else "market",
        "order_class": "simple" if single else "mleg",
        "qty": _qty_str(qty),
        "filled_qty": _qty_str(int(row.get("filled_qty") or 0)),
        "limit_price": _money(limit) if limit is not None else None,
        "stop_price": None,
        "filled_avg_price": _money(abs(net)) if net is not None else None,
        "status": row["status"],
        "time_in_force": "day",
        "submitted_at": row["submitted_at"],
        "created_at": row["submitted_at"],
        "filled_at": row.get("filled_at"),
        "legs": None
        if single
        else [
            {
                "id": f"{row['id']}:{i}",
                "symbol": leg["symbol"],
                "side": leg["side"],
                "position_intent": leg.get("position_intent"),
                "ratio_qty": str(leg.get("ratio_qty") or 1),
                "qty": _qty_str(qty * int(leg.get("ratio_qty") or 1)),
                "filled_qty": _qty_str(int(row.get("filled_qty") or 0) * int(leg.get("ratio_qty") or 1)),
                "filled_avg_price": _money(leg.get("fill_price")) if leg.get("fill_price") is not None else None,
                "status": row["status"],
            }
            for i, leg in enumerate(legs)
        ],
        "strategy": row.get("strategy"),
        "underlying": row.get("underlying"),
        "direction": row.get("direction"),
        "net_fill_price": net,
        "source": row.get("source"),
        "as_of": row.get("as_of"),
    }


def collateral_for(group: SpreadGroup) -> float:
    """What the book holds against an open credit package: the wider wing
    less the credit for a spread, the strike for a cash-secured put,
    nothing for a covered call or a debit (already paid)."""
    if group.qty <= 0 or group.net_entry >= 0 or group.strategy == "covered_call":
        return 0.0
    credit = -group.net_entry
    legs = group.legs
    if len(legs) == 1:
        leg = legs[0]
        if leg.kind == "put" and leg.qty < 0:
            return round(leg.strike * CONTRACT_MULTIPLIER * group.qty, 2)
        return 0.0
    puts = sorted(leg.strike for leg in legs if leg.kind == "put")
    calls = sorted(leg.strike for leg in legs if leg.kind == "call")
    if len(legs) == 2 and (len(puts) == 2 or len(calls) == 2):
        strikes = puts or calls
        width = strikes[-1] - strikes[0]
    elif len(puts) >= 2 and len(calls) >= 2:
        width = max(puts[-1] - puts[0], calls[-1] - calls[0])
    else:
        strikes = sorted(leg.strike for leg in legs)
        width = strikes[-1] - strikes[0]
    return round(max(0.0, width - credit) * CONTRACT_MULTIPLIER * group.qty, 2)


class SimOptionsService(OptionsService):
    def __init__(
        self,
        clients: AlpacaClients,
        settings: Settings,
        *,
        sim_store: SimStore,
        options_store: SimOptionsStore,
        user_id: int,
        source: QuoteSource,
        sim_service: SimOrderService,
        engine=None,
    ) -> None:
        super().__init__(clients, settings, engine=engine, account="paper", source=source)
        self._account = SIM_ACCOUNT  # type: ignore[assignment]
        self._sim_store = sim_store
        self._options_store = options_store
        self._user_id = user_id
        self._sim = sim_service
        self._book = SimOptionsBook(options_store, sim_store, user_id)

    @property
    def _trading(self):
        raise RuntimeError("the simulated options book never reaches a broker")

    @property
    def book(self) -> SimOptionsBook:
        return self._book

    def _now(self) -> datetime:
        return self._source.now()

    def _as_of(self) -> datetime | None:
        return getattr(self._source, "as_of", None)

    # --- reads --------------------------------------------------------------

    async def account(self) -> dict:
        base = await self._sim.account()
        marks = await self.marked_positions()
        option_value = sum(_number(p["market_value"]) or 0.0 for p in marks)
        equity = (_number(base.get("equity")) or 0.0) + option_value
        buying_power = _number(base.get("buying_power")) or 0.0
        reserved = sum(collateral_for(g) for g in await self._groups(marks))
        as_of = self._as_of()
        return {
            "account": SIM_ACCOUNT,
            "options_buying_power": round(max(0.0, buying_power - reserved), 2),
            "buying_power": round(buying_power, 2),
            "equity": round(equity, 2),
            "options_approved_level": SIM_OPTIONS_LEVEL,
            "options_trading_level": SIM_OPTIONS_LEVEL,
            "feed": self._source.feed,
            "replay_as_of": as_of.isoformat() if as_of is not None else None,
            "option_market_value": round(option_value, 2),
            "reserved_collateral": round(reserved, 2),
        }

    async def _shares_held(self, underlying: str) -> int:
        rows = await self._sim_store.list_positions(self._user_id)
        return sum(
            int(round(p["qty"])) for p in rows if p["symbol"].upper() == underlying.upper() and p["side"] == "long"
        )

    async def marked_positions(self) -> list[dict]:
        """Held contracts priced at the source's moment, in Alpaca's
        position shape (signed qty, money x 100)."""
        rows = await self._options_store.list_positions(self._user_id)
        if not rows:
            return []
        try:
            quotes = await self._source.leg_quotes([p["symbol"] for p in rows])
        except Exception:
            logger.exception("Sim option positions: quote fetch failed")
            quotes = {}
        out: list[dict] = []
        for p in rows:
            quote = quotes.get(p["symbol"])
            avg = float(p["avg_entry_price"])
            current = avg
            if quote is not None:
                if quote.mid is not None and quote.mid > 0:
                    current = float(quote.mid)
                elif quote.last is not None and quote.last > 0:
                    current = float(quote.last)
            qty = int(round(p["qty"]))
            direction = 1 if p["side"] == "long" else -1
            market_value = current * qty * CONTRACT_MULTIPLIER * direction
            cost_basis = avg * qty * CONTRACT_MULTIPLIER
            unrealized = (current - avg) * qty * CONTRACT_MULTIPLIER * direction
            plpc = (unrealized / cost_basis) if cost_basis > 0 else None
            plpc_str = f"{plpc:.6f}" if plpc is not None else None
            out.append(
                {
                    "symbol": p["symbol"],
                    "asset_class": "us_option",
                    "qty": _qty_str(qty * direction),
                    "side": p["side"],
                    "avg_entry_price": _money(avg),
                    "current_price": _money(current),
                    "market_value": _money(market_value),
                    "cost_basis": _money(cost_basis),
                    "unrealized_pl": _money(unrealized),
                    "unrealized_plpc": plpc_str,
                    "unrealized_intraday_pl": _money(unrealized),
                    "unrealized_intraday_plpc": plpc_str,
                    "asset_id": f"sim-{p['symbol']}",
                    "opened_at": p.get("opened_at"),
                    "last_at": quote.last_at.isoformat() if quote is not None and quote.last_at is not None else None,
                }
            )
        return out

    async def _groups(self, marks: list[dict]) -> list[SpreadGroup]:
        shares = [
            {"symbol": p["symbol"], "qty": p["qty"], "asset_class": "us_equity"}
            for p in await self._sim_store.list_positions(self._user_id)
            if p["side"] == "long"
        ]
        return group_spreads(marks, account=SIM_ACCOUNT, today=self._now().date(), equity_positions=shares)

    async def spreads(self) -> list[SpreadGroup]:
        return await self._groups(await self.marked_positions())

    async def orders(self, status: str = "open") -> list[dict]:
        return [public_option_order(r) for r in await self._options_store.list_orders(self._user_id, status)]

    # --- pricing ------------------------------------------------------------

    async def preview(self, ticket: SpreadTicket) -> ResolvedSpread:
        resolved = await super().preview(ticket)
        resolved.account = SIM_ACCOUNT
        # The default limit is the natural, so a submit with no limit typed
        # fills at once -- the mid would rest until the market came to it.
        if ticket.limit_price is None and resolved.net_natural is not None:
            resolved.limit_price = round(resolved.net_natural, 2)
            resolved.alpaca_limit_price = alpaca_limit(resolved.direction, resolved.limit_price)
        warnings: list[str] = []
        for warning in resolved.warnings:
            if warning.startswith("Expires today: Alpaca"):
                warnings.append("Expires today: the simulation settles it at intrinsic value at 16:00 ET.")
            elif warning.startswith("No greeks for at least one leg"):
                warnings.append("No greeks for at least one leg (no solvable implied volatility this close to expiry).")
            else:
                warnings.append(warning)
        now = self._now()
        for leg in resolved.legs:
            if leg.last_at is not None and now - leg.last_at.astimezone(now.tzinfo) > STALE_AFTER:
                warnings.append(
                    f"{leg.symbol}: last print {leg.last_at.astimezone(now.tzinfo):%H:%M} -- the replay fill uses a stale price."
                )
        resolved.warnings = warnings
        return resolved

    # --- writes -------------------------------------------------------------

    async def submit(self, ticket: SpreadTicket, confirm: str | None = None) -> dict:
        resolved = await self.preview(ticket)
        if resolved.coverage is not None and not resolved.coverage.ok:
            raise OrderRejected(
                f"{STRATEGY_LABELS[ticket.strategy]} is not covered: {resolved.coverage.need:,.0f} "
                f"{resolved.coverage.kind} needed, {resolved.coverage.have:,.0f} available",
                field="qty",
            )
        quotes = await self._source.leg_quotes([leg.symbol for leg in resolved.legs])
        order = await self._book.submit(
            legs=resolved.legs,
            qty=resolved.qty,
            direction=resolved.direction,
            limit_price=resolved.limit_price,
            underlying=resolved.underlying,
            strategy=resolved.strategy,
            client_order_id=resolved.client_order_id,
            quotes=quotes,
            now=self._now(),
            source=self._source.feed,
            as_of=self._as_of(),
        )
        return public_option_order(order)

    async def close_spread(
        self, req: CloseSpreadRequest, confirm: str | None = None, *, marketable: bool = False
    ) -> dict:
        """Close at the natural (no limit given, or a trigger firing), or
        rest a limit until the market reaches it."""
        legs, direction, _net_mid, _net_natural = await self._priced_close(req)
        parsed = try_parse_occ(legs[0].symbol)
        limit = None if (marketable or req.limit_price is None) else round(req.limit_price, 2)
        quotes = await self._source.leg_quotes([leg.symbol for leg in legs])
        order = await self._book.submit(
            legs=legs,
            qty=req.qty,
            direction=direction,
            limit_price=limit,
            underlying=parsed.underlying if parsed else legs[0].symbol,
            strategy="close",
            client_order_id=req.client_order_id,
            quotes=quotes,
            now=self._now(),
            source=self._source.feed,
            as_of=self._as_of(),
        )
        return public_option_order(order)

    async def close_contract(self, symbol: str, qty: int | None = None) -> dict:
        """The positions tab's close button on a held contract."""
        position = await self._options_store.get_position(self._user_id, symbol.upper())
        if position is None:
            raise OrderRejected(f"No open position in {symbol}.", field="symbol")
        held = int(round(position["qty"])) * (1 if position["side"] == "long" else -1)
        closing = abs(held) if qty is None else min(int(qty), abs(held))
        return await self.close_spread(CloseSpreadRequest(legs=[CloseLeg(symbol=symbol.upper(), qty=held)], qty=closing))

    async def cancel(self, order_id: str) -> dict:
        return public_option_order(await self._book.cancel(order_id, self._now()))


# --- wiring ---------------------------------------------------------------------


@dataclass
class OptionsWiring:
    """What the loops need to run the book for a user, bundled once in
    main.py: the stores, the per-user replay option engines, and the live
    chain cache / scanner engine for spot prices."""

    options_store: SimOptionsStore
    trigger_store: TriggerStore
    option_engines: ReplayOptionsEngineCache
    chain_cache: ChainCache | None = None
    scanner_engine: object | None = None


def replay_quote_source(
    clients: AlpacaClients | None,
    option_engines: ReplayOptionsEngineCache,
    user_id: int,
    replay_engine,
    as_of: datetime,
) -> ReplayQuoteSource:
    """The user's replayed option data at `as_of`, creating the engine on
    first use (its spot comes from the session's stock engine when the
    underlying is part of the session)."""
    engine = option_engines.get(user_id)
    if engine is None:

        async def spot_fn(underlying: str, at: datetime) -> float | None:
            return replay_engine.reference_price(underlying, at)

        engine = ReplayOptionsEngine(clients, spot_fn=spot_fn)
        option_engines.put(user_id, engine)
    return ReplayQuoteSource(engine, as_of)


def make_sim_options_service(
    clients: AlpacaClients,
    settings: Settings,
    *,
    sim_store: SimStore,
    options_store: SimOptionsStore,
    user_id: int,
    seam,
    option_engines: ReplayOptionsEngineCache | None,
    chain_cache: ChainCache | None,
    engine=None,
) -> SimOptionsService:
    """`seam` is (ReplayEngine, as_of) when the user is in a replay (see
    routers/trading_sim.py's _replay_seam), None for live prices."""
    if seam is not None and option_engines is not None:
        replay_engine, as_of = seam
        source: QuoteSource = replay_quote_source(clients, option_engines, user_id, replay_engine, as_of)
    else:
        live = OptionsService(clients, settings, engine=engine, chain_cache=chain_cache)
        source = live.source
    sim_service = SimOrderService(clients, settings, sim_store, user_id, engine=engine, replay=seam)
    return SimOptionsService(
        clients,
        settings,
        sim_store=sim_store,
        options_store=options_store,
        user_id=user_id,
        source=source,
        sim_service=sim_service,
        engine=engine,
    )
