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
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.auth.dependency import get_current_user
from app.playbooks import loader
from app.playbooks.store import ACTIVE, CLOSED, PAUSED, PlaybookStore
from app.routers.trading_sim_options import _service as _sim_service
from app.trading.errors import TradingError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/trading/options/playbooks", tags=["playbooks"])

Account = Literal["sim"]
EVENTS_SHOWN = 50


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
    if account != "sim":
        raise HTTPException(
            status_code=422,
            detail={"code": "sim_only", "message": "Playbooks run in the Simulation account for now.", "field": "account"},
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
        service = await _sim_service(request, user)
        await _runner(request).run_one(campaign, service, now=now, force=True)
    except HTTPException:
        raise
    except TradingError as exc:
        await store.update(campaign["id"], proposal_error=exc.to_detail().get("message"), now=now)
    except Exception:
        logger.exception("First proposal failed for campaign %s", campaign["id"])
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
        service = await _sim_service(request, user)
        await _runner(request).run_one(campaign, service, now=datetime.now(UTC), force=True)
    except HTTPException:
        raise
    except TradingError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from exc
    except Exception:
        logger.exception("Proposal failed for campaign %s", campaign_id)
        raise HTTPException(status_code=502, detail="Failed to compute the proposal")
    return await _with_events(store, await store.get(campaign_id) or campaign)


class NoteBody(BaseModel):
    note: str = Field(min_length=1, max_length=500)


@router.post("/campaigns/{campaign_id}/note")
async def add_note(campaign_id: str, body: NoteBody, request: Request, user: dict = Depends(get_current_user)) -> dict:
    store = _store(request)
    campaign = await _owned(store, campaign_id, user)
    await store.add_event(campaign_id, user["id"], "manual_note", note=body.note)
    return await _with_events(store, campaign)
