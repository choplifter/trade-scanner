"""One reporter, evaluated: gather the signals, score the families, price
the picked ones.

This is the Earnings screen's expanded row. It is the same shape as
app.ai.options_suggest -- gather, decide, price -- with the deciding step
a rule matrix (app.options.strategy_select) instead of a model, because
the question "which families are worth pricing into this print" is one a
table of thresholds answers reproducibly and a model answers differently
every time.

Two things it does that the plain Optimizer does not, both of them
consequences of an earnings print:

- **It loads only the post-report expiry** (`horizon_only`). The
  Optimizer's ranking values legs at unchanged implied volatility, which
  is exactly the assumption a print breaks; with every leg expiring on
  the horizon the P/L is intrinsic and the assumption does no work.
- **It widens the strike window and the condor delta band** in
  proportion to the implied move. At 200 % implied volatility the
  0.40-delta call is further out than the default 12 % window reaches,
  so the default bounds return no condor at all -- not because none is
  worth having, but because none was enumerated.

Best-effort throughout, in the manner of options_context: each source in
its own try/except, a failure is a null field with its reason, and the
matrix never reads a null as evidence.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone

from app.ai.options_context import _chain_block, _daily_block, _gex_block, pick_expiries
from app.ai.options_resolve import MAX_SPREAD_FRACTION, MIN_OPEN_INTEREST, _quote_is_offerable
from app.market_data.earnings_screen import gather_symbol_facts, implied_move_pct
from app.options.chain import ExpiryInfo
from app.options.iv_context import iv_premium, term_structure
from app.options.optimize import OptimizeRequest, optimize_structures
from app.options.strategy_select import (
    FAMILY_GROUPS,
    MIN_WING_STRIKES,
    Signals,
    pick_families,
    score_families,
    target_policy,
    warnings_for,
)
from app.services.market_clock import ET
from app.trading.errors import OrderRejected, TradingError

logger = logging.getLogger(__name__)

# How far past the horizon the second chain is read, for the term
# structure the calendar rule turns on. A month out is past the print and
# past the front expiry's event premium.
BACK_EXPIRY_OFFSET_DAYS = 30
# The strike window, as a multiple of the implied move, and its bounds.
# Twice the move puts the wings of a condor comfortably inside; the floor
# is condense_chain's own default so a calm name is unaffected.
WINDOW_MOVE_MULTIPLE = 2.0
WINDOW_MIN = 0.12
WINDOW_MAX = 0.40
# Above this implied move the delta band is widened: at a 10 % expected
# move the whole chain's deltas are compressed toward the money.
WIDE_BAND_MOVE_PCT = 10.0
WIDE_BAND_DELTA_MAX = 0.45
TOP_N = 8

DISCLAIMER = (
    "Signals read from the listed chain, the stock's own past reports, and this app's recorded history; "
    "families scored by a fixed rule table, then priced by the Optimizer through the ticket's own path. "
    "A score says how many of the available signals point at a family, not how likely any trade is to work. "
    "Nothing here is advice, and every number should be checked against the live market before acting on it."
)


def _infos(raw: list) -> list[ExpiryInfo]:
    out: list[ExpiryInfo] = []
    for e in raw:
        if isinstance(e, ExpiryInfo):
            out.append(e)
        else:
            out.append(
                ExpiryInfo(expiry=date.fromisoformat(e["expiry"]), dte=e["dte"], contract_count=e["contract_count"])
            )
    return out


def choose_horizon(infos: list[ExpiryInfo], report_date: date | None, today: date) -> date | None:
    """The first listed expiry that survives the report -- the one whose
    price contains the event and whose settlement is after it. Without a
    known report date, the first expiry with a day left."""
    usable = [e for e in infos if e.dte >= 1 and e.contract_count > 0]
    if not usable:
        return None
    usable.sort(key=lambda e: e.expiry)
    if report_date is None:
        return usable[0].expiry
    after = [e for e in usable if e.expiry >= report_date]
    return after[0].expiry if after else usable[-1].expiry


def wing_counts(chain, spot: float, move: float) -> tuple[int, int]:
    """(quotable puts below, quotable calls above) the expected move --
    what a four-legged structure needs to exist at all.

    Counted on the *raw* chain rather than the condensed rows, applying
    condense_chain's own offerability test strike by strike. Counting the
    condensed rows would answer a different question and always answer it
    the same way: they are cropped to a window around spot (12 % by
    default) and then to the 24 strikes nearest it, so on any symbol whose
    expected move is larger than that window nothing is ever beyond the
    move and every chain looks wingless."""
    if spot <= 0 or move <= 0 or chain is None:
        return 0, 0
    low, high = spot * (1 - move), spot * (1 + move)
    puts = 0
    calls = 0
    for row in getattr(chain, "rows", []):
        if row.strike <= low and _quote_is_offerable(row.put, min_oi=MIN_OPEN_INTEREST, max_spread_frac=MAX_SPREAD_FRACTION):
            puts += 1
        if row.strike >= high and _quote_is_offerable(row.call, min_oi=MIN_OPEN_INTEREST, max_spread_frac=MAX_SPREAD_FRACTION):
            calls += 1
    return puts, calls


def _window(move_pct: float | None) -> float | None:
    if not move_pct:
        return None
    return round(min(WINDOW_MAX, max(WINDOW_MIN, WINDOW_MOVE_MULTIPLE * move_pct / 100.0)), 4)


def _delta_max(move_pct: float | None) -> float | None:
    if move_pct is not None and move_pct >= WIDE_BAND_MOVE_PCT:
        return WIDE_BAND_DELTA_MAX
    return None


async def _run_optimizer(service, symbol: str, req: OptimizeRequest, *, today, now, earnings_calendar) -> dict:
    """The Optimizer's answer, or the reason there is none. A rejection
    is this run's answer, not the whole evaluation's: the signals and the
    scores stand either way."""
    try:
        return await optimize_structures(service, symbol, req, today=today, now=now, earnings_calendar=earnings_calendar)
    except TradingError as exc:
        return {"error": str(exc)}
    except Exception:
        logger.exception("Earnings evaluation: optimizer failed for %s", symbol)
        return {"error": "could not price these structures right now"}


async def evaluate_symbol(
    service,
    clients,
    symbol: str,
    *,
    gex_cache=None,
    iv_store=None,
    earnings_calendar=None,
    market_conditions=None,
    reported: bool = False,
    today: date | None = None,
    now: datetime | None = None,
) -> dict:
    """Signals, scores and priced structures for one reporter.

    Raises OrderRejected -- a 422 with a reason -- only when there is
    nothing to work from: no price, no listed expiry, no tradable strikes.
    """
    symbol = symbol.upper()
    now = now or datetime.now(timezone.utc)
    today = today or now.astimezone(ET).date()

    spot = await service.spot(symbol)
    if not spot:
        raise OrderRejected(f"No price for {symbol}", field="underlying")
    infos = _infos((await service.expiries(symbol)).get("expiries", []))

    report_date = None
    days_until = None
    if earnings_calendar is not None:
        try:
            upcoming = await earnings_calendar.next_earnings(symbol)
        except Exception:
            logger.exception("Earnings evaluation: report date lookup failed for %s", symbol)
            upcoming = None
        if upcoming is not None:
            report_date = upcoming.report_date
            days_until = upcoming.days_until

    horizon = choose_horizon(infos, report_date, today)
    if horizon is None:
        raise OrderRejected(f"No listed expiry with a day left for {symbol}", field="underlying")
    horizon_dte = (horizon - today).days
    later = [e for e in infos if e.expiry > horizon and e.dte >= 1 and e.contract_count > 0]
    back = pick_expiries(later, targets=(horizon_dte + BACK_EXPIRY_OFFSET_DAYS,), limit=1)
    wanted = [horizon, *back]

    # A first, default-window read: the term structure and the liquidity
    # counts come from it, and it tells us the implied move that sizes
    # the wider window the optimizer runs get.
    rows_payload, _strikes, chains = await _chain_block(service, symbol, wanted, today)
    structure = term_structure(chains, today)
    front_row = next((r for r in structure if r.expiry == horizon), None)
    back_row = next((r for r in structure if r.expiry != horizon and r.atm_iv), None)
    front_iv = front_row.atm_iv if front_row is not None else None
    move_pct = implied_move_pct(front_iv, horizon_dte)

    horizon_chain = next((c for c in chains if c.expiry == horizon), None)
    puts_below, calls_above = wing_counts(horizon_chain, spot, (move_pct or 0.0) / 100.0)

    daily, realized = await _daily_block(clients, symbol, today) if clients is not None else ({}, None)
    gex = await _gex_block(gex_cache, symbol) if gex_cache is not None else None

    iv_rank_pct = None
    iv_rank_samples = 0
    if iv_store is not None and front_iv is not None:
        try:
            rank, iv_rank_samples = await iv_store.rank(symbol, front_iv)
            iv_rank_pct = None if rank is None else rank.percent
            await iv_store.record(symbol, today, front_iv, horizon_dte)
        except Exception:
            logger.exception("Earnings evaluation: IV rank failed for %s", symbol)

    facts = await gather_symbol_facts(
        [symbol], service=service, clients=clients, earnings_calendar=earnings_calendar, today=today
    )
    fact = facts.get(symbol)

    level = getattr(market_conditions, "level", None)
    signals = Signals(
        spot=spot,
        implied_move_pct=None if move_pct is None else round(move_pct, 2),
        hist_median_pct=fact.hist_median_pct if fact else None,
        hist_max_pct=fact.hist_max_pct if fact else None,
        samples=fact.samples if fact else 0,
        iv_over_realized=(lambda p: None if p is None else round(p, 2))(iv_premium(front_iv, realized)),
        iv_rank_pct=iv_rank_pct,
        front_iv=front_iv,
        back_iv=back_row.atm_iv if back_row is not None else None,
        skew=front_row.skew if front_row is not None else None,
        gex_regime=(gex or {}).get("regime"),
        near_flip=(gex or {}).get("near_flip"),
        call_wall=((gex or {}).get("call_wall") or {}).get("strike"),
        put_wall=((gex or {}).get("put_wall") or {}).get("strike"),
        offerable_puts_below=puts_below,
        offerable_calls_above=calls_above,
        wing_room=(puts_below >= MIN_WING_STRIKES and calls_above >= MIN_WING_STRIKES) if move_pct else None,
        market_level=level,
        reported=reported,
    )

    scores = score_families(signals)
    picks = pick_families(scores, market_level=level)
    policy = target_policy(signals, picks)
    warnings = warnings_for(signals)
    if fact is not None and fact.history_note:
        warnings.append(f"Past-report history: {fact.history_note}.")

    window = _window(move_pct)
    delta_max = _delta_max(move_pct)
    runs: dict[str, dict | None] = {"neutral": None, "volatility": None}
    requests: list[tuple[str, OptimizeRequest]] = []
    if policy["neutral"]:
        move = policy["neutral"]["move"]
        requests.append(
            (
                "neutral",
                OptimizeRequest(
                    underlying=symbol,
                    target_low=round(spot * (1 - move), 2),
                    target_high=round(spot * (1 + move), 2),
                    outlook="neutral",
                    horizon_expiry=horizon,
                    strategies=policy["neutral"]["families"],
                    horizon_only=True,
                    strike_pct_range=window,
                    condor_short_delta_max=delta_max,
                    preference=0.5,
                    top_n=TOP_N,
                ),
            )
        )
    if policy["volatility"]:
        move = policy["volatility"]["move"]
        requests.append(
            (
                "volatility",
                OptimizeRequest(
                    underlying=symbol,
                    target_points=[round(spot * (1 - move), 2), round(spot * (1 + move), 2)],
                    outlook="directional",
                    horizon_expiry=horizon,
                    strategies=policy["volatility"]["families"],
                    horizon_only=True,
                    strike_pct_range=window,
                    condor_short_delta_max=delta_max,
                    preference=0.5,
                    top_n=TOP_N,
                ),
            )
        )
    if requests:
        answers = await asyncio.gather(
            *(
                _run_optimizer(service, symbol, req, today=today, now=now, earnings_calendar=earnings_calendar)
                for _, req in requests
            )
        )
        for (group, _), answer in zip(requests, answers):
            runs[group] = answer

    return {
        "symbol": symbol,
        "spot": round(spot, 2),
        "as_of": now.isoformat(timespec="seconds"),
        "report_date": None if report_date is None else report_date.isoformat(),
        "days_until": days_until,
        "reported": reported,
        "horizon_expiry": horizon.isoformat(),
        "horizon_dte": horizon_dte,
        "back_expiry": back[0].isoformat() if back else None,
        "signals": {
            **signals.to_dict(),
            "iv_rank_samples": iv_rank_samples,
            "realized_vol_20d": daily.get("realized_vol_20d"),
            "avg_daily_range_pct": daily.get("avg_daily_range_pct"),
            "term_structure": [row.to_dict() for row in structure],
            "gex": gex,
        },
        "market": {
            "level": level,
            "reasons": list(getattr(market_conditions, "reasons", []) or []),
        },
        "scores": [_score_payload(score, horizon, back) for score in scores],
        "picks": picks,
        "target": policy,
        "warnings": warnings,
        "optimizer": runs,
        "disclaimer": DISCLAIMER,
    }


def _score_payload(score, horizon: date, back: list[date]) -> dict:
    """A score, plus -- for the families this path does not price -- the
    Optimizer request that would price it, so the widget can hand the
    reader straight over rather than leaving a dead end."""
    payload = score.to_dict()
    group = FAMILY_GROUPS[score.family]
    payload["priced"] = group in ("neutral", "volatility")
    payload["open_optimizer"] = None
    if group == "time":
        payload["priced"] = False
        payload["not_priced_because"] = (
            "a calendar's edge is the front expiry's implied volatility collapsing on the report, and the "
            "Optimizer values the back leg at today's implied volatility, which will not survive it"
        )
        payload["open_optimizer"] = {
            "outlook": "neutral",
            "strategies": ["calendar"],
            "horizon_expiry": (back[0] if back else horizon).isoformat(),
        }
    elif group == "directional":
        payload["not_priced_because"] = "a direction is yours to pick; these signals do not supply one"
        bullish = score.family in ("bull_call", "long_call")
        payload["open_optimizer"] = {
            "outlook": "bullish" if bullish else "bearish",
            "strategies": [score.family],
            "horizon_expiry": horizon.isoformat(),
        }
    return payload


__all__ = ["DISCLAIMER", "choose_horizon", "evaluate_symbol", "wing_counts"]
