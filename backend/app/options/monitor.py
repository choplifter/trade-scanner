"""The trigger loop: watches the underlying's last price and, where a
trigger asks for it, the position's own premium (the mid of closing the
package) for every armed trigger and, when a bound is crossed, closes the
spread with a marketable limit order. Started in main.py's lifespan next
to the sim fill loop and shaped like it: one batched price fetch and one
batched option-snapshot fetch per tick, then each trigger.

Only during the regular session -- options do not trade outside it, so a
premarket print through a stop is noted by the next regular-hours tick,
not acted on at 07:00 against a market that is not open.
"""

import asyncio
import logging

from app.alpaca.client import AlpacaClients
from app.core.config import Settings
from app.options.chain_fetch import ChainCache, fetch_leg_quotes
from app.options.models import CloseLeg, CloseSpreadRequest, closing_legs
from app.options.prices import batch_last_prices
from app.options.pricing import net_price
from app.options.service import OptionsService
from app.options.trigger_store import TriggerStore
from app.services.market_clock import current_session
from app.trading.errors import TradingError
from app.trading.guards import LIVE_CONFIRMATION, assert_can_trade

logger = logging.getLogger(__name__)

# A trigger that keeps failing to get its closing order out (broker down,
# rejected price) is parked as failed after this many ticks, not retried
# every two seconds forever.
MAX_ATTEMPTS = 3


def is_hit(trigger: dict, last: float | None, premium: float | None = None) -> str | None:
    """"below" / "above" (the underlying) / "premium_below" / "premium_above"
    / None. Inclusive on every bound: a stop at 740 fires on a 740.00
    print. A missing price only silences the bounds that need it."""
    if last is not None:
        below = trigger.get("close_below")
        above = trigger.get("close_above")
        if below is not None and last <= float(below):
            return "below"
        if above is not None and last >= float(above):
            return "above"
    if premium is not None:
        p_below = trigger.get("premium_below")
        p_above = trigger.get("premium_above")
        if p_below is not None and premium <= float(p_below):
            return "premium_below"
        if p_above is not None and premium >= float(p_above):
            return "premium_above"
    return None


def wants_premium(trigger: dict) -> bool:
    return trigger.get("premium_below") is not None or trigger.get("premium_above") is not None


def premium_of(trigger: dict, quotes: dict) -> float | None:
    """The position's mark per share: the mid of the closing package as a
    positive number, from the given contract quotes (see fetch_leg_quotes).
    None when any leg lacks a quote -- a package price with a hole in it
    is not a price to fire a stop on."""
    try:
        legs = closing_legs([CloseLeg(symbol=leg["symbol"], qty=leg["qty"]) for leg in trigger.get("legs") or []])
    except Exception:
        return None
    if not legs:
        return None
    for leg in legs:
        quote = quotes.get(leg.symbol)
        if quote is not None:
            leg.bid, leg.ask, leg.mid = quote.bid, quote.ask, quote.mid
    signed = net_price(legs, "mid")
    return None if signed is None else abs(signed)


async def check_one(
    service: OptionsService,
    store: TriggerStore,
    settings: Settings,
    trigger: dict,
    last: float | None,
    premium: float | None = None,
) -> None:
    if last is None and premium is None:
        return
    hit = is_hit(trigger, last, premium)
    if hit is None:
        return
    on_premium = hit.startswith("premium")
    fired_value = premium if on_premium else last
    assert fired_value is not None
    trigger_id = trigger["id"]
    account = trigger.get("account", "paper")

    # The gate, with the confirmation the user gave when arming: a live
    # trigger was confirmed then, it is not asked to type LIVE again at
    # 14:32 on a stop. A refusal (switch turned off since) is final.
    try:
        assert_can_trade(settings, account, LIVE_CONFIRMATION)
    except TradingError as exc:
        await store.mark_failed(trigger_id, f"refused: {exc.message}", MAX_ATTEMPTS, final=True)
        logger.warning("Trigger %s refused: %s", trigger_id, exc.message)
        return

    attempts = int(trigger.get("attempts") or 0) + 1
    try:
        held = {leg.symbol: leg.qty for group in await service.spreads() for leg in group.legs}
        wanted = trigger.get("legs") or []
        legs = [CloseLeg(symbol=leg["symbol"], qty=held[leg["symbol"]]) for leg in wanted if leg["symbol"] in held]
        if len(legs) != len(wanted) or not legs:
            await store.mark_orphaned(trigger_id, "the spread is no longer held (closed elsewhere or expired)")
            logger.info("Trigger %s orphaned: legs no longer held", trigger_id)
            return
        qty = min(int(trigger["qty"]), min(abs(leg.qty) for leg in legs))
        order = await service.close_spread(
            CloseSpreadRequest(legs=legs, qty=qty), confirm=LIVE_CONFIRMATION, marketable=True
        )
        await store.mark_fired(
            trigger_id, fired_value, (order or {}).get("id"), on="premium" if on_premium else "underlying"
        )
        logger.info(
            "Trigger %s fired (%s at %.2f): closing %d x %s on %s, order %s",
            trigger_id, hit, fired_value, qty, trigger.get("underlying"), account, (order or {}).get("id"),
        )
    except Exception as exc:
        final = attempts >= MAX_ATTEMPTS
        await store.mark_failed(trigger_id, str(exc), attempts, final=final)
        logger.exception("Trigger %s attempt %d failed%s", trigger_id, attempts, " (final)" if final else "")


async def run_options_trigger_loop(
    clients: AlpacaClients, settings: Settings, store: TriggerStore, chain_cache: ChainCache, engine
) -> None:
    while True:
        try:
            if settings.has_credentials:
                triggers = await store.all_active()
                if triggers and current_session() == "regular":
                    prices = await batch_last_prices(clients, engine, sorted({t["underlying"] for t in triggers}))
                    # One snapshot call for every contract a premium trigger
                    # watches; a failed fetch silences the premium bounds
                    # for this tick, the underlying bounds still work.
                    quotes: dict = {}
                    symbols = sorted(
                        {leg["symbol"] for t in triggers if wants_premium(t) for leg in t.get("legs") or []}
                    )
                    if symbols:
                        try:
                            quotes = await fetch_leg_quotes(clients, symbols)
                        except Exception:
                            logger.exception("Premium trigger quote fetch failed")
                    services: dict[str, OptionsService] = {}
                    for trigger in triggers:
                        account = trigger.get("account", "paper")
                        if account == "live" and not settings.has_live_credentials:
                            await store.mark_failed(trigger["id"], "no live account configured", MAX_ATTEMPTS, final=True)
                            continue
                        service = services.get(account)
                        if service is None:
                            service = services[account] = OptionsService(
                                clients, settings, engine=engine, chain_cache=chain_cache, account=account
                            )
                        premium = premium_of(trigger, quotes) if wants_premium(trigger) and quotes else None
                        await check_one(service, store, settings, trigger, prices.get(trigger["underlying"]), premium)
        except Exception:
            logger.exception("Options trigger loop tick failed")
        await asyncio.sleep(settings.trading_options_trigger_check_interval)
