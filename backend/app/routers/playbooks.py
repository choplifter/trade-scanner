"""Playbook campaigns: the scripts on offer, the campaigns running, their
proposals -- and the synthetic backtest. Simulation account only for now:
the runner needs the simulated book's orders to reconcile, and the Paper
lift (Alpaca's activities for assignments) is a later step, so anything but
`account=sim` is a 422 with code sim_only rather than a wrong answer.

Same conventions as the other trading routers: TradingError -> 422 with
{code, message, field}, anything unexpected -> 502.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.auth.dependency import get_current_user
from app.playbooks import loader
from app.playbooks.backtest import BacktestRequest, run_backtest
from app.playbooks.store import ACTIVE, CLOSED, PAUSED, PlaybookStore
from app.routers.trading_options import _service as _paper_service
from app.routers.trading_sim_options import _service as _sim_service
from app.trading.errors import TradingError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/trading/options/playbooks", tags=["playbooks"])

# The accounts a campaign can run in. Live is left out on purpose: a
# campaign there would propose real orders on a real account, and the
# propose-not-trade posture wants a deliberate later decision for that.
ALLOWED_ACCOUNTS = ("sim", "paper")
EVENTS_SHOWN = 50


async def _service_for(request: Request, user: dict, account: str):
    """The options service the campaign's account is read through: the
    simulated book, or this user's paper broker (the same resolution the
    paper options router does)."""
    if account == "sim":
        return await _sim_service(request, user)
    return await _paper_service(request, user)


def _store(request: Request) -> PlaybookStore:
    store = getattr(request.app.state, "playbook_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Playbook store not initialised")
    return store


def _runner(request: Request):
    runner = getattr(request.app.state, "playbook_runner", None)
    if runner is None:
        raise HTTPException(status_code=503, detail="Playbook runner not initialised")
    return runner


def _sim_only(account: str) -> None:
    """Historically the sim-only gate; now the allowed-accounts gate, kept
    under its name at the call sites. Live stays out (see ALLOWED_ACCOUNTS)."""
    if account not in ALLOWED_ACCOUNTS:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "account_not_allowed",
                "message": "Playbooks run in the Simulation and Paper accounts; the live account is not offered.",
                "field": "account",
            },
        )


async def _with_events(store: PlaybookStore, campaign: dict) -> dict:
    events = await store.events(campaign["id"], limit=EVENTS_SHOWN)
    return {**campaign, "events": events}


@router.get("/scripts")
async def list_scripts(user: dict = Depends(get_current_user)) -> dict:
    playbooks, errors = loader.load_playbooks()
    return {
        "playbooks": [p.to_dict() for p in playbooks],
        "errors": [{"filename": e.filename, "error": e.error} for e in errors],
    }


@router.get("/campaigns")
async def list_campaigns(
    request: Request,
    account: str = Query(default="sim"),
    include_closed: bool = Query(default=False),
    user: dict = Depends(get_current_user),
) -> dict:
    _sim_only(account)
    store = _store(request)
    campaigns = await store.list_for_user(user["id"], account, include_closed=include_closed)
    return {"campaigns": [await _with_events(store, c) for c in campaigns]}


class CampaignCreate(BaseModel):
    account: str = "sim"
    symbol: str = Field(min_length=1, max_length=12)
    playbook: str = Field(min_length=1, max_length=64)
    params: dict = Field(default_factory=dict)
    auto_execute: bool = False


@router.post("/campaigns")
async def create_campaign(body: CampaignCreate, request: Request, user: dict = Depends(get_current_user)) -> dict:
    _sim_only(body.account)
    store = _store(request)
    playbook = loader.get_playbook(body.playbook)
    if playbook is None:
        raise HTTPException(status_code=422, detail={"code": "no_playbook", "message": f"No playbook named {body.playbook!r}.", "field": "playbook"})
    try:
        params = playbook.resolve_params(body.params)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail={"code": "bad_params", "message": str(exc), "field": "params"}) from exc
    now = datetime.now(UTC)
    try:
        campaign = await store.create(user["id"], body.account, body.symbol, playbook.stem, params, auto_execute=body.auto_execute, now=now)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "campaign_exists", "message": str(exc), "field": "symbol"}) from exc
    await store.add_event(campaign["id"], user["id"], "started", at=now, note=f"{playbook.name} started with {params}")
    try:
        service = await _service_for(request, user, body.account)
        await _runner(request).run_one(campaign, service, now=now, force=True)
    except HTTPException:
        raise
    except TradingError as exc:
        await store.update(campaign["id"], proposal_error=exc.to_detail().get("message"), now=now)
    except Exception as exc:
        # The campaign exists either way; the card says why its first
        # proposal is missing rather than showing an empty slot.
        logger.exception("First proposal failed for campaign %s", campaign["id"])
        await store.update(campaign["id"], proposal_error=f"{type(exc).__name__}: {exc}", now=now)
    return await _with_events(store, await store.get(campaign["id"]) or campaign)


class CampaignPatch(BaseModel):
    status: Literal["active", "paused", "closed"] | None = None
    params: dict | None = None
    auto_execute: bool | None = None


async def _owned(store: PlaybookStore, campaign_id: str, user: dict) -> dict:
    campaign = await store.get(campaign_id)
    if campaign is None or int(campaign["user_id"]) != int(user["id"]):
        raise HTTPException(status_code=404, detail="No such campaign")
    return campaign


@router.patch("/campaigns/{campaign_id}")
async def patch_campaign(campaign_id: str, body: CampaignPatch, request: Request, user: dict = Depends(get_current_user)) -> dict:
    store = _store(request)
    campaign = await _owned(store, campaign_id, user)
    now = datetime.now(UTC)
    fields: dict = {}
    if body.params is not None:
        playbook = loader.get_playbook(campaign["playbook"])
        if playbook is None:
            raise HTTPException(status_code=422, detail={"code": "no_playbook", "message": "The campaign's playbook is not available.", "field": "params"})
        try:
            fields["params"] = playbook.resolve_params(body.params)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail={"code": "bad_params", "message": str(exc), "field": "params"}) from exc
    if body.auto_execute is not None:
        fields["auto_execute"] = body.auto_execute
        if body.auto_execute:
            fields["executed_order_id"] = None
    if fields:
        await store.update(campaign_id, now=now, **fields)
    if body.status is not None and body.status != campaign["status"]:
        if campaign["status"] == CLOSED:
            raise HTTPException(status_code=422, detail={"code": "closed", "message": "A closed campaign stays closed; start a new one.", "field": "status"})
        await store.set_status(campaign_id, body.status, now=now)
        kind = {ACTIVE: "resumed", PAUSED: "paused", CLOSED: "campaign_closed"}[body.status]
        await store.add_event(campaign_id, user["id"], kind, at=now, note=None if kind != "campaign_closed" else "positions left as they are")
    return await _with_events(store, await store.get(campaign_id) or campaign)


@router.post("/campaigns/{campaign_id}/propose")
async def propose_now(campaign_id: str, request: Request, user: dict = Depends(get_current_user)) -> dict:
    store = _store(request)
    campaign = await _owned(store, campaign_id, user)
    _sim_only(campaign["account"])
    try:
        service = await _service_for(request, user, campaign["account"])
        await _runner(request).run_one(campaign, service, now=datetime.now(UTC), force=True)
    except HTTPException:
        raise
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except Exception as exc:
        logger.exception("Proposal failed for campaign %s", campaign_id)
        await store.update(campaign_id, proposal_error=f"{type(exc).__name__}: {exc}")
        raise HTTPException(status_code=502, detail=f"Failed to compute the proposal: {type(exc).__name__}: {exc}")
    return await _with_events(store, await store.get(campaign_id) or campaign)


@router.post("/backtest")
async def backtest(body: BacktestRequest, request: Request, user: dict = Depends(get_current_user)) -> dict:
    """Walk a playbook over months of daily closes with Black-Scholes chains
    built from realized volatility -- synthetic prices, and the response
    says so. See app.playbooks.backtest."""
    clients = getattr(request.app.state, "alpaca_clients", None)
    if clients is None:
        raise HTTPException(status_code=503, detail="Market data not configured")
    try:
        return await run_backtest(clients, body, earnings_calendar=getattr(request.app.state, "earnings_calendar", None))
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except Exception:
        logger.exception("Playbook backtest failed for %s", body.symbol)
        raise HTTPException(status_code=502, detail="Failed to run the backtest")


class SignalBacktestBody(BaseModel):
    """Symbols to walk (default: the user's watchlist) and the methods."""

    symbols: list[str] | None = Field(default=None, max_length=60)
    variants: list[Literal["trend", "band", "vol"]] = Field(default_factory=lambda: ["trend", "band", "vol"])
    starting_equity: float = Field(default=100_000.0, gt=0)
    # Walk only these sessions: set rules on one stretch, check on another.
    start: date | None = None
    end: date | None = None
    # No new premium sold while SPY is below its 200-day average or its IV
    # spikes (app.options.signal_backtest.risk_off).
    market_filter: bool = False


# Closes behind the first priced session: the 200-day trend filter needs
# ~290 calendar days before the IV history starts. The fetch reaches back
# that far behind the oldest IV reading of any symbol walked -- a year for
# most, to 2018 for those seeded from Barchart's full export.
SIGNAL_TREND_LEAD_DAYS = 300


@router.post("/signal-backtest")
async def signal_backtest(body: SignalBacktestBody, request: Request, user: dict = Depends(get_current_user)) -> dict:
    """Donchian breakout and Bollinger dip, traded as volatility-picked
    verticals under hard risk rules, over the sessions the IV history
    covers. Synthetic option prices at the real daily IV -- see
    app.options.signal_backtest for exactly what that does and does not
    capture."""
    import asyncio

    from app.market_data.bars import get_daily_bars_multi
    from app.playbooks.backtest import closes_by_session
    from app.options.signal_backtest import summarize, walk

    clients = getattr(request.app.state, "alpaca_clients", None)
    iv_store = getattr(request.app.state, "iv_history_store", None)
    if clients is None or iv_store is None:
        raise HTTPException(status_code=503, detail="Market data or IV history not configured")
    symbols = body.symbols
    if not symbols:
        watchlist = getattr(request.app.state, "watchlist_store", None)
        symbols = await watchlist.list_symbols(user["id"]) if watchlist is not None else []
    symbols = [s.upper() for s in symbols][:60]
    if not symbols:
        raise HTTPException(status_code=422, detail="No symbols: pass some, or add them to the watchlist")
    try:
        ivs = {s: await asyncio.to_thread(iv_store.series_sync, s) for s in symbols}
        market_iv = ivs.get("SPY") or await asyncio.to_thread(iv_store.series_sync, "SPY")
        oldest = min((min(series) for series in ivs.values() if series), default=None)
        lookback = ((date.today() - oldest).days if oldest else 365) + SIGNAL_TREND_LEAD_DAYS
        bars = await get_daily_bars_multi(clients, sorted(set(symbols) | {"SPY"}), lookback_days=lookback)
        closes = {s.upper(): closes_by_session(rows) for s, rows in bars.items()}
    except Exception:
        logger.exception("Signal backtest data failed")
        raise HTTPException(status_code=502, detail="Failed to load closes or IV history")
    priced = {s: series for s, series in ivs.items() if len(series) >= 60 and s in closes}
    out = {
        "synthetic": True,
        "symbols": sorted(priced),
        "without_iv_history": sorted(set(symbols) - set(priced)),
        "first_session": min((min(v) for v in priced.values()), default=None),
        "last_session": max((max(v) for v in priced.values()), default=None),
        "results": [],
    }
    for variant in body.variants:
        result = walk(
            variant, closes, priced, starting_equity=body.starting_equity, start=body.start, end=body.end,
            market_filter=body.market_filter, market=(closes.get("SPY", []), market_iv),
        )
        # "trade_log", not "trades": the summary's own "trades" is the count.
        out["results"].append({**summarize(result, body.starting_equity), "curve": result.curve, "trade_log": result.trades})
    return out


class DailyMethodBody(BaseModel):
    """Which account to read positions and equity from, and the symbols to
    consider (default: the user's watchlist)."""

    account: Literal["paper", "sim"] = "paper"
    symbols: list[str] | None = Field(default=None, max_length=60)


# Closes behind today: the 200-day trend filter and a 20-session realised vol.
DAILY_BARS_LOOKBACK_DAYS = 330


@router.post("/daily-method")
async def daily_method(body: DailyMethodBody, request: Request, user: dict = Depends(get_current_user)) -> dict:
    """Today's proposals under the daily method (app.options.daily_method):
    the market light, exits for held bull put spreads, and entries across
    the watchlist. Proposes only -- nothing is placed."""
    import asyncio

    from app.market_data.bars import get_daily_bars_multi
    from app.options.daily_method import evaluate, held_from_groups
    from app.options.screener import atm_iv_of, pick_expiry
    from app.playbooks.backtest import closes_by_session
    from app.services.market_clock import ET

    clients = getattr(request.app.state, "alpaca_clients", None)
    iv_store = getattr(request.app.state, "iv_history_store", None)
    if clients is None or iv_store is None:
        raise HTTPException(status_code=503, detail="Market data or IV history not configured")
    service = await _service_for(request, user, body.account)
    symbols = body.symbols
    if not symbols:
        watchlist = getattr(request.app.state, "watchlist_store", None)
        symbols = await watchlist.list_symbols(user["id"]) if watchlist is not None else []
    symbols = list(dict.fromkeys(s.upper() for s in symbols))[:60]
    if not symbols:
        raise HTTPException(status_code=422, detail="No symbols: pass some, or add them to the watchlist")
    today = datetime.now(UTC).astimezone(ET).date()
    everything = sorted(set(symbols) | {"SPY"})

    try:
        bars = await get_daily_bars_multi(clients, everything, lookback_days=DAILY_BARS_LOOKBACK_DAYS)
        closes = {s.upper(): [c for _, c in closes_by_session(rows)] for s, rows in bars.items()}
        series = {s: await asyncio.to_thread(iv_store.series_sync, s) for s in everything}
        account = await service.account()
        groups = await service.spreads()
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except Exception:
        logger.exception("Daily method data failed")
        raise HTTPException(status_code=502, detail="Failed to load closes, IV history or the account")

    chains, atm, chain_errors = {}, {}, {}
    for symbol in everything:
        try:
            strip = await service.expiries(symbol)
            listed = [date.fromisoformat(e["expiry"]) for e in strip.get("expiries", [])]
            expiry = pick_expiry(listed, today, 30, 60)
            if expiry is None:
                chain_errors[symbol] = "no listed expiry 30-60 days out"
                continue
            chain = await service.chain(symbol, expiry)
            chains[symbol] = chain
            atm[symbol] = atm_iv_of(chain.rows, chain.spot)
        except Exception as exc:  # one symbol failing must not stop the rest
            chain_errors[symbol] = f"{type(exc).__name__}: {exc}"

    history = {s: [v for d, v in sorted(series[s].items()) if d < today] for s in everything}
    spy_ivs = history["SPY"] + ([atm["SPY"]] if atm.get("SPY") else [])
    equity = float(account.get("equity") or 0.0)
    if equity <= 0:
        raise HTTPException(status_code=422, detail="The account reports no equity to size against")

    outcome = evaluate(
        today=today,
        equity=equity,
        symbols=symbols,
        closes=closes,
        iv_history=history,
        chains=chains,
        atm_iv=atm,
        spy_closes=closes.get("SPY", []),
        spy_ivs=spy_ivs,
        held=held_from_groups(groups, today),
    )
    for symbol, why in chain_errors.items():
        if symbol in symbols:
            outcome.skip(symbol, f"chain unavailable: {why}")
    return {
        "as_of": today.isoformat(),
        "account": body.account,
        "equity": round(equity, 2),
        "open_risk": outcome.open_risk,
        "risk_cap": round(0.05 * equity, 2),
        "market": outcome.market,
        "exits": outcome.exits,
        "entries": outcome.entries,
        "skipped": outcome.skipped,
    }


class NoteBody(BaseModel):
    note: str = Field(min_length=1, max_length=500)


@router.post("/campaigns/{campaign_id}/note")
async def add_note(campaign_id: str, body: NoteBody, request: Request, user: dict = Depends(get_current_user)) -> dict:
    store = _store(request)
    campaign = await _owned(store, campaign_id, user)
    await store.add_event(campaign_id, user["id"], "manual_note", note=body.note)
    return await _with_events(store, campaign)
