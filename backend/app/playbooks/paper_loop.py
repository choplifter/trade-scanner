"""Keeps the paper account's campaigns current, the way the sim loop keeps
the simulated book's: every interval, each user with an active paper
campaign gets an OptionsService on their own broker (app.broker.resolver,
the operator's keys for the admin) and the runner reconciles their orders
and activities, refreshes the proposal, and never executes -- auto-execute
is the simulated account's alone (runner.maybe_execute checks the account).

Runs in and out of session: an assignment lands over the weekend, and the
campaign should say so on Saturday morning rather than at Monday's open.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from app.options.monitor import _service_for

logger = logging.getLogger(__name__)

PAPER_ACCOUNT = "paper"
INTERVAL_SECONDS = 60.0


async def tick_paper_campaigns(clients, settings, store, runner, chain_cache, engine, *, resolver=None, user_store=None) -> int:
    """One pass over every active paper campaign; returns how many users were
    ticked. Separate from the loop so a test can call it."""
    campaigns = await store.all_active(PAPER_ACCOUNT)
    if not campaigns:
        return 0
    users: dict[int, dict] = {}
    if user_store is not None:
        users = {int(u["id"]): u for u in await user_store.list_users()}
    ticked = 0
    now = datetime.now(UTC)
    for user_id in sorted({int(c["user_id"]) for c in campaigns}):
        try:
            service = await _service_for(clients, settings, engine, chain_cache, resolver, users.get(user_id), PAPER_ACCOUNT)
        except Exception:
            logger.exception("Paper playbook: no service for user %s", user_id)
            continue
        if service is None:
            for campaign in campaigns:
                if int(campaign["user_id"]) == user_id:
                    await store.update(campaign["id"], now=now, last_error="no paper broker account connected")
            continue
        await runner.tick(user_id, PAPER_ACCOUNT, service, now=now)
        ticked += 1
    return ticked


async def run_playbook_paper_loop(clients, settings, store, runner, chain_cache, engine, *, resolver=None, user_store=None, interval: float = INTERVAL_SECONDS) -> None:
    while True:
        try:
            if settings.has_credentials:
                await tick_paper_campaigns(clients, settings, store, runner, chain_cache, engine, resolver=resolver, user_store=user_store)
        except Exception:
            logger.exception("Paper playbook loop tick failed")
        await asyncio.sleep(interval)
