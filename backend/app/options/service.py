"""Everything the Options widget needs from Alpaca, per account.

Mirrors app.trading.service.OrderService in shape and posture: reads are
ungated, every write starts at the guard, every SDK call goes through
asyncio.to_thread, SDK imports are function-local, and the request
builder is a pure function with its own tests.
"""

import asyncio
import logging

from app.alpaca.client import AlpacaClients
from app.core.config import Settings
from app.options.chain import Chain
from app.options.chain_fetch import ChainCache
from app.options.guards import assert_options_level
from app.options.models import (
    STRATEGY_LABELS,
    TIME_STRATEGIES,
    Coverage,
    Payoff,
    PayoffRequest,
    CloseSpreadRequest,
    ResolvedSpread,
    SpreadLeg,
    SpreadTicket,
    closing_legs,
    options_level_required,
    resolve_legs,
)
from app.options.occ import try_parse_occ
from app.options.payoff import PayoffLeg, payoff_curve
from app.options.positions import SpreadGroup, group_spreads
from app.options.quote_source import LiveQuoteSource, QuoteSource
from app.options.pricing import (
    alpaca_limit,
    assert_spread_within_limits,
    marketable_close_limit,
    net_price,
    spread_risk,
)
from app.trading.errors import OrderRejected, rejection_from_api_error
from app.trading.guards import Account, assert_can_trade, limits_for
from app.trading.service import OrderService, _number, _plain

logger = logging.getLogger(__name__)

# A market wider than this fraction of its own mid gets a warning on the
# preview: a mid-priced limit on a 1.00/1.60 contract is not a fill.
_WIDE_MARKET_FRACTION = 0.25


def build_mleg_request(legs: list[SpreadLeg], qty: int, alpaca_limit_price: float, client_order_id: str | None):
    """The multi-leg order. Pure and testable without a client, like
    app.trading.service._build_request. Options at Alpaca are day orders,
    and a multi-leg order must be a limit; the sign of the limit says
    debit (+) or credit (-)."""
    from alpaca.trading.enums import OrderClass, OrderSide, PositionIntent, TimeInForce
    from alpaca.trading.requests import LimitOrderRequest, OptionLegRequest

    kwargs = {}
    if client_order_id:
        kwargs["client_order_id"] = client_order_id
    return LimitOrderRequest(
        qty=qty,
        order_class=OrderClass.MLEG,
        time_in_force=TimeInForce.DAY,
        limit_price=alpaca_limit_price,
        legs=[
            OptionLegRequest(
                symbol=leg.symbol,
                ratio_qty=leg.ratio_qty,
                side=OrderSide(leg.side),
                position_intent=PositionIntent(leg.position_intent),
            )
            for leg in legs
        ],
        **kwargs,
    )


def build_single_leg_request(leg: SpreadLeg, qty: int, limit_price: float, client_order_id: str | None):
    """A plain option order: a long call/put opened outright, or a broken
    spread down to one contract being closed -- the SDK refuses MLEG with
    fewer than two legs."""
    from alpaca.trading.enums import OrderSide, PositionIntent, TimeInForce
    from alpaca.trading.requests import LimitOrderRequest

    kwargs = {}
    if client_order_id:
        kwargs["client_order_id"] = client_order_id
    return LimitOrderRequest(
        symbol=leg.symbol,
        qty=qty,
        side=OrderSide(leg.side),
        position_intent=PositionIntent(leg.position_intent),
        time_in_force=TimeInForce.DAY,
        limit_price=round(abs(limit_price), 2),
        **kwargs,
    )


class OptionsService:
    def __init__(
        self,
        clients: AlpacaClients,
        settings: Settings,
        engine=None,
        chain_cache: ChainCache | None = None,
        account: Account = "paper",
        source: QuoteSource | None = None,
        broker=None,
        live_available: bool | None = None,
    ) -> None:
        """`source` is where prices and the clock come from (see
        app.options.quote_source): Alpaca now by default, a replayed
        moment for the simulated book. `broker` is the user's own
        TradingClient (app.broker.resolver), None the operator's from .env;
        `live_available` whether that user has a live pair, for the guard."""
        self._clients = clients
        self._settings = settings
        self._broker = broker
        self._live_available = live_available
        self._engine = engine
        self._account: Account = account
        self._chain_cache = chain_cache or ChainCache(clients, self._live_spot)
        self._source: QuoteSource = source or LiveQuoteSource(clients, self._chain_cache, self._live_spot)

    @property
    def source(self) -> QuoteSource:
        return self._source

    @property
    def account_name(self) -> Account:
        return self._account

    @property
    def _trading(self):
        if self._broker is not None:
            return self._broker
        if self._account == "paper":
            return self._clients.trading
        return self._clients.trading_for(self._account)

    # --- reads --------------------------------------------------------------

    async def account(self) -> dict:
        raw = _plain(await asyncio.to_thread(self._trading.get_account)) or {}
        level = raw.get("options_trading_level")
        if level is None:
            level = raw.get("options_approved_level")
        return {
            "account": self._account,
            "options_buying_power": _number(raw.get("options_buying_power")),
            "buying_power": _number(raw.get("buying_power")),
            "equity": _number(raw.get("equity")),
            "options_approved_level": raw.get("options_approved_level"),
            "options_trading_level": level,
        }

    async def spot(self, underlying: str) -> float | None:
        """The underlying's last price as of the source's moment."""
        return await self._source.spot(underlying.upper())

    async def _live_spot(self, underlying: str) -> float | None:
        """Alpaca now: the scanner engine's row first, the latest trade
        otherwise, exactly as the equity ticket does."""
        try:
            return await OrderService(self._clients, self._settings, engine=self._engine).reference_price(
                underlying.upper()
            )
        except Exception:
            logger.debug("No spot for %s", underlying, exc_info=True)
            return None

    async def expiries(self, underlying: str) -> dict:
        try:
            spot, expiries = await self._source.expiries(underlying)
        except LookupError as exc:
            raise OrderRejected(str(exc), field="underlying") from exc
        return {
            "underlying": underlying.upper(),
            "spot": spot,
            "expiries": [e.to_dict() for e in expiries],
        }

    async def chain(self, underlying: str, expiry) -> Chain:
        try:
            return await self._source.chain(underlying, expiry)
        except LookupError as exc:
            raise OrderRejected(str(exc), field="expiry") from exc

    async def spreads(self) -> list[SpreadGroup]:
        positions = _plain(await asyncio.to_thread(self._trading.get_all_positions)) or []
        return group_spreads(positions, account=self._account, equity_positions=positions)

    async def _shares_held(self, underlying: str) -> int:
        positions = _plain(await asyncio.to_thread(self._trading.get_all_positions)) or []
        return sum(
            int(round(_number(p.get("qty")) or 0))
            for p in positions
            if (p.get("symbol") or "").upper() == underlying.upper()
            and (p.get("asset_class") or "us_equity").lower() == "us_equity"
        )

    # --- pricing ------------------------------------------------------------

    async def preview(self, ticket: SpreadTicket, *, account: dict | None = None) -> ResolvedSpread:
        """What the ticket would become. Ungated, like the equity preview:
        seeing the risk of a spread you may not place is useful, not
        dangerous. The options level is reported, not enforced, here.

        `account` lets a caller that previews many tickets in one go (the
        optimizer's finalists) fetch the account once and pass it in; left
        out, it is fetched here as before."""
        chains = {expiry: await self.chain(ticket.underlying, expiry) for expiry in ticket.expiries}
        chain = chains[ticket.expiry]
        legs = resolve_legs(ticket, chains)
        signed_mid = net_price(legs, "mid")
        if signed_mid is None:
            raise OrderRejected("No market on at least one leg right now", field="strikes")
        expected = 1 if ticket.direction == "debit" else -1
        warnings: list[str] = []
        if signed_mid * expected <= 0:
            warnings.append(
                f"The market quotes this {ticket.direction} spread the other way round "
                f"({signed_mid:+.2f}) -- check the legs."
            )
        net_mid = abs(signed_mid)
        signed_natural = net_price(legs, "natural")
        net_natural = (
            abs(signed_natural)
            if signed_natural is not None and signed_natural * expected > 0
            else None
        )
        price = round(ticket.limit_price if ticket.limit_price is not None else net_mid, 2)
        risk = spread_risk(ticket.strategy, ticket.strikes, price, ticket.qty, stock_price=chain.spot)

        account = account if account is not None else await self.account()
        limits = limits_for(self._settings, self._account)
        assert_spread_within_limits(
            qty=ticket.qty,
            collateral=risk.collateral,
            options_buying_power=account["options_buying_power"],
            max_contracts=limits.max_option_contracts,
            max_notional=limits.max_order_notional,
        )

        # What an income strategy is written against. Reported here, enforced
        # at submit: the broker would refuse an uncovered write anyway, but
        # a clear number beats its message.
        coverage: Coverage | None = None
        if ticket.strategy == "covered_call":
            have = await self._shares_held(ticket.underlying)
            need = 100 * ticket.qty
            coverage = Coverage(kind="shares", have=have, need=need, ok=have >= need)
            if not coverage.ok:
                warnings.append(f"Covered call needs {need} shares of {ticket.underlying.upper()}; {have} held.")
        elif ticket.strategy == "cash_secured_put":
            have = _number(account.get("buying_power")) or 0.0
            need = ticket.strikes[0] * 100 * ticket.qty
            coverage = Coverage(kind="cash", have=have, need=need, ok=have >= need)
            if not coverage.ok:
                warnings.append(f"Cash-secured put needs {need:,.0f} of buying power; {have:,.0f} available.")

        # The risk chart, and for the two-expiry shapes the numbers the
        # closed-form arithmetic cannot give.
        payoff = self._payoff(
            legs, ticket.qty, price if ticket.direction == "debit" else -price, chain.spot, ticket.strategy
        )
        max_profit, max_loss, breakevens = risk.max_profit, risk.max_loss, risk.breakevens
        if ticket.strategy in TIME_STRATEGIES and payoff is not None:
            max_profit = payoff.max_profit
            breakevens = payoff.breakevens
        if payoff is not None and payoff.today is None:
            warnings.append("No IV on at least one leg: the risk chart shows the expiry curve only.")

        dte = (ticket.expiry - self._source.now().date()).days
        if dte <= 0:
            warnings.append("Expires today: Alpaca closes same-day-expiry option positions around 15:15 ET.")
        if any(leg.delta is None for leg in legs):
            warnings.append("No greeks for at least one leg (Alpaca returns none close to expiry).")
        for leg in legs:
            if leg.bid is not None and leg.ask is not None and leg.mid and (leg.ask - leg.bid) > _WIDE_MARKET_FRACTION * leg.mid:
                warnings.append(f"{leg.symbol}: wide market ({leg.bid:.2f} / {leg.ask:.2f}); a mid limit may not fill.")

        return ResolvedSpread(
            underlying=ticket.underlying.upper(),
            strategy=ticket.strategy,
            expiry=ticket.expiry,
            qty=ticket.qty,
            direction=ticket.direction,
            legs=legs,
            spot=chain.spot,
            width=risk.width,
            net_mid=round(net_mid, 4),
            net_natural=round(net_natural, 4) if net_natural is not None else None,
            limit_price=price,
            alpaca_limit_price=alpaca_limit(ticket.direction, price),
            max_profit=max_profit,
            max_loss=max_loss,
            breakevens=breakevens,
            collateral=risk.collateral,
            options_buying_power=account["options_buying_power"],
            dte=dte,
            options_level=account["options_trading_level"],
            account=self._account,
            warnings=warnings,
            client_order_id=ticket.client_order_id,
            coverage=coverage,
            payoff=payoff,
        )

    def _payoff(
        self, legs: list[SpreadLeg], qty: int, net_price: float, spot: float, strategy: str | None = None
    ) -> Payoff | None:
        """The risk chart for `legs` (a covered call gets its share leg at
        the spot). None when the curve cannot be built."""
        payoff_legs = [
            PayoffLeg(kind=leg.kind, strike=leg.strike, side=leg.side, ratio=leg.ratio_qty, expiry=leg.expiry, iv=leg.iv)
            for leg in legs
        ]
        if strategy == "covered_call":
            payoff_legs.append(PayoffLeg(kind="stock", strike=spot, side="buy"))
        try:
            return Payoff(**payoff_curve(payoff_legs, qty, net_price, spot, self._source.now()))
        except (ValueError, ZeroDivisionError):
            logger.debug("No payoff curve", exc_info=True)
            return None

    async def payoff_for_held(self, req: PayoffRequest) -> Payoff:
        """The risk chart of a held position, priced from fresh quotes: the
        legs as held (their sides), the net entry as the cost basis."""
        held = closing_legs(req.legs)
        # closing_legs gives the *closing* sides; flip back to what is held.
        for leg in held:
            leg.side = "buy" if leg.side == "sell" else "sell"
            leg.position_intent = "buy_to_open" if leg.side == "buy" else "sell_to_open"
        quotes = await self._source.leg_quotes([leg.symbol for leg in held])
        for leg in held:
            quote = quotes.get(leg.symbol)
            if quote is not None:
                leg.bid, leg.ask, leg.mid, leg.delta, leg.iv = quote.bid, quote.ask, quote.mid, quote.delta, quote.iv
                leg.gamma, leg.theta, leg.last_at = quote.gamma, quote.theta, quote.last_at
        parsed = try_parse_occ(held[0].symbol)
        spot = await self.spot(parsed.underlying) if parsed else None
        if spot is None:
            raise OrderRejected("No price for the underlying right now", field="legs")
        # A short call with shares behind it draws as a covered call.
        strategy = None
        if parsed is not None and len(held) == 1 and held[0].kind == "call" and held[0].side == "sell":
            if await self._shares_held(parsed.underlying) >= 100 * req.qty:
                strategy = "covered_call"
        payoff = self._payoff(held, req.qty, req.net_entry, spot, strategy)
        if payoff is None:
            raise OrderRejected("Could not build the risk chart for these legs", field="legs")
        return payoff

    async def _priced_close(self, req: CloseSpreadRequest) -> tuple[list[SpreadLeg], str, float, float | None]:
        legs = closing_legs(req.legs)
        quotes = await self._source.leg_quotes([leg.symbol for leg in legs])
        for leg in legs:
            quote = quotes.get(leg.symbol)
            if quote is not None:
                leg.bid, leg.ask, leg.mid, leg.delta = quote.bid, quote.ask, quote.mid, quote.delta
                leg.gamma, leg.theta, leg.iv, leg.last_at = quote.gamma, quote.theta, quote.iv, quote.last_at
        signed_mid = net_price(legs, "mid")
        if signed_mid is None:
            raise OrderRejected("No market on at least one leg right now", field="legs")
        direction = "debit" if signed_mid > 0 else "credit"
        net_mid = abs(signed_mid)
        signed_natural = net_price(legs, "natural")
        net_natural = (
            abs(signed_natural)
            if signed_natural is not None and (signed_natural > 0) == (signed_mid > 0)
            else None
        )
        return legs, direction, net_mid, net_natural

    async def preview_close(self, req: CloseSpreadRequest) -> dict:
        legs, direction, net_mid, net_natural = await self._priced_close(req)
        suggested = round(net_mid, 2)
        return {
            "legs": [leg.model_dump(mode="json") for leg in legs],
            "qty": req.qty,
            "direction": direction,
            "net_mid": round(net_mid, 4),
            "net_natural": round(net_natural, 4) if net_natural is not None else None,
            "suggested_limit": suggested,
            "alpaca_limit_price": alpaca_limit(direction, suggested) if suggested > 0 else 0.0,
        }

    # --- writes -------------------------------------------------------------

    async def submit(self, ticket: SpreadTicket, confirm: str | None = None) -> dict:
        assert_can_trade(self._settings, self._account, confirm, live_available=self._live_available)
        resolved = await self.preview(ticket)
        assert_options_level(
            resolved.options_level, options_level_required(ticket.strategy), STRATEGY_LABELS[ticket.strategy]
        )
        if resolved.coverage is not None and not resolved.coverage.ok:
            raise OrderRejected(
                f"{STRATEGY_LABELS[ticket.strategy]} is not covered: {resolved.coverage.need:,.0f} "
                f"{resolved.coverage.kind} needed, {resolved.coverage.have:,.0f} available",
                field="qty",
            )
        if len(resolved.legs) == 1:
            request = build_single_leg_request(
                resolved.legs[0], resolved.qty, resolved.limit_price, resolved.client_order_id
            )
        else:
            request = build_mleg_request(
                resolved.legs, resolved.qty, resolved.alpaca_limit_price, resolved.client_order_id
            )
        try:
            order = await asyncio.to_thread(self._trading.submit_order, request)
        except Exception as exc:
            rejection = rejection_from_api_error(exc)
            if rejection is not None:
                raise rejection from exc
            raise
        logger.info(
            "Submitted %s %s x%d on %s (%s limit %+.2f) account=%s client_order_id=%s",
            resolved.strategy,
            resolved.underlying,
            resolved.qty,
            resolved.expiry.isoformat(),
            resolved.direction,
            resolved.alpaca_limit_price,
            self._account,
            resolved.client_order_id,
        )
        return _plain(order)

    async def close_spread(
        self, req: CloseSpreadRequest, confirm: str | None = None, *, marketable: bool = False
    ) -> dict:
        """Close `req.qty` spreads with a limit order: the caller's price,
        else the mid, else (for the trigger loop) a price stepped toward
        the natural so it fills rather than rests."""
        assert_can_trade(self._settings, self._account, confirm, live_available=self._live_available)
        legs, direction, net_mid, net_natural = await self._priced_close(req)
        if req.limit_price is not None:
            price = round(req.limit_price, 2)
        elif marketable:
            price = marketable_close_limit(direction, net_mid, net_natural, self._settings.trading_options_trigger_slippage)
        else:
            price = round(net_mid, 2)
        if price <= 0:
            raise OrderRejected("The closing price must be positive", field="limit_price")
        if len(legs) == 1:
            request = build_single_leg_request(legs[0], req.qty, price, req.client_order_id)
        else:
            request = build_mleg_request(legs, req.qty, alpaca_limit(direction, price), req.client_order_id)
        try:
            order = await asyncio.to_thread(self._trading.submit_order, request)
        except Exception as exc:
            rejection = rejection_from_api_error(exc)
            if rejection is not None:
                raise rejection from exc
            raise
        logger.info(
            "Closing %d x %s (%s %.2f) account=%s marketable=%s",
            req.qty,
            "/".join(leg.symbol for leg in legs),
            direction,
            price,
            self._account,
            marketable,
        )
        return _plain(order)
