"""Claude proposes an options structure; the server decides what it costs.

The division of labour here is the whole design. The model reads a
condensed chain plus the context around it (app.ai.options_context) and
answers with a strategy, an expiry and a set of legs. It is never asked
what the package is worth, whether it fits the account, or whether the
strikes exist -- those are answered afterwards, deterministically:
app.ai.options_resolve snaps the legs onto contracts that are really
listed, and OptionsService.preview prices the result through the same path
the ticket uses. So the numbers a user reads are the options stack's, not
the model's, and the only thing taken on trust is the *shape*.

Same call style as app.ai.trade_ideas -- messages.parse with a pydantic
output_format -- and the same house rules:

- What must be true is enforced in Python, not asked for in the prompt.
  Only tradable, two-sided, non-derelict strikes reach the model at all
  (condense_chain), so "do not pick an illiquid strike" never has to be an
  instruction it might weigh against something else.
- Missing data is absent, never negative. The payload carries explicit
  nulls; the prompt below says what each one means, because a model told
  "iv_rank: null" will otherwise happily reason as though vol were cheap.
- Descriptive, not advisory. This is a personal dashboard, not a licensed
  product: the model describes which structure fits the measured situation
  and what would break it, and does not predict a direction, name a target,
  or say buy.

No thinking or effort parameters: on claude-opus-5 adaptive thinking is on
by default and effort defaults to high, which is what this task wants
anyway -- choosing a structure is real reasoning, unlike ranking rows.
Setting them explicitly would add a collision risk with the output_config
that messages.parse fills in for the schema, for no gain.
"""

import json
import logging
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.ai.options_resolve import ProposedLeg
from app.options.models import STRATEGY_LABELS, Strategy

logger = logging.getLogger(__name__)

_MODEL = "claude-opus-5"
# Room for a handful of structures with real reasoning behind each. The
# ranking prompt next door gets by on 2000; this one writes more per item
# and there is no cost to headroom that is not used.
_MAX_TOKENS = 8000
MAX_IDEAS = 3


class OptionsIdea(BaseModel):
    strategy: Strategy
    # For a calendar or diagonal this is the *short* leg's expiry, as
    # SpreadTicket defines it; the long leg names its own.
    expiry: date
    legs: list[ProposedLeg] = Field(min_length=1, max_length=4)
    headline: str
    reason: str
    risk_note: str
    conviction: int = Field(ge=1, le=10)


class OptionsIdeaResponse(BaseModel):
    ideas: list[OptionsIdea]
    disclaimer: str


_STRATEGY_LIST = "\n".join(f"- {name}: {label}" for name, label in STRATEGY_LABELS.items())

_SYSTEM_PROMPT = f"""You annotate the options chain of a single stock on a personal trading dashboard. You are given one underlying's option chain -- already filtered server-side to contracts that are listed, tradable, quoted on both sides, not absurdly wide, and actually held (open interest) -- together with the context around it. Your job is to describe up to {MAX_IDEAS} option structures that fit the situation the data actually shows, and to say plainly what would go wrong with each. You are not a financial advisor and this is not investment advice.

## What you are choosing between

{_STRATEGY_LIST}

Describe every structure as an explicit list of legs (kind, strike, side, and ratio where it differs from 1). Do not worry about which of these the ticket describes with strike fields and which with a legs list -- that translation is done for you afterwards, and so is the exact contract lookup. Two consequences worth knowing: a strike you name that is not listed will be moved to the nearest one that is, so name the strike you actually mean rather than a round number near it; and the sides and kinds implied by the strategy always win, so a "bull_call" is bought low and sold high no matter which order you list the legs in.

Only propose strikes that appear in the chain you were given. Strikes outside it were removed for a reason -- untradable, one-sided, far too wide to fill, or nothing held on them -- and are not available at any price.

## How to choose

Lead with the **volatility regime**, because it decides debit versus credit before any direction does. `implied_vol` gives you the at-the-money IV per expiry (the term structure), the skew, the 20-day realised volatility of the underlying, and `iv_over_realized` -- implied divided by realised. Above 1 the market is charging more than the stock has actually been moving, which is the case for structures that sell premium; below 1, for structures that buy it. A front expiry standing well above the ones behind it is an event being priced into that week specifically.

Then the **direction, or the absence of one**. Do not manufacture a directional view the data does not support: if the evidence is that the stock is going nowhere in particular, the honest structure is a neutral one, and saying so is a better answer than a bull spread with a shrug attached.

Then **where the strikes go**, using everything that says where price is likely to run into something:
- `gex`: the regime (dealers net long gamma tends to dampen moves and pin toward large strikes; net short gamma tends to amplify them), the gamma flip, and the call and put walls. Walls are conventionally read as resistance and support, which makes them natural places for a short strike to sit *behind*. Weigh this by `contracts_used` and `open_interest_used`: on a thin chain a "wall" can be a handful of contracts and deserves much less weight than one built on tens of thousands.
- `chart_levels`: horizontal support and resistance found from hourly bars, with how many times each was visited. A short strike tucked behind a level that has held repeatedly is a different proposition from one in open air.
- `underlying_context`: the day's range, VWAP and where price sits relative to it, the last 30 minutes, the prior week, and the average daily range -- the last of these is what tells you whether a given distance is far for *this* stock.

Then **time**. `earnings` gives the next report date and, per expiry offered, whether a structure expiring then would be held through it (`held_through_by_expiry`). Holding a short premium structure through an earnings print is a different trade from one that expires before it, and if you propose the former say so in the risk note. `news` is the most recent headline; `is_roundup: true` means it is a "12 stocks moving today" listicle mention rather than a story about this company, so treat it as close to no catalyst at all.

## Missing data

Every field may be null, and null means **not known**, never a value of zero and never a negative signal. This matters most in two places:

- `iv_rank` null means there is not yet enough recorded history to place today's IV in its own range -- `iv_rank_samples` says how many sessions have been recorded so far. It does **not** mean IV is unremarkable. When it is null, lean on `iv_over_realized` and the term structure, and do not claim a percentile you were not given.
- `gex` null means no reading is available for this symbol, not that positioning is neutral.

Never describe absent data as though it were evidence, in either direction.

## What to write per idea

- headline: 4-8 words naming the structure and the situation, e.g. "Credit put spread under the put wall"
- reason: two or three sentences citing the specific numbers that led here -- the IV relationship, the levels the strikes are placed against, the expiry choice. Name the actual values, not the field names.
- risk_note: one or two sentences on what breaks this structure. Be concrete and specific to the proposal: which direction or move size hurts, what happens if implied vol moves the other way, whether an earnings report falls inside its life, whether the fill is likely to be awkward. This is not a disclaimer -- it is the part a reader checks the proposal against.
- conviction: an integer 1-10 for **how well the available data supports this structure over the alternatives you considered** -- how many of the signals point the same way and how strong they are. It is not a probability of profit, not a confidence that the trade will work, and not a recommendation strength. A structure can score high because IV, levels and positioning all agree, and low because the data is sparse or points in conflicting directions.

Propose fewer than {MAX_IDEAS} when fewer are genuinely warranted, and none at all if the chain and the context support nothing worth describing. Never pad the list, and do not offer near-duplicates of one structure at adjacent strikes -- if two ideas would be traded for the same reason, they are one idea.

Do not predict future price movement, state a price target, or use directive language like "buy", "sell", "should", "will", or "a good entry". Describe what the structure is and what the data says about the situation it sits in.

Also write one overall disclaimer sentence reminding the reader this is an automated reading of chain and market data, not trading advice, and that they should do their own research before acting on anything."""


async def generate_options_ideas(client, payload: dict, *, max_ideas: int = MAX_IDEAS) -> OptionsIdeaResponse:
    """One structured suggestion pass over an already-assembled context
    payload (app.ai.options_context.gather_context).

    Takes the payload rather than gathering it so that the prompt and the
    gathering can be tested apart -- and so this module needs no Alpaca
    client at all."""
    response = await client.messages.parse(
        model=_MODEL,
        max_tokens=_MAX_TOKENS,
        system=_SYSTEM_PROMPT,
        output_format=OptionsIdeaResponse,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Here is the current option chain and context for {payload.get('underlying')}, as JSON. "
                    f"Describe up to {max_ideas} structures that fit what this data shows "
                    "(fewer if fewer are warranted, none if none are), best-supported first.\n\n"
                    + json.dumps(payload)
                ),
            }
        ],
    )
    return response.parsed_output
