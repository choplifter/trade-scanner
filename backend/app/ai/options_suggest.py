"""Gather, ask, repair, price -- the whole suggestion in one call.

Four steps, deliberately separate modules so each can be tested on its own:

    options_context.gather_context   what the model is shown
    options_idea.generate_*          what it proposes
    options_resolve.snap_legs        onto strikes that exist
    OptionsService.preview           what it actually costs

The last step is the one that makes this trustworthy. Every number a user
reads -- net debit or credit, max profit, max loss, breakevens, collateral,
the payoff curve -- comes from the same pricing path the spread ticket
uses, not from the model. A proposal that survives to the screen has been
priced, checked against the account's options level, and checked against
the notional and contract ceilings.

A proposal that does *not* survive is reported, not swallowed. "Claude
suggested an iron condor, and your account's options level does not permit
one" is useful; a silently shorter list is not, and it is exactly the kind
of quiet failure that would make the feature untrustworthy in a way nobody
could see. Same instinct as gex_plan's `available: false`.
"""

import asyncio
import logging

from app.ai.options_context import IdeaContext, gather_context
from app.ai.options_idea import MAX_IDEAS, OptionsIdea, generate_options_ideas
from app.ai.options_resolve import IdeaUnresolvable, snap_legs, ticket_from_legs
from app.options.models import STRATEGY_LABELS
from app.trading.errors import TradingError

logger = logging.getLogger(__name__)

# One spread per suggestion. The ticket is where size is chosen, and
# previewing at 1 keeps the collateral check about the structure rather
# than about a quantity nobody asked for.
DEFAULT_QTY = 1


def _rejected(idea: OptionsIdea, reason: str) -> dict:
    return {
        "strategy": idea.strategy,
        "strategy_label": STRATEGY_LABELS.get(idea.strategy, idea.strategy),
        "expiry": idea.expiry.isoformat(),
        "headline": idea.headline,
        "reason": idea.reason,
        "rejected_because": reason,
    }


async def _price_one(service, context: IdeaContext, idea: OptionsIdea, qty: int) -> tuple[dict | None, dict | None]:
    """(priced idea, rejection). Exactly one of the two is not None."""
    try:
        legs = snap_legs(idea.legs, context.strikes, idea.strategy, idea.expiry)
        ticket = ticket_from_legs(context.underlying, idea.strategy, idea.expiry, legs, qty)
    except IdeaUnresolvable as exc:
        return None, _rejected(idea, str(exc))
    except ValueError as exc:
        # SpreadTicket's own validation. Reaching here means the repair
        # produced a shape the stack refuses, which is a bug in the resolver
        # rather than a bad proposal -- worth the log, not worth a 500.
        logger.warning("Resolved %s ticket was rejected by its own validation: %s", idea.strategy, exc)
        return None, _rejected(idea, f"could not be built into a valid {idea.strategy} ticket")

    try:
        spread = await service.preview(ticket)
    except TradingError as exc:
        # Level too low, over the notional ceiling, no market on a leg --
        # all real answers about this structure on this account.
        return None, _rejected(idea, str(exc))
    except Exception:
        logger.exception("Pricing a suggested %s for %s failed", idea.strategy, context.underlying)
        return None, _rejected(idea, "could not be priced right now")

    return {
        "strategy": idea.strategy,
        "strategy_label": STRATEGY_LABELS.get(idea.strategy, idea.strategy),
        "headline": idea.headline,
        "reason": idea.reason,
        "risk_note": idea.risk_note,
        "conviction": idea.conviction,
        # The ticket the frontend loads, and the economics the server
        # computed for it -- never the model's arithmetic.
        "ticket": ticket.model_dump(mode="json", exclude_none=True),
        "spread": spread.model_dump(mode="json"),
    }, None


async def suggest_options_ideas(
    anthropic_client,
    service,
    clients,
    underlying: str,
    *,
    gex_cache=None,
    iv_store=None,
    earnings_calendar=None,
    qty: int = DEFAULT_QTY,
    max_ideas: int = MAX_IDEAS,
) -> dict:
    """Structures for one underlying, each priced against the live chain.

    Raises whatever gather_context raises -- a symbol with no price, no
    expiries or no tradable strikes has no suggestion to make, and saying so
    beats an empty list that looks like "nothing appeals today"."""
    context = await gather_context(
        service,
        clients,
        underlying,
        gex_cache=gex_cache,
        iv_store=iv_store,
        earnings_calendar=earnings_calendar,
    )
    result = await generate_options_ideas(anthropic_client, context.payload, max_ideas=max_ideas)

    priced = await asyncio.gather(
        *(_price_one(service, context, idea, qty) for idea in result.ideas[:max_ideas])
    )

    return {
        "underlying": context.underlying,
        "spot": round(context.spot, 2),
        "ideas": [idea for idea, _ in priced if idea is not None],
        "rejected": [rejection for _, rejection in priced if rejection is not None],
        "disclaimer": result.disclaimer,
        # A short, honest note on what the model could see. Without it a
        # reader cannot tell a suggestion made with GEX, earnings and an IV
        # rank from one made with none of them.
        "context_used": {
            "expiries": [expiry.isoformat() for expiry in context.expiries],
            "has_gex": context.payload.get("gex") is not None,
            "has_news": context.payload.get("news") is not None,
            "has_earnings": context.payload.get("earnings") is not None,
            "iv_rank_samples": (context.payload.get("implied_vol") or {}).get("iv_rank_samples", 0),
        },
    }
