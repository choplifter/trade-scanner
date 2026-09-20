"""Everything the Options widget needs from Alpaca, per account.

Mirrors app.trading.service.OrderService in shape and posture: reads are
ungated, every write starts at the guard, every SDK call goes through
asyncio.to_thread, SDK imports are function-local, and the request
builder is a pure function with its own tests.
"""

import asyncio
import logging
from datetime import date, datetime, time

from app.alpaca.client import AlpacaClients
from app.core.config import Settings
from app.options.chain import Chain
from app.options.chain_fetch import ChainCache
from app.options.guards import assert_options_level
from app.options.custom import chance_of_touch, custom_risk, nearest_breakeven
from app.options.iv_context import atm_iv
from app.options.models import (
    STRATEGY_LABELS,
    TIME_STRATEGIES,
    Coverage,
    OrderType,
    Payoff,
    PayoffRequest,
    CloseSpreadRequest,
    RollRequest,
    ResolvedSpread,
    SpreadLeg,
    SpreadTicket,
    closing_legs,
    level_for_legs,
    naked_shorts,
    options_level_required,
    resolve_legs,
)
from app.options.optimizer import chance_of_profit, position_pnl
from app.services.market_clock import ET
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
from app.trading.errors import OrderRejected, TradingError, rejection_from_api_error
from app.trading.guards import Account, assert_can_trade, limits_for
from app.trading.service import OrderService, _number, _plain

logger = logging.getLogger(__name__)

# A market wider than this fraction of its own mid gets a warning on the
# preview: a mid-priced limit on a 1.00/1.60 contract is not a fill.
_WIDE_MARKET_FRACTION = 0.25


def worst_case(
    legs: list[SpreadLeg], bare: list[SpreadLeg], net_price: float, payoff: Payoff, qty: int
) -> float | None:
    """The most a built package can lose.

    The payoff curve's own minimum is the honest answer for defined risk,
    but it is only the edge of a grid that reaches a few standard
    deviations: with an uncovered short it reports whatever the grid
    happened to stop at, which read "max loss -940" beside a naked put
    whose real floor was seventy-five thousand. So: an uncovered call has
    no floor at all (None, shown as unbounded), and an uncovered put's is
    the position valued with the underlying at nothing."""
    if not bare:
        return payoff.max_loss
    if any(leg.kind == "call" for leg in bare):
        return None
    payoff_legs = [
        PayoffLeg(kind=leg.kind, strike=leg.strike, side=leg.side, ratio=leg.ratio_qty, expiry=leg.expiry, iv=leg.iv)
        for leg in legs
    ]
    expiry_moment = datetime.combine(payoff.expiry, time(16, 0), tzinfo=ET)
    floor = position_pnl(payoff_legs, net_price, 0.01, expiry_moment, qty)
    return floor if floor is not None else payoff.max_loss


def market_warning(leg_count: int, no_natural: bool) -> str:
    """What a market order's preview has to say: the price shown is a
    quote, not a promise -- and a package crosses every leg's spread."""
    parts = ["Market order: fills at whatever the market gives; the price shown is the natural, not a guarantee."]
    if leg_count > 1:
        parts.append("A multi-leg market order can fill well beyond the natural when a leg is wide.")
    if no_natural:
        parts.append("No natural right now (a leg without a two-sided quote), so the estimate is the mid.")
    parts.append("Alpaca takes option market orders in the regular session only.")
    return " ".join(parts)


def build_mleg_request(
    legs: list[SpreadLeg],
    qty: int,
    alpaca_limit_price: float,
    client_order_id: str | None,
    order_type: OrderType = "limit",
):
    """The multi-leg order. Pure and testable without a client, like
    app.trading.service._build_request. Options at Alpaca are day orders;
    the sign of a limit says debit (+) or credit (-). A market order sends
    no price at all -- the legs' sides say which way the package goes."""
    from alpaca.trading.enums import OrderClass, OrderSide, PositionIntent, TimeInForce
    from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest, OptionLegRequest

    kwargs = {}
    if client_order_id:
        kwargs["client_order_id"] = client_order_id
    if order_type == "market":
        request_cls = MarketOrderRequest
    else:
        request_cls = LimitOrderRequest
        kwargs["limit_price"] = alpaca_limit_price
    return request_cls(
        qty=qty,
        order_class=OrderClass.MLEG,
        time_in_force=TimeInForce.DAY,
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


def build_single_leg_request(
    leg: SpreadLeg,
    qty: int,
    limit_price: float,
    client_order_id: str | None,
    order_type: OrderType = "limit",
):
    """A plain option order: a long call/put opened outright, or a broken
    spread down to one contract being closed -- the SDK refuses MLEG with
    fewer than two legs."""
    from alpaca.trading.enums import OrderSide, PositionIntent, TimeInForce
    from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

    kwargs = {}
    if client_order_id:
        kwargs["client_order_id"] = client_order_id
    if order_type == "market":
        request_cls = MarketOrderRequest
    else:
        request_cls = LimitOrderRequest
        kwargs["limit_price"] = round(abs(limit_price), 2)
    return request_cls(
        symbol=leg.symbol,
        qty=qty,
        side=OrderSide(leg.side),
        position_intent=PositionIntent(leg.position_intent),
        time_in_force=TimeInForce.DAY,
        **kwargs,
    )



def released_collateral(legs: list[SpreadLeg], qty: int) -> float:
    """What closing `legs` gives back to buying power: the strike for a lone
    short put (a cash-secured put's collateral -- SimOptionsService's
    collateral_for and Alpaca agree on it), the width for a short vertical,
    nothing otherwise. A roll's open leg is judged against buying power plus
    this, so rolling a put to the same strike, or a condor's side to the
    same width, needs no new cash.

    The width, not width-less-credit: the credit was received when the
    package was opened and is not in `legs`. That understates what comes
    back by the credit, which errs toward refusing a roll rather than
    placing one the broker then rejects."""
    if len(legs) == 1:
        leg = legs[0]
        if leg.kind == "put" and leg.position_intent == "buy_to_close":
            return round(leg.strike * 100 * qty * int(leg.ratio_qty or 1), 2)
        return 0.0
    if len(legs) == 2:
        short, long_ = legs
        if short.position_intent != "buy_to_close":
            short, long_ = long_, short
        # A short vertical: the short leg is being bought back, both legs
        # are the same kind and expiry, and the short strike is the one
        # that can be exercised against -- above the long for puts, below
        # it for calls. The other way round is a long vertical, which was
        # paid for rather than collateralised, so nothing comes back.
        if (
            short.position_intent == "buy_to_close"
            and long_.position_intent == "sell_to_close"
            and short.kind == long_.kind
            and short.expiry == long_.expiry
        ):
            written = short.strike > long_.strike if short.kind == "put" else short.strike < long_.strike
            if written:
                return round(abs(long_.strike - short.strike) * 100 * qty, 2)
    return 0.0


def roll_net(close: dict, opened: ResolvedSpread) -> dict:
    """The net of a roll per package from its two halves: what the close
    costs (positive = pay) plus what the open brings (negative = receive).
    The natural is reported only when both halves have one and the sum
    points the same way as the mid; a roll whose mid is a credit but whose
    natural is a debit has no honest single number for it."""
    signed_close_mid = close["net_mid"] if close["direction"] == "debit" else -close["net_mid"]
    signed_open_mid = opened.net_mid if opened.direction == "debit" else -opened.net_mid
    signed_mid = signed_close_mid + signed_open_mid
    direction = "debit" if signed_mid > 0 else "credit"
    natural = None
    if close.get("net_natural") is not None and opened.net_natural is not None:
        signed_close_nat = close["net_natural"] if close["direction"] == "debit" else -close["net_natural"]
        signed_open_nat = opened.net_natural if opened.direction == "debit" else -opened.net_natural
        signed_nat = signed_close_nat + signed_open_nat
        if (signed_nat > 0) == (signed_mid > 0) or signed_nat == 0:
            natural = round(abs(signed_nat), 4)
    return {
        "direction": direction,
        "mid": round(abs(signed_mid), 4),
        "natural": natural,
        "suggested_limit": round(abs(signed_mid), 2),
    }


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

    async def expiries(self, underlying: str, *, far: tuple[int, int] | None = None, board: bool = False) -> dict:
        """The picker's expiry strip. With `board`, every listed expiry
        beyond it (out to about three years, learned from the strikes
        nearest the spot) is appended -- what the expiry axis draws. With
        `far` = (lo_days, hi_days), the expiries in that window beyond the
        strip are appended instead, with their full contract counts."""
        try:
            spot, expiries = await self._source.expiries(underlying)
            extra = []
            if board:
                fetch = getattr(self._source, "board_expiries", None)
                if fetch is not None:
                    _spot, extra = await fetch(underlying)
            if far is not None:
                extra = [*extra, *(await self.far_expiries(underlying, far[0], far[1]))]
            if extra:
                listed = {e.expiry for e in expiries}
                expiries = [*expiries, *(e for e in sorted(extra, key=lambda e: e.expiry) if e.expiry not in listed and not listed.add(e.expiry))]
        except LookupError as exc:
            raise OrderRejected(str(exc), field="underlying") from exc
        return {
            "underlying": underlying.upper(),
            "spot": spot,
            "expiries": [e.to_dict() for e in expiries],
        }

    async def far_expiries(self, underlying: str, lo_days: int, hi_days: int) -> list:
        """The far strip's ExpiryInfos `lo_days`..`hi_days` out; empty when
        the source has none (a replay)."""
        fetch = getattr(self._source, "far_expiries", None)
        if fetch is None or hi_days < lo_days:
            return []
        _spot, expiries = await fetch(underlying, lo_days, hi_days)
        return list(expiries)

    async def chain(self, underlying: str, expiry) -> Chain:
        try:
            return await self._source.chain(underlying, expiry)
        except LookupError as exc:
            raise OrderRejected(str(exc), field="expiry") from exc

    async def spreads(self) -> list[SpreadGroup]:
        positions = _plain(await asyncio.to_thread(self._trading.get_all_positions)) or []
        return group_spreads(positions, account=self._account, equity_positions=positions)

    async def _long_call_cover(self, underlying: str, strike: float, expiry: date) -> int:
        """Long calls held on `underlying` that cover a short call at
        `strike` / `expiry`: a lower-or-equal strike and a later-or-equal
        expiry, in contracts. Short calls already written against them are
        not subtracted -- the playbooks never write twice, and the broker
        refuses a truly naked call itself."""
        contracts = 0
        for p in await self.marked_positions():
            qty = _number(p.get("qty")) or 0.0
            if qty <= 0:
                continue
            parsed = try_parse_occ(str(p.get("symbol") or ""))
            if parsed is None or parsed.underlying != underlying.upper() or parsed.kind != "call":
                continue
            if parsed.strike <= strike + 1e-9 and parsed.expiry >= expiry:
                contracts += int(round(qty))
        return contracts

    async def _shares_held(self, underlying: str) -> int:
        positions = _plain(await asyncio.to_thread(self._trading.get_all_positions)) or []
        return sum(
            int(round(_number(p.get("qty")) or 0))
            for p in positions
            if (p.get("symbol") or "").upper() == underlying.upper()
            and (p.get("asset_class") or "us_equity").lower() == "us_equity"
        )

    # --- pricing ------------------------------------------------------------

    async def orders(self, status: str = "closed") -> list[dict]:
        """The account's option orders by status, in Alpaca's shape (an MLEG
        parent with its legs nested) -- what the playbook runner reconciles
        a campaign from. Equity orders are left out."""
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        try:
            query = QueryOrderStatus(status)
        except ValueError:
            raise OrderRejected(f"Unknown order status: {status}", field="status") from None
        request = GetOrdersRequest(status=query, limit=500, nested=True)
        rows = _plain(await asyncio.to_thread(self._trading.get_orders, request)) or []

        def is_option(order: dict) -> bool:
            if try_parse_occ(str(order.get("symbol") or "").upper()) is not None:
                return True
            return any(try_parse_occ(str(l.get("symbol") or "").upper()) is not None for l in order.get("legs") or [] if isinstance(l, dict))

        return [o for o in rows if isinstance(o, dict) and is_option(o)]

    async def cancel(self, order_id: str, confirm: str | None = None) -> None:
        """Cancel one resting option order (an MLEG parent takes its legs
        with it). A write, so it goes through the same guard as a submit."""
        assert_can_trade(self._settings, self._account, confirm, live_available=self._live_available)
        try:
            await asyncio.to_thread(self._trading.cancel_order_by_id, order_id)
        except Exception as exc:
            rejection = rejection_from_api_error(exc)
            if rejection is not None:
                raise rejection from exc
            raise

    async def activities(self, after: str | None = None) -> list[dict]:
        """Assignments, expirations and exercises on the account since
        `after` -- see app.alpaca.activities. Empty when the broker cannot be
        asked; the runner then reads only orders and the positions."""
        from app.alpaca.activities import fetch_option_activities

        return await fetch_option_activities(self._trading, after=after)

    async def marked_positions(self) -> list[dict]:
        """The account's option positions in Alpaca's own shape (signed qty,
        avg_entry_price and current_price per share) -- the same list
        SimOptionsService.marked_positions builds for the simulated book, so
        the playbook runner reads both accounts alike."""
        positions = _plain(await asyncio.to_thread(self._trading.get_all_positions)) or []
        return [p for p in positions if isinstance(p, dict) and str(p.get("asset_class", "")) == "us_option"]

    async def share_position(self, underlying: str) -> dict | None:
        """The long share position in `underlying` as {qty, avg_entry_price},
        or None -- what a playbook's cost basis starts from."""
        underlying = underlying.upper()
        positions = _plain(await asyncio.to_thread(self._trading.get_all_positions)) or []
        for p in positions:
            if str(p.get("asset_class", "us_equity")) != "us_equity" or str(p.get("symbol", "")).upper() != underlying:
                continue
            qty = _number(p.get("qty")) or 0.0
            if qty <= 0:
                continue
            return {"symbol": underlying, "qty": qty, "avg_entry_price": _number(p.get("avg_entry_price"))}
        return None

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
        built = ticket.strategy == "custom"
        # A named shape declares its direction and is warned when the market
        # disagrees; a built one has no declaration to disagree with -- what
        # the legs come to is what it is.
        direction = ("debit" if signed_mid > 0 else "credit") if built else ticket.direction
        expected = 1 if direction == "debit" else -1
        warnings: list[str] = []
        if not built and signed_mid * expected <= 0:
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
        market = ticket.order_type == "market"
        if market:
            # No limit is sent; the natural is what the order is expected to
            # fill near, and pricing the risk there keeps the ceilings honest.
            price = round(net_natural if net_natural is not None else net_mid, 2)
        else:
            price = round(ticket.limit_price if ticket.limit_price is not None else net_mid, 2)
        # A built position gets its ceilings from the payoff curve below;
        # the closed forms have no name to key on. Priced first so the
        # buying-power check has a collateral to work with.
        bare = naked_shorts(ticket.leg_specs_full()) if built else []
        built_payoff = (
            self._payoff(legs, ticket.qty, price if direction == "debit" else -price, chain.spot, ticket.strategy)
            if built
            else None
        )
        if built:
            if built_payoff is None:
                raise OrderRejected("These legs cannot be priced together right now", field="legs")
            bare_legs = [leg for leg in legs if any(b.kind == leg.kind and b.strike == leg.strike for b in bare)]
            risk = custom_risk(
                legs,
                bare_legs,
                ticket.qty,
                chain.spot,
                built_payoff.max_profit,
                worst_case(legs, bare_legs, price if direction == "debit" else -price, built_payoff, ticket.qty),
                built_payoff.breakevens,
            )
        else:
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
            shares = await self._shares_held(ticket.underlying)
            need = 100 * ticket.qty
            if shares >= need:
                coverage = Coverage(kind="shares", have=shares, need=need, ok=True)
            else:
                # The poor man's cover: a long call at or below the strike
                # that expires no earlier stands in for 100 shares.
                calls = await self._long_call_cover(ticket.underlying, ticket.strikes[0], ticket.expiry)
                have = shares + 100 * calls
                coverage = Coverage(kind="cover" if calls else "shares", have=have, need=need, ok=have >= need)
                if not coverage.ok:
                    warnings.append(
                        f"Covered call needs {need} shares of {ticket.underlying.upper()}, or a longer-dated call at or "
                        f"below {ticket.strikes[0]:g} per contract; {shares} shares and {calls} such call{'s' if calls != 1 else ''} held."
                    )
        elif ticket.strategy == "cash_secured_put":
            have = _number(account.get("buying_power")) or 0.0
            need = ticket.strikes[0] * 100 * ticket.qty
            coverage = Coverage(kind="cash", have=have, need=need, ok=have >= need)
            if not coverage.ok:
                warnings.append(f"Cash-secured put needs {need:,.0f} of buying power; {have:,.0f} available.")

        # The risk chart, and for the two-expiry shapes the numbers the
        # closed-form arithmetic cannot give.
        payoff = built_payoff or self._payoff(
            legs, ticket.qty, price if direction == "debit" else -price, chain.spot, ticket.strategy
        )
        max_profit, max_loss, breakevens = risk.max_profit, risk.max_loss, risk.breakevens
        if ticket.strategy in TIME_STRATEGIES and payoff is not None:
            max_profit = payoff.max_profit
            breakevens = payoff.breakevens
        if payoff is not None and payoff.today is None:
            warnings.append("No IV on at least one leg: the risk chart shows the expiry curve only.")

        dte = (ticket.expiry - self._source.now().date()).days
        if dte <= 0:
            # Two different times, and they were conflated here before: 15:15
            # is the order cutoff, the liquidation starts a quarter of an
            # hour later -- and both are 15 minutes later again for the
            # broad-based ETFs (SPY, QQQ), which is exactly what most of
            # this widget's 0DTE tickets are.
            warnings.append(
                "Expires today: Alpaca takes orders until 15:15 ET (15:30 for broad-based ETFs like SPY and QQQ) "
                "and from 15:30 ET (15:45 for those) liquidates expiring positions the account cannot cover. "
                "An in-the-money short leg it can cover is assigned after the close instead."
            )
        if any(leg.delta is None for leg in legs):
            warnings.append("No greeks for at least one leg (Alpaca returns none close to expiry).")
        for leg in legs:
            if leg.bid is not None and leg.ask is not None and leg.mid and (leg.ask - leg.bid) > _WIDE_MARKET_FRACTION * leg.mid:
                if market:
                    warnings.append(f"{leg.symbol}: wide market ({leg.bid:.2f} / {leg.ask:.2f}); a market order pays the spread.")
                else:
                    warnings.append(f"{leg.symbol}: wide market ({leg.bid:.2f} / {leg.ask:.2f}); a mid limit may not fill.")
        if market:
            warnings.append(market_warning(len(legs), net_natural is None))
        if built and bare:
            naked_note = ", ".join(f"{leg.strike:g} {leg.kind}" for leg in bare)
            warnings.append(
                f"Uncovered short leg ({naked_note}): the loss has no ceiling, and the collateral shown is the "
                "broker's standard margin as an estimate, not what Alpaca will hold. Needs options level 4."
            )

        # The two probabilities, under the distribution the chain itself
        # implies (see app.options.custom): the chance of any profit at
        # expiry, and the chance the market reaches the nearest breakeven
        # at all before then -- the one a seller manages against.
        chance = touch = None
        sigma = atm_iv(chain)
        years = max(0.0, (payoff.expiry - self._source.now().date()).days / 365.0) if payoff is not None else 0.0
        if payoff is not None and sigma and years > 0:
            payoff_legs = [
                PayoffLeg(kind=leg.kind, strike=leg.strike, side=leg.side, ratio=leg.ratio_qty, expiry=leg.expiry, iv=leg.iv)
                for leg in legs
            ]
            horizon = self._source.now().replace(year=payoff.expiry.year, month=payoff.expiry.month, day=payoff.expiry.day)
            chance = chance_of_profit(
                payoff_legs, price if direction == "debit" else -price, horizon, chain.spot, sigma, years, ticket.qty
            )
            barrier = nearest_breakeven(breakevens, chain.spot)
            if barrier is not None:
                touch = chance_of_touch(chain.spot, barrier, sigma, years)

        return ResolvedSpread(
            underlying=ticket.underlying.upper(),
            strategy=ticket.strategy,
            expiry=ticket.expiry,
            qty=ticket.qty,
            direction=direction,
            legs=legs,
            spot=chain.spot,
            width=risk.width,
            net_mid=round(net_mid, 4),
            net_natural=round(net_natural, 4) if net_natural is not None else None,
            limit_price=price,
            alpaca_limit_price=alpaca_limit(direction, price),
            order_type=ticket.order_type,
            chance=chance,
            touch=touch,
            touch_at=nearest_breakeven(breakevens, chain.spot),
            naked=bool(bare),
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
        self, legs: list[SpreadLeg], qty: int, net_entry: float, spot: float, strategy: str | None = None
    ) -> Payoff | None:
        """The risk chart for `legs` (a covered call gets its share leg at
        the spot). None when the curve cannot be built.

        The legs' mids anchor the today curve to the market (see
        payoff_curve); a leg without a mid leaves it on the model alone.
        The covered call's share leg is added afterwards on purpose: it is
        worth nothing at the spot it is referenced to, so the mark stays
        the options' own."""
        mark = net_price(legs, "mid")
        payoff_legs = [
            PayoffLeg(kind=leg.kind, strike=leg.strike, side=leg.side, ratio=leg.ratio_qty, expiry=leg.expiry, iv=leg.iv)
            for leg in legs
        ]
        if strategy == "covered_call":
            payoff_legs.append(PayoffLeg(kind="stock", strike=spot, side="buy"))
        try:
            return Payoff(**payoff_curve(payoff_legs, qty, net_entry, spot, self._source.now(), mark=mark))
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

    async def _account_after_closing(self, req: CloseSpreadRequest) -> dict:
        """The account as it stands once `req` has closed: the collateral the
        closing legs hold is given back to buying power, so the leg a roll
        opens is judged against the cash it will actually have -- a
        cash-secured put rolled to the same strike needs no new cash."""
        account = await self.account()
        released = released_collateral(closing_legs(req.legs), req.qty)
        if released <= 0:
            return account
        bumped = dict(account)
        for key in ("options_buying_power", "buying_power"):
            value = _number(account.get(key))
            if value is not None:
                bumped[key] = round(value + released, 2)
        return bumped

    async def preview_roll(self, req: RollRequest) -> dict:
        """Both halves of a roll priced together: the close as preview_close
        prices it, the open as preview prices it with the closing legs'
        collateral already released, and the net per package -- a credit
        when the new leg brings in more than the old one costs to close."""
        close = await self.preview_close(req.close)
        opened = await self.preview(req.open, account=await self._account_after_closing(req.close))
        net = roll_net(close, opened)
        warnings = list(opened.warnings)
        if opened.coverage is not None and not opened.coverage.ok:
            warnings.append(
                f"The new leg is not covered: {opened.coverage.need:,.0f} {opened.coverage.kind} needed, "
                f"{opened.coverage.have:,.0f} available once the old leg is closed."
            )
        return {
            "close": close,
            "open": opened.model_dump(mode="json"),
            "net": net,
            "collateral_delta": round(opened.collateral - released_collateral(closing_legs(req.close.legs), req.close.qty), 2),
            "warnings": warnings,
            "can_submit": opened.coverage is None or opened.coverage.ok,
        }

    async def roll(self, req: RollRequest, confirm: str | None = None) -> dict:
        """At Alpaca a roll is two orders in sequence: the close, then the
        open. The second can be refused (coverage the broker only frees
        once the close fills, a limit the market has left) or the first can
        rest unfilled; the response reports both orders and the open's
        error, and the caller says so rather than pretending a package."""
        assert_can_trade(self._settings, self._account, confirm, live_available=self._live_available)
        close_order = await self.close_spread(req.close, confirm)
        try:
            open_order = await self.submit(req.open, confirm)
        except TradingError as exc:
            logger.warning("Roll: close placed, open refused: %s", exc)
            return {"order": None, "close_order": close_order, "open_order": None, "open_error": str(exc)}
        except Exception as exc:
            logger.exception("Roll: close placed, open failed")
            return {"order": None, "close_order": close_order, "open_order": None, "open_error": str(exc)}
        return {"order": None, "close_order": close_order, "open_order": open_order, "open_error": None}

    # --- writes -------------------------------------------------------------

    async def submit(self, ticket: SpreadTicket, confirm: str | None = None) -> dict:
        assert_can_trade(self._settings, self._account, confirm, live_available=self._live_available)
        resolved = await self.preview(ticket)
        assert_options_level(
            resolved.options_level,
            level_for_legs(ticket.strategy, ticket.leg_specs_full()),
            STRATEGY_LABELS[ticket.strategy],
        )
        if resolved.coverage is not None and not resolved.coverage.ok:
            raise OrderRejected(
                f"{STRATEGY_LABELS[ticket.strategy]} is not covered: {resolved.coverage.need:,.0f} "
                f"{resolved.coverage.kind} needed, {resolved.coverage.have:,.0f} available",
                field="qty",
            )
        if len(resolved.legs) == 1:
            request = build_single_leg_request(
                resolved.legs[0], resolved.qty, resolved.limit_price, resolved.client_order_id, resolved.order_type
            )
        else:
            request = build_mleg_request(
                resolved.legs, resolved.qty, resolved.alpaca_limit_price, resolved.client_order_id, resolved.order_type
            )
        try:
            order = await asyncio.to_thread(self._trading.submit_order, request)
        except Exception as exc:
            rejection = rejection_from_api_error(exc)
            if rejection is not None:
                raise rejection from exc
            raise
        logger.info(
            "Submitted %s %s x%d on %s (%s %s %+.2f) account=%s client_order_id=%s",
            resolved.strategy,
            resolved.underlying,
            resolved.qty,
            resolved.expiry.isoformat(),
            resolved.direction,
            resolved.order_type,
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
        the natural so it fills rather than rests. Or, asked for, a market
        order (the trigger loop keeps its marketable limit)."""
        assert_can_trade(self._settings, self._account, confirm, live_available=self._live_available)
        legs, direction, net_mid, net_natural = await self._priced_close(req)
        order_type: OrderType = "market" if req.order_type == "market" and not marketable else "limit"
        if order_type == "market":
            price = round(net_natural if net_natural is not None else net_mid, 2)
        elif req.limit_price is not None:
            price = round(req.limit_price, 2)
        elif marketable:
            price = marketable_close_limit(direction, net_mid, net_natural, self._settings.trading_options_trigger_slippage)
        else:
            price = round(net_mid, 2)
        if order_type == "limit" and price <= 0:
            raise OrderRejected("The closing price must be positive", field="limit_price")
        if len(legs) == 1:
            request = build_single_leg_request(legs[0], req.qty, price, req.client_order_id, order_type)
        else:
            request = build_mleg_request(
                legs, req.qty, alpaca_limit(direction, price) if price > 0 else 0.0, req.client_order_id, order_type
            )
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
