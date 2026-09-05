"""Gather, enumerate, price, rank, then preview the finalists -- the whole
Optimizer in one call, the way app.ai.options_suggest is the whole
suggestion. Four steps, three of them pure (app.options.optimizer):

    chains        what is listed, condensed to what can be traded
    enumerate     every shape worth pricing within the bounds
    price + rank  P/L at the target on the horizon, return on risk
    preview       the finalists through OptionsService.preview

Only the last step touches the account, and only for the dozen shapes
that survive: previewing every candidate would mean a broker round trip
per candidate for numbers that differ from the cheap pass only in the
account's collateral check. The finalists' card numbers are recomputed
from the previewed legs (their IVs, the ticket's own limit price), so a
card can never disagree with the ticket it loads into.

Nothing here asks a model. The ranking is a return on risk at one
assumed price and date with implied volatility held still; it is not a
probability and the response says so in as many words.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time, timezone

from pydantic import BaseModel, Field, model_validator

from app.ai.options_context import _chain_block, pick_expiries
from app.options.chain import ExpiryInfo
from app.options.models import STRATEGY_LABELS, Strategy
from app.options.optimizer import (
    DEFAULT_STRATEGIES,
    FINALISTS,
    Candidate,
    Skipped,
    Target,
    candidate_ticket,
    enumerate_candidates,
    filter_and_rank,
    legs_label,
    position_pnl,
    price_candidate,
)
from app.options.payoff import PayoffLeg
from app.services.market_clock import ET
from app.trading.errors import OrderRejected, TradingError

logger = logging.getLogger(__name__)

# Expiries to load around the horizon: the horizon's own, and two later
# ones so calendars and a longer-dated leg have something to price on.
EXPIRY_OFFSETS = (0, 14, 35)
MAX_EXPIRIES = 3

DISCLAIMER = (
    "Structures enumerated from the listed chain and priced through the ticket's own path. "
    "Return on risk is the P/L if the underlying is at the target on the horizon date with each "
    "leg's implied volatility unchanged, divided by what the account puts up -- not a probability, "
    "not advice. Check every quote yourself before acting on any of it."
)


class OptimizeRequest(BaseModel):
    underlying: str = Field(min_length=1, max_length=12)
    target_low: float = Field(gt=0)
    target_high: float | None = Field(default=None, gt=0)
    # Exactly one of the two: the horizon is a listed expiry, or a date
    # (which excludes expiries before it).
    horizon_expiry: date | None = None
    horizon_date: date | None = None
    # Per position, in dollars: the most the account puts up (debit or
    # collateral), and the largest defined loss to accept.
    budget: float | None = Field(default=None, gt=0)
    max_loss: float | None = Field(default=None, gt=0)
    strategies: list[Strategy] | None = None
    top_n: int = Field(default=8, ge=1, le=FINALISTS)

    @model_validator(mode="after")
    def _check(self) -> "OptimizeRequest":
        if (self.horizon_expiry is None) == (self.horizon_date is None):
            raise ValueError("give either horizon_expiry or horizon_date")
        if self.target_high is not None and self.target_high < self.target_low:
            raise ValueError("target_high must be at or above target_low")
        return self

    @property
    def target(self) -> Target:
        return Target(low=self.target_low, high=self.target_high if self.target_high is not None else self.target_low)

    @property
    def horizon(self) -> date:
        return self.horizon_expiry if self.horizon_expiry is not None else self.horizon_date  # type: ignore[return-value]


def _expiry_infos(raw: list) -> list[ExpiryInfo]:
    return [
        e
        if isinstance(e, ExpiryInfo)
        else ExpiryInfo(expiry=date.fromisoformat(e["expiry"]), dte=e["dte"], contract_count=e["contract_count"])
        for e in raw
    ]


def choose_expiries(infos: list[ExpiryInfo], horizon: date, today: date) -> list[date]:
    """The horizon's own expiry when it is listed, plus the listed expiries
    nearest the offsets after it -- never one before the horizon, since a
    leg that has expired by then has nothing to be worth."""
    usable = [e for e in infos if e.expiry >= horizon and e.dte >= 1 and e.contract_count > 0]
    if not usable:
        return []
    h_dte = (horizon - today).days
    chosen = pick_expiries(usable, targets=tuple(h_dte + off for off in EXPIRY_OFFSETS), limit=MAX_EXPIRIES)
    if horizon in {e.expiry for e in usable} and horizon not in chosen:
        chosen = sorted([horizon, *chosen])[:MAX_EXPIRIES]
    return chosen


def _signed_limit(spread) -> float:
    return spread.limit_price if spread.direction == "debit" else -spread.limit_price


def _repriced(cand: Candidate, spread, target: Target, horizon: datetime) -> tuple[list[float], float] | None:
    """The card's P/L points and risk from the previewed legs and limit --
    the same arithmetic as the cheap pass, on the numbers the ticket will
    actually carry. None when a previewed leg lacks an IV."""
    legs = [
        PayoffLeg(kind=leg.kind, strike=leg.strike, side=leg.side, ratio=leg.ratio_qty, expiry=leg.expiry, iv=leg.iv)
        for leg in spread.legs
    ]
    if cand.strategy == "covered_call":
        legs.append(PayoffLeg(kind="stock", strike=spread.spot, side="buy"))
    net = _signed_limit(spread)
    points = []
    for point in target.points():
        pnl = position_pnl(legs, net, point, horizon, spread.qty)
        if pnl is None:
            return None
        points.append(pnl)
    risk = spread.collateral if spread.collateral > 0 else (spread.max_loss or 0.0)
    return points, risk


async def optimize_structures(
    service,
    underlying: str,
    req: OptimizeRequest,
    *,
    today: date | None = None,
    now: datetime | None = None,
    warnings: list[str] | None = None,
) -> dict:
    """Ranked structures for `req.target` on `req.horizon`, each with the
    ticket the widget loads and the preview the ticket would show.

    Raises OrderRejected -- a 422 with a reason -- when there is nothing to
    enumerate from: no price, no listed expiry on or after the horizon, a
    horizon that is today (no greeks, no IV for a contract expiring today),
    or no tradable two-sided strikes."""
    underlying = underlying.upper()
    now = now or datetime.now(timezone.utc)
    today = today or now.astimezone(ET).date()

    spot = await service.spot(underlying)
    if not spot:
        raise OrderRejected(f"No price for {underlying}", field="underlying")

    infos = _expiry_infos((await service.expiries(underlying)).get("expiries", []))
    horizon = req.horizon
    if horizon <= today:
        raise OrderRejected(
            "The horizon must be after today: a contract expiring today has no greeks or implied volatility "
            "to price a horizon on.",
            field="horizon",
        )
    if req.horizon_expiry is not None and req.horizon_expiry not in {e.expiry for e in infos}:
        raise OrderRejected(f"{req.horizon_expiry.isoformat()} is not a listed expiry for {underlying}", field="horizon")
    expiries = choose_expiries(infos, horizon, today)
    if not expiries:
        raise OrderRejected(f"No listed expiry on or after {horizon.isoformat()} for {underlying}", field="horizon")

    rows_by_expiry_payload, _strikes, _chains = await _chain_block(service, underlying, expiries, today)
    rows_by_expiry = {expiry: block["strikes"] for expiry, block in rows_by_expiry_payload.items()}
    horizon_moment = datetime.combine(horizon, time(16, 0), tzinfo=ET)
    target = req.target
    strategies = frozenset(req.strategies) if req.strategies else DEFAULT_STRATEGIES

    raws, skipped = enumerate_candidates(rows_by_expiry, spot, target, strategies)
    candidates: list[Candidate] = []
    for raw in raws:
        priced = price_candidate(raw, rows_by_expiry, spot, target, horizon_moment)
        if isinstance(priced, str):
            skipped.add(priced)
        else:
            candidates.append(priced)
    skipped.scored = len(candidates)
    finalists, drop_reasons = filter_and_rank(candidates, budget=req.budget, max_loss=req.max_loss, top_k=FINALISTS)
    for reason, n in drop_reasons.items():
        skipped.add(reason, n)

    results: list[dict] = []
    rejected: list[dict] = []
    if finalists:
        account = await service.account()
        previews = await asyncio.gather(*(_preview_one(service, underlying, cand, account) for cand in finalists))
        for cand, (ticket, spread, reason) in zip(finalists, previews):
            if spread is None:
                rejected.append(
                    {
                        "strategy": cand.strategy,
                        "strategy_label": cand.label,
                        "expiry": cand.expiry.isoformat(),
                        "legs_label": cand.legs_label(),
                        "rejected_because": reason,
                    }
                )
                continue
            repriced = _repriced(cand, spread, target, horizon_moment)
            if repriced is None:
                rejected.append(
                    {
                        "strategy": cand.strategy,
                        "strategy_label": cand.label,
                        "expiry": cand.expiry.isoformat(),
                        "legs_label": cand.legs_label(),
                        "rejected_because": "a leg has no implied volatility to value it at the horizon",
                    }
                )
                continue
            points, risk = repriced
            pnl_min = min(points)
            if risk <= 0 or pnl_min <= 0:
                rejected.append(
                    {
                        "strategy": cand.strategy,
                        "strategy_label": cand.label,
                        "expiry": cand.expiry.isoformat(),
                        "legs_label": cand.legs_label(),
                        "rejected_because": "does not pay off at the target once priced at the ticket's limit",
                    }
                )
                continue
            results.append(
                {
                    "strategy": cand.strategy,
                    "strategy_label": cand.label,
                    "expiry": cand.expiry.isoformat(),
                    "legs_label": legs_label([leg for leg in _ticket_legs(spread)], cand.expiry),
                    "direction": spread.direction,
                    "net_price": round(_signed_limit(spread), 2),
                    "risk": round(risk, 2),
                    "pnl_at_target": round(sum(points) / len(points), 2),
                    "pnl_min": round(pnl_min, 2),
                    "pnl_mean": round(sum(points) / len(points), 2),
                    "pnl_max": round(max(points), 2),
                    "return_on_risk": round(pnl_min / risk, 4),
                    "max_profit": spread.max_profit,
                    "max_loss": spread.max_loss,
                    "breakevens": spread.breakevens,
                    "ticket": ticket.model_dump(mode="json", exclude_none=True),
                    "spread": spread.model_dump(mode="json"),
                }
            )

    results.sort(key=lambda r: (-r["return_on_risk"], -r["pnl_mean"], r["risk"]))
    results = results[: req.top_n]
    for i, r in enumerate(results, start=1):
        r["rank"] = i

    return {
        "underlying": underlying,
        "spot": round(spot, 2),
        "as_of": now.isoformat(timespec="seconds"),
        "target": {"low": target.low, "high": target.high},
        "horizon": {"date": horizon.isoformat(), "expiries_considered": [e.isoformat() for e in expiries]},
        "results": results,
        "rejected": rejected,
        "skipped": skipped.to_dict(),
        "warnings": list(warnings or []),
        "disclaimer": DISCLAIMER,
    }


def _ticket_legs(spread):
    """The previewed legs as TicketLegs, for the label (ratio and expiry
    included)."""
    from app.options.models import TicketLeg

    return [
        TicketLeg(kind=leg.kind, strike=leg.strike, side=leg.side, ratio=leg.ratio_qty, expiry=leg.expiry)
        for leg in spread.legs
    ]


async def _preview_one(service, underlying: str, cand: Candidate, account: dict):
    """(ticket, spread, reason) -- spread None with the reason when the
    finalist cannot be built or priced, same convention as the Idea tab's
    _price_one."""
    try:
        ticket = candidate_ticket(underlying, cand)
    except ValueError as exc:
        logger.warning("Optimizer candidate %s rejected by SpreadTicket: %s", cand.strategy, exc)
        return None, None, f"could not be built into a valid {STRATEGY_LABELS.get(cand.strategy, cand.strategy)} ticket"
    try:
        spread = await service.preview(ticket, account=account)
    except TradingError as exc:
        return ticket, None, str(exc)
    except Exception:
        logger.exception("Optimizer preview failed for %s %s", cand.strategy, underlying)
        return ticket, None, "could not be priced right now"
    return ticket, spread, None
