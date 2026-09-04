"""Everything a structure decision turns on, gathered for one underlying.

Picking an options structure is not the same problem as ranking movers
(app.ai.trade_ideas), and it needs a different set of facts: not just "is
this stock moving" but "what does its chain cost, where is it pinned, and
what is coming". So this assembles, for one symbol:

- the **chain** itself, condensed to strikes that are actually tradable in
  a handful of candidate expiries (app.ai.options_resolve.condense_chain) --
  without this the model would invent strikes, and with it the strikes it
  is shown are exactly the ones the resolver will accept;
- **GEX**: regime, gamma flip and the call/put walls, for any symbol now
  (app.market_data.gex_cache), with the sample the profile rests on;
- the **underlying**: spot, the day's range, VWAP, 30-minute momentum,
  the prior week's move and the average daily range;
- **chart levels**: the horizontal support/resistance the app already finds
  from hourly bars (app.market_data.levels), because a short strike sitting
  under a level that has held four times is a different trade from one in
  open air;
- **news**, the same catalyst check the scanner ideas use;
- **implied vol**: term structure, skew, and IV against realised vol -- plus
  an IV rank once there is enough history for one (app.options.iv_context,
  app.options.iv_history_store);
- **earnings**: the next report, and per candidate expiry whether the
  structure would be held through it.

Every one of these is best-effort in the manner the rest of the app has
established: each source is gathered inside its own try/except, a failure
logs and yields nothing, and **absent data is absent, never a negative
signal**. That rule is load-bearing here rather than decorative -- a model
told "IV rank: none" must not read that as "IV is not elevated", so the
payload carries explicit nulls with their reasons alongside (iv_rank_samples
saying how far the history has got, for instance) and the system prompt
says what they mean.

The chain is the one exception: without it there is no structure to
propose at all, so a chain failure raises rather than degrading.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime

import numpy as np

from app.ai.options_resolve import ChainStrikes, condense_chain
from app.market_data.bars import (
    get_daily_bars_multi,
    get_historical_bars,
    get_intraday_minute_bars_multi,
)
from app.market_data.gex_plan import compute_gex_plan
from app.market_data.levels import find_levels
from app.market_data.momentum import MOMENTUM_WINDOW, MOMENTUM_WINDOW_MINUTES, pct_change_over_window
from app.market_data.news import fetch_headlines, is_roundup_headline
from app.market_data.vwap import SessionVwapState
from app.options.chain import Chain, ExpiryInfo
from app.options.iv_context import iv_premium, realized_vol, term_structure
from app.trading.errors import OrderRejected

logger = logging.getLogger(__name__)

# Three expiries is the sweet spot: one to trade the next few days, one for
# the usual two-week structure, one far enough out for a calendar's back
# leg. More would mostly add payload; fewer would rule out whole strategies.
MAX_EXPIRIES = 3
# What the three are aimed at. Not thresholds -- the nearest listed expiry
# to each is taken, so a symbol with only monthlies still gets three.
EXPIRY_TARGET_DTE = (7, 21, 45)
# 0DTE is excluded outright: Alpaca cannot compute greeks for a contract
# expiring today (division by zero in the time-to-expiry term), so its
# strikes carry no delta and no IV -- nothing to reason with.
MIN_DTE = 1
# Enough sessions for a 20-day realised vol with room for holidays.
DAILY_LOOKBACK_DAYS = 60
# More than this and the levels stop being levels and start being a list of
# prices; the finder already returns them strongest first.
MAX_LEVELS_SHOWN = 6


@dataclass
class IdeaContext:
    """What the model is shown, plus what the resolver needs afterwards.

    `payload` is the JSON the prompt carries; `chains` and `strikes` never
    reach the model -- they are how the proposal that comes back is put onto
    real contracts."""

    underlying: str
    spot: float
    chains: dict[date, Chain]
    strikes: dict[date, ChainStrikes]
    payload: dict = field(default_factory=dict)

    @property
    def expiries(self) -> list[date]:
        return sorted(self.chains)


def pick_expiries(expiries: list[ExpiryInfo], *, targets=EXPIRY_TARGET_DTE, limit=MAX_EXPIRIES) -> list[date]:
    """The expiries to load: the listed one nearest each target DTE.

    Pure, and deliberately target-based rather than range-based: a symbol
    that only lists monthlies still yields several expiries instead of
    falling through empty ranges and returning nothing.

    Each target takes the nearest expiry *not already taken*. Without that
    exclusion a board of only two monthlies would answer with one expiry
    three times over -- and one expiry rules out the calendar and the
    diagonal outright, which is precisely the board on which a time spread
    is the interesting suggestion. So this returns as many distinct expiries
    as the board has, up to `limit`."""
    usable = sorted((e for e in expiries if e.dte >= MIN_DTE and e.contract_count > 0), key=lambda e: e.dte)
    if not usable:
        return []
    picked: list[date] = []
    for target in targets:
        remaining = [e for e in usable if e.expiry not in picked]
        if not remaining:
            break
        picked.append(min(remaining, key=lambda e: (abs(e.dte - target), e.dte)).expiry)
    return sorted(picked)[:limit]


async def _chain_block(service, underlying: str, expiries: list[date], today: date) -> tuple[dict, dict, list]:
    """(rows per expiry for the prompt, strikes per expiry for the resolver,
    the loaded chains). The one part that may not fail quietly: with no
    chain there is nothing to propose."""
    chains = await asyncio.gather(*(service.chain(underlying, expiry) for expiry in expiries))
    rows_by_expiry = {}
    strikes_by_expiry = {}
    for chain in chains:
        rows, strikes = condense_chain(chain)
        if not rows:
            continue
        rows_by_expiry[chain.expiry] = {
            "expiry": chain.expiry.isoformat(),
            "dte": (chain.expiry - today).days,
            "strikes": rows,
        }
        strikes_by_expiry[chain.expiry] = strikes
    if not rows_by_expiry:
        raise OrderRejected(
            f"No tradable, two-sided strikes near the money for {underlying} right now.", field="underlying"
        )
    return rows_by_expiry, strikes_by_expiry, [c for c in chains if c.expiry in rows_by_expiry]


async def _gex_block(gex_cache, underlying: str) -> dict | None:
    if gex_cache is None:
        return None
    try:
        reading = await gex_cache.reading(underlying)
    except Exception:
        logger.exception("GEX context failed for %s", underlying)
        return None
    if reading is None:
        return None
    plan = compute_gex_plan(reading)

    def _wall(row):
        return None if row is None else {"strike": row.strike, "net_gex": round(row.net_gex)}

    return {
        "regime": plan.regime,
        "near_flip": plan.near_flip,
        "gamma_flip_strike": plan.gamma_flip_strike,
        "call_wall": _wall(plan.call_wall),
        "put_wall": _wall(plan.put_wall),
        "net_gex": round(reading.net_gex),
        # How much this rests on. A wall built from a handful of contracts
        # is a real number and a weak one; the model is told which it has.
        "contracts_used": reading.contracts_used,
        "open_interest_used": reading.open_interest_used,
    }


async def _intraday_block(clients, underlying: str, spot: float) -> dict:
    """Session VWAP and momentum from one batch of today's minute bars --
    the same two numbers, computed the same way, that the scanner ideas
    use."""
    try:
        bars_by_symbol = await get_intraday_minute_bars_multi(clients, [underlying])
    except Exception:
        logger.exception("Intraday context failed for %s", underlying)
        return {}
    bars = bars_by_symbol.get(underlying) or []
    if not bars:
        return {}

    block: dict = {}
    vwap = SessionVwapState(symbol=underlying).seed_from_bars(bars)
    if vwap:
        block["vwap"] = round(vwap, 2)
        block["pct_from_vwap"] = round((spot - vwap) / vwap * 100, 2)
    momentum = pct_change_over_window(bars, MOMENTUM_WINDOW)
    if momentum is not None:
        block[f"pct_change_last_{MOMENTUM_WINDOW_MINUTES}m"] = momentum

    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    if highs and lows:
        block["day_high"] = round(max(highs), 2)
        block["day_low"] = round(min(lows), 2)
    return block


async def _daily_block(clients, underlying: str, today: date) -> tuple[dict, float | None]:
    """(multi-day context, 20-day realised vol). The realised number comes
    back separately because the IV block is what needs it."""
    try:
        bars_by_symbol = await get_daily_bars_multi(clients, [underlying], lookback_days=DAILY_LOOKBACK_DAYS)
    except Exception:
        logger.exception("Daily context failed for %s", underlying)
        return {}, None
    bars = bars_by_symbol.get(underlying) or []
    # Today's bar is still forming; a partial session is not a data point.
    completed = [b for b in bars if b.timestamp.date() < today]
    if len(completed) < 2:
        return {}, None

    closes = [b.close for b in completed]
    realized = realized_vol(closes)
    block: dict = {}
    if realized is not None:
        block["realized_vol_20d"] = round(realized, 4)
    if len(closes) >= 6:
        block["pct_change_prior_5d"] = round((closes[-1] / closes[-6] - 1) * 100, 2)
    ranges = [(b.high - b.low) / b.close * 100 for b in completed[-10:] if b.close]
    if ranges:
        block["avg_daily_range_pct"] = round(sum(ranges) / len(ranges), 2)
    return block, realized


async def _levels_block(clients, underlying: str) -> list[dict]:
    """Horizontal support/resistance -- the same levels the chart draws. A
    short strike tucked under a level that has held four times is a
    different proposition from one in open air, and that is not visible
    anywhere in a chain.

    Native hourly bars, as app.indicators.market_structure feeds it:
    find_levels' own docstring is explicit that changing the resolution
    changes the answer rather than sharpening it, so this must not be handed
    daily bars for convenience."""
    try:
        bars = await get_historical_bars(clients, underlying, "1Hour")
    except Exception:
        logger.exception("Levels context failed for %s", underlying)
        return []
    if not bars:
        return []
    try:
        found = find_levels(
            np.array([b.high for b in bars], dtype=float),
            np.array([b.low for b in bars], dtype=float),
            np.array([b.close for b in bars], dtype=float),
        )
    except Exception:
        logger.exception("Level detection failed for %s", underlying)
        return []
    return [{"price": round(price, 2), "visits": visits} for price, visits in found[:MAX_LEVELS_SHOWN]]


async def _news_block(clients, underlying: str) -> dict | None:
    try:
        headlines = await fetch_headlines(clients, [underlying])
    except Exception:
        logger.exception("News context failed for %s", underlying)
        return None
    headline = headlines.get(underlying)
    if not headline:
        return None
    # A "12 stocks moving today" mention is not a catalyst for this symbol.
    # Flagged rather than dropped: the model should see the headline and
    # know how much it is worth.
    return {"headline": headline, "is_roundup": is_roundup_headline(headline)}


async def _iv_block(chains, today: date, realized: float | None, iv_store, underlying: str) -> dict:
    structure = term_structure(chains, today)
    block: dict = {
        "term_structure": [row.to_dict() for row in structure],
        "realized_vol_20d": None if realized is None else round(realized, 4),
    }
    front = next((row for row in structure if row.atm_iv is not None), None)
    atm = front.atm_iv if front is not None else None
    block["atm_iv"] = None if atm is None else round(atm, 4)
    premium = iv_premium(atm, realized)
    block["iv_over_realized"] = None if premium is None else round(premium, 2)

    # The part that needs weeks of history. Reported as null with its own
    # sample count rather than approximated -- "no rank yet, 7 sessions
    # recorded" is a different statement from "IV is not elevated", and the
    # prompt is told so explicitly.
    block["iv_rank"] = None
    block["iv_rank_samples"] = 0
    if iv_store is not None and atm is not None:
        try:
            rank, samples = await iv_store.rank(underlying, atm)
            block["iv_rank"] = None if rank is None else rank.to_dict()
            block["iv_rank_samples"] = samples
            if front is not None:
                await iv_store.record(underlying, today, atm, front.dte)
        except Exception:
            logger.exception("IV rank lookup failed for %s", underlying)
    return block


async def _earnings_block(calendar, underlying: str, expiries: list[date]) -> dict | None:
    if calendar is None:
        return None
    try:
        earnings = await calendar.next_earnings(underlying)
    except Exception:
        logger.exception("Earnings lookup failed for %s", underlying)
        return None
    if earnings is None:
        return None
    block = earnings.to_dict()
    # The decision-relevant part is not the date, it is which candidate
    # expiries would be held through the report.
    block["held_through_by_expiry"] = {
        expiry.isoformat(): earnings.before(expiry) for expiry in expiries
    }
    return block


async def gather_context(
    service,
    clients,
    underlying: str,
    *,
    gex_cache=None,
    iv_store=None,
    earnings_calendar=None,
    today: date | None = None,
    now: datetime | None = None,
) -> IdeaContext:
    """One underlying's full picture. The chain is loaded first because
    everything else is enrichment around it -- and because a symbol with no
    usable chain has no structure to suggest, which is a refusal rather than
    a thinner answer."""
    underlying = underlying.upper()
    today = today or date.today()

    spot = await service.spot(underlying)
    if not spot:
        raise OrderRejected(f"No price for {underlying}", field="underlying")

    expiry_info = (await service.expiries(underlying)).get("expiries", [])
    infos = [
        e if isinstance(e, ExpiryInfo) else ExpiryInfo(
            expiry=date.fromisoformat(e["expiry"]), dte=e["dte"], contract_count=e["contract_count"]
        )
        for e in expiry_info
    ]
    chosen = pick_expiries(infos)
    if not chosen:
        raise OrderRejected(f"No listed expiries for {underlying} beyond today.", field="underlying")

    chain_rows, strikes, chains = await _chain_block(service, underlying, chosen, today)
    loaded = sorted(chain_rows)

    # Each of these fails on its own; a missing one is a thinner picture,
    # not a failed suggestion.
    gex, intraday, daily_pair, levels, news = await asyncio.gather(
        _gex_block(gex_cache, underlying),
        _intraday_block(clients, underlying, spot),
        _daily_block(clients, underlying, today),
        _levels_block(clients, underlying),
        _news_block(clients, underlying),
    )
    daily, realized = daily_pair

    iv, earnings = await asyncio.gather(
        _iv_block(chains, today, realized, iv_store, underlying),
        _earnings_block(earnings_calendar, underlying, loaded),
    )

    payload = {
        "underlying": underlying,
        "spot": round(spot, 2),
        "as_of": (now or datetime.now()).isoformat(timespec="seconds"),
        "expiries": [chain_rows[expiry] for expiry in loaded],
        "gex": gex,
        "underlying_context": {**intraday, **daily},
        "chart_levels": levels,
        "news": news,
        "implied_vol": iv,
        "earnings": earnings,
    }
    return IdeaContext(
        underlying=underlying,
        spot=spot,
        chains={chain.expiry: chain for chain in chains},
        strikes=strikes,
        payload=payload,
    )
