"""Keeps every active campaign current: reconciles what the books did into
events, rebuilds the snapshot, asks the playbook for its next step, stores
the proposal -- and, only in the simulated account with the campaign's
switch on, places it.

Three steps per campaign per tick, each safe to repeat:

  reconcile  the account's closed option orders on the campaign's symbol
             since the last cursor, translated by their `strategy` into
             events (sold_put, sold_call, closed, rolled, expired,
             cash_settled, assigned, called_away); a share count that moved
             with no settlement to explain it becomes a shares_changed note.
             Read from the orders rather than hooked into the book, so a
             replay and, later, a broker account reconcile the same way.
  propose    when an event arrived, when asked, or every few minutes: the
             chains around the campaign's DTE window, the calendar, the
             account's options buying power, then playbook.next_step(ctx),
             rendered and stored on the campaign with its error if any.
  execute    with auto_execute on, in the simulated account, in the regular
             session, and not the same proposal twice: submit / roll /
             close through the service. A failure turns the switch off --
             a switch that silently fails every tick is worse than one that
             trips.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import UTC, date, datetime, timedelta

from app.options.models import CloseSpreadRequest, RollRequest, SpreadTicket
from app.options.occ import try_parse_occ
from app.playbooks import loader
from app.playbooks.actions import render
from app.playbooks.context import CalendarView, ChainView, PlaybookContext
from app.playbooks.snapshot import build_snapshot, events_from_rows
from app.playbooks.store import ACTIVE, PlaybookStore
from app.services.market_clock import ET, current_session

logger = logging.getLogger(__name__)

PROPOSE_INTERVAL = timedelta(minutes=5)
# Chains loaded per proposal: the DTE window with slack, plus every open
# leg's expiry, at most this many.
MAX_CHAINS = 6
WINDOW_SLACK_BEFORE = 7
WINDOW_SLACK_AFTER = 14
DONE_STATUSES = {"filled", "expired", "assigned"}


def _num(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _order_legs(order: dict) -> list[dict]:
    legs = order.get("legs")
    if legs:
        return list(legs)
    symbol = order.get("symbol")
    return [{"symbol": symbol, "side": order.get("side"), "position_intent": None}] if symbol else []


def order_events(campaign: dict, order: dict) -> list[dict]:
    """The events one closed order on the campaign's symbol stands for --
    pure, so the mapping is testable with dicts. Empty for an order that is
    not the campaign's business (another underlying, not done)."""
    if str(order.get("underlying") or "").upper() != campaign["symbol"]:
        return []
    if str(order.get("status")) not in DONE_STATUSES:
        return []
    qty = int(round(_num(order.get("qty")) or 0))
    net = _num(order.get("net_fill_price")) or 0.0  # per share, positive = paid
    cash = round(-net * 100 * qty, 2)
    strategy = str(order.get("strategy") or "")
    legs = _order_legs(order)
    at = order.get("filled_at") or order.get("submitted_at")
    base = {"at": at, "order_id": order.get("id"), "qty": qty, "cash_delta": cash}
    first = legs[0]["symbol"] if legs else None
    parsed = try_parse_occ(str(first)) if first else None
    if strategy == "cash_secured_put":
        return [{**base, "kind": "sold_put", "occ": first, "price": abs(net), "note": None}]
    if strategy == "covered_call":
        return [{**base, "kind": "sold_call", "occ": first, "price": abs(net), "note": None}]
    if strategy == "close":
        return [{**base, "kind": "closed", "occ": first, "price": abs(net), "note": None}]
    if strategy == "roll":
        opened = next((l for l in legs if str(l.get("position_intent") or "").endswith("_to_open")), None)
        closed = next((l for l in legs if str(l.get("position_intent") or "").endswith("_to_close")), None)
        return [
            {
                **base,
                "kind": "rolled",
                "occ": opened["symbol"] if opened else first,
                "price": abs(net),
                "note": f"closed {closed['symbol']}" if closed else None,
            }
        ]
    if strategy == "expiry":
        return [{**base, "kind": "expired", "occ": first, "price": 0.0, "cash_delta": 0.0, "note": None}]
    if strategy == "cash_settled":
        return [{**base, "kind": "cash_settled", "occ": first, "price": abs(net), "note": None}]
    if strategy == "assigned" and parsed is not None:
        return [{**base, "kind": "assigned", "occ": first, "qty": 100 * qty, "price": float(parsed.strike), "cash_delta": 0.0, "note": None}]
    if strategy == "called_away" and parsed is not None:
        return [{**base, "kind": "called_away", "occ": first, "qty": -100 * qty, "price": float(parsed.strike), "cash_delta": 0.0, "note": None}]
    # Something else on the symbol (a spread the user opened by hand): noted,
    # not counted as premium -- the playbook did not ask for it.
    return [{**base, "kind": "manual_note", "occ": first, "price": abs(net), "cash_delta": 0.0, "note": f"{strategy or 'order'} filled outside the playbook's shapes"}]


def proposal_key(proposal: dict | None) -> str | None:
    """What makes a proposal 'the same one': its executable part."""
    if not proposal:
        return None
    body = proposal.get("ticket") or proposal.get("roll") or proposal.get("close")
    if body is None:
        return None
    return hashlib.sha1(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]


class PlaybookRunner:
    def __init__(
        self,
        store: PlaybookStore,
        *,
        earnings_calendar=None,
        macro_calendar=None,
        propose_interval: timedelta = PROPOSE_INTERVAL,
    ) -> None:
        self._store = store
        self._earnings = earnings_calendar
        self._macro = macro_calendar
        self._interval = propose_interval

    # --- the tick ------------------------------------------------------------

    async def tick(self, user_id: int, account: str, service, *, now: datetime | None = None, force: bool = False) -> None:
        now = now or datetime.now(UTC)
        campaigns = [c for c in await self._store.list_for_user(user_id, account) if c["status"] == ACTIVE]
        for campaign in campaigns:
            try:
                await self.run_one(campaign, service, now=now, force=force)
            except Exception:
                logger.exception("Playbook tick failed for campaign %s (%s)", campaign["id"], campaign["symbol"])
                await self._store.update(campaign["id"], last_error="tick failed; see the server log", now=now)

    async def run_one(self, campaign: dict, service, *, now: datetime, force: bool = False) -> dict | None:
        changed = await self.reconcile(campaign, service, now=now)
        campaign = await self._store.get(campaign["id"]) or campaign
        due = force or changed or campaign.get("proposal_at") is None
        if not due and campaign.get("proposal_at"):
            try:
                last = datetime.fromisoformat(campaign["proposal_at"])
                due = now - last >= self._interval
            except ValueError:
                due = True
        if not due:
            return campaign
        proposal = await self.propose(campaign, service, now=now)
        campaign = await self._store.get(campaign["id"]) or campaign
        if proposal is not None and campaign.get("auto_execute"):
            await self.maybe_execute(campaign, service, proposal, now=now)
        return await self._store.get(campaign["id"])

    # --- reconcile -----------------------------------------------------------

    async def reconcile(self, campaign: dict, service, *, now: datetime) -> bool:
        """Turn the books' doings since the cursor into events. Returns True
        when anything was recorded."""
        orders = await service.orders("closed")
        cursor = campaign.get("orders_cursor") or ""
        mine = [
            o for o in orders
            if str(o.get("underlying") or "").upper() == campaign["symbol"]
            and str(o.get("filled_at") or o.get("submitted_at") or "") > cursor
            and str(o.get("status")) in DONE_STATUSES
        ]
        mine.sort(key=lambda o: str(o.get("filled_at") or o.get("submitted_at") or ""))
        recorded = False
        newest = cursor
        share_events = False
        for order in mine:
            if await self._store.has_order_event(campaign["id"], str(order.get("id"))):
                continue
            for ev in order_events(campaign, order):
                at = datetime.fromisoformat(ev["at"]) if ev.get("at") else now
                await self._store.add_event(
                    campaign["id"], int(campaign["user_id"]), ev["kind"], at=at, occ=ev.get("occ"), qty=int(ev.get("qty") or 0),
                    price=ev.get("price"), cash_delta=ev.get("cash_delta"), order_id=ev.get("order_id"), note=ev.get("note"),
                )
                recorded = True
                if ev["kind"] in ("assigned", "called_away"):
                    share_events = True
            stamp = str(order.get("filled_at") or order.get("submitted_at") or "")
            if stamp > newest:
                newest = stamp

        # Shares that moved without a settlement to explain it: the user
        # bought or sold them by hand. Recorded, not fought.
        share_position = await service.share_position(campaign["symbol"])
        shares_now = int(round(_num((share_position or {}).get("qty")) or 0)) if share_position else 0
        if shares_now != int(campaign.get("shares") or 0) and not share_events:
            spot = await service.spot(campaign["symbol"])
            delta = shares_now - int(campaign.get("shares") or 0)
            await self._store.add_event(
                campaign["id"], int(campaign["user_id"]), "shares_changed", at=now, qty=delta, price=spot,
                note=f"shares changed by hand: {int(campaign.get('shares') or 0)} → {shares_now} (recorded at the spot)",
            )
            recorded = True

        fields: dict = {}
        if newest != cursor:
            fields["orders_cursor"] = newest
        snapshot = await self.snapshot(campaign, service, now=now)
        fields.update(
            shares=snapshot.shares,
            shares_avg_entry=snapshot.shares_avg_entry,
            cost_basis=snapshot.cost_basis,
            premiums_collected=round(snapshot.premiums_collected, 2),
            realized_pnl=round(snapshot.realized_pnl, 2),
            phase=snapshot.phase,
        )
        await self._store.update(campaign["id"], now=now, **fields)
        return recorded

    async def snapshot(self, campaign: dict, service, *, now: datetime):
        marks = await service.marked_positions()
        share_position = await service.share_position(campaign["symbol"])
        events = await self._store.events(campaign["id"])
        return build_snapshot(
            campaign["symbol"], marks=marks, share_position=share_position, events=events, today=now.astimezone(ET).date()
        )

    # --- propose -------------------------------------------------------------

    async def build_context(self, campaign: dict, service, *, now: datetime) -> PlaybookContext:
        playbook = loader.get_playbook(campaign["playbook"])
        if playbook is None:
            raise LookupError(f"playbook {campaign['playbook']!r} is not available")
        params = playbook.resolve_params(campaign.get("params") or {})
        today = now.astimezone(ET).date()
        snapshot = await self.snapshot(campaign, service, now=now)

        spot = await service.spot(campaign["symbol"])
        infos = (await service.expiries(campaign["symbol"])).get("expiries", [])
        min_dte = int(params.get("min_dte", 21)) - WINDOW_SLACK_BEFORE
        max_dte = int(params.get("max_dte", 45)) + WINDOW_SLACK_AFTER
        wanted: list[date] = []
        for info in infos:
            expiry = info["expiry"] if isinstance(info, dict) else info.expiry
            dte = info["dte"] if isinstance(info, dict) else info.dte
            if isinstance(expiry, str):
                expiry = date.fromisoformat(expiry)
            if dte >= 1 and min_dte <= dte <= max_dte:
                wanted.append(expiry)
        for leg in snapshot.open_legs:
            if leg.expiry not in wanted and leg.dte >= 0:
                wanted.insert(0, leg.expiry)
        wanted = sorted(set(wanted))[:MAX_CHAINS]
        chains = {}
        results = await asyncio.gather(*(service.chain(campaign["symbol"], e) for e in wanted), return_exceptions=True)
        for expiry, result in zip(wanted, results):
            if isinstance(result, Exception):
                logger.warning("Playbook chain %s %s failed: %s", campaign["symbol"], expiry, result)
                continue
            chains[expiry] = result
        chain = ChainView(chains, today, now=now)
        if not chain.spot and spot:
            chain.spot = float(spot)

        next_earnings = None
        macro: tuple[tuple[date, str], ...] = ()
        if self._earnings is not None:
            try:
                earnings = await self._earnings.next_earnings(campaign["symbol"])
                next_earnings = earnings.report_date if earnings else None
            except Exception:
                logger.exception("Playbook earnings lookup failed for %s", campaign["symbol"])
        if self._macro is not None:
            try:
                macro = tuple((m.date, m.label) for m in await self._macro.upcoming())
            except Exception:
                logger.exception("Playbook macro lookup failed")

        account = await service.account()
        cash = _num(account.get("options_buying_power")) or 0.0
        return PlaybookContext(
            symbol=campaign["symbol"],
            account=campaign["account"],
            now=now,
            today=today,
            spot=float(spot or chain.spot or 0.0),
            shares=snapshot.shares,
            shares_avg_entry=snapshot.shares_avg_entry,
            cost_basis=snapshot.cost_basis,
            open_legs=snapshot.open_legs,
            premiums_collected=snapshot.premiums_collected,
            realized_pnl=snapshot.realized_pnl,
            events=events_from_rows(await self._store.events(campaign["id"])),
            chain=chain,
            calendar=CalendarView(next_earnings=next_earnings, macro=macro, iv_rank=None),
            params=params,
            phase=snapshot.phase,
            cash_available=cash,
        )

    async def propose(self, campaign: dict, service, *, now: datetime) -> dict | None:
        try:
            playbook = loader.get_playbook(campaign["playbook"])
            if playbook is None:
                raise LookupError(f"playbook {campaign['playbook']!r} is not available")
            ctx = await self.build_context(campaign, service, now=now)
            action = playbook.next_step(ctx)
            proposal = render(action, campaign["symbol"]) if action is not None else render_hold_none()
            proposal["computed_at"] = now.isoformat()
            proposal["spot"] = ctx.spot
            proposal["phase"] = ctx.phase
            proposal["expiries_loaded"] = [e.isoformat() for e in ctx.chain.expiries]
            proposal["key"] = proposal_key(proposal)
            await self._store.update(campaign["id"], now=now, proposal=proposal, proposal_at=now.isoformat(), proposal_error=None)
            return proposal
        except Exception as exc:
            logger.exception("Playbook proposal failed for %s", campaign["symbol"])
            await self._store.update(campaign["id"], now=now, proposal_at=now.isoformat(), proposal_error=f"{type(exc).__name__}: {exc}")
            return None

    # --- execute -------------------------------------------------------------

    async def maybe_execute(self, campaign: dict, service, proposal: dict, *, now: datetime) -> None:
        if not campaign.get("auto_execute") or campaign["account"] != "sim":
            return
        if proposal.get("kind") == "hold" or not proposal.get("key"):
            return
        if current_session() != "regular":
            return
        if proposal["key"] == campaign.get("executed_order_id"):
            return
        try:
            if proposal.get("ticket"):
                result = await service.submit(SpreadTicket(**proposal["ticket"]))
            elif proposal.get("roll"):
                result = await service.roll(RollRequest(**proposal["roll"]))
                result = result.get("order") or result
            elif proposal.get("close"):
                result = await service.close_spread(CloseSpreadRequest(**proposal["close"]))
            else:
                return
        except Exception as exc:
            logger.exception("Playbook auto-execute failed for %s", campaign["symbol"])
            await self._store.update(campaign["id"], now=now, auto_execute=False, last_error=f"auto-execute failed: {exc}")
            await self._store.add_event(
                campaign["id"], int(campaign["user_id"]), "execute_failed", at=now, note=f"{proposal.get('sentence')}: {exc}; auto-execute switched off"
            )
            return
        order_id = (result or {}).get("id") if isinstance(result, dict) else None
        await self._store.update(campaign["id"], now=now, executed_order_id=proposal["key"], last_error=None)
        await self._store.add_event(
            campaign["id"], int(campaign["user_id"]), "executed", at=now, order_id=order_id, note=f"auto-executed: {proposal.get('sentence')}"
        )


def render_hold_none() -> dict:
    return {"kind": "hold", "sentence": "Hold", "reason": "The playbook returned nothing.", "ticket": None, "roll": None, "close": None}
