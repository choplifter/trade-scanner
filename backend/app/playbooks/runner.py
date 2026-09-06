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
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

from app.options.models import CloseSpreadRequest, RollRequest, SpreadTicket
from app.options.occ import try_parse_occ
from app.playbooks import loader
from app.playbooks.actions import render
from app.playbooks.alpaca_adapter import book_order_from_activity, book_order_from_alpaca, parse_stamp
from app.playbooks.context import CalendarView, ChainView, PlaybookContext
from app.playbooks.snapshot import build_snapshot, events_from_rows
from app.playbooks.store import ACTIVE, PlaybookStore
from app.services.market_clock import ET, current_session

logger = logging.getLogger(__name__)

PROPOSE_INTERVAL = timedelta(minutes=5)
# Chains loaded per proposal: the DTE window with slack, plus every open
# leg's expiry, at most this many.
MAX_CHAINS = 8
WINDOW_SLACK_BEFORE = 7
WINDOW_SLACK_AFTER = 14
DONE_STATUSES = {"filled", "expired", "assigned"}


def _num(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _leg_fill(leg: dict) -> float:
    """A leg's own fill per share: `fill_price` in the book's rows and the
    Alpaca adapter's, `filled_avg_price` (a money string) in the simulated
    service's public order shape."""
    price = _num(leg.get("fill_price"))
    if price is None:
        price = _num(leg.get("filled_avg_price"))
    return abs(price) if price is not None else 0.0


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
    intents = [str(l.get("position_intent") or "") for l in legs]
    # A leg sold to close was held long: the long side's events, kept apart
    # from the premiums (see snapshot.LONG_EVENTS).
    closes_long = any(i == "sell_to_close" for i in intents)
    if strategy == "cash_secured_put":
        return [{**base, "kind": "sold_put", "occ": first, "price": abs(net), "note": None}]
    if strategy == "covered_call":
        return [{**base, "kind": "sold_call", "occ": first, "price": abs(net), "note": None}]
    if strategy == "long_call":
        return [{**base, "kind": "bought_call", "occ": first, "price": abs(net), "note": None}]
    if strategy == "close":
        if closes_long and len(legs) > 1:
            # Both sides closed as one package (a poor man's wheel turning):
            # each leg by its own fill, so the premiums and the long side
            # each get their part.
            out = []
            for leg in legs:
                fill = _leg_fill(leg)
                long_leg = str(leg.get("position_intent") or "") == "sell_to_close"
                out.append(
                    {
                        **base, "kind": "sold_long" if long_leg else "closed", "occ": leg.get("symbol"), "price": fill,
                        "cash_delta": round((fill if long_leg else -fill) * 100 * qty, 2), "note": "both legs closed",
                    }
                )
            return out
        return [{**base, "kind": "sold_long" if closes_long else "closed", "occ": first, "price": abs(net), "note": None}]
    if strategy == "roll":
        opened = next((l for l in legs if str(l.get("position_intent") or "").endswith("_to_open")), None)
        closed = next((l for l in legs if str(l.get("position_intent") or "").endswith("_to_close")), None)
        if opened is not None and str(opened.get("position_intent")) == "buy_to_open":
            # A long leg rolled out: the old one sold, the new one bought,
            # each at its own fill.
            sold = _leg_fill(closed) if closed is not None else 0.0
            bought = _leg_fill(opened)
            out = []
            if closed is not None:
                out.append({**base, "kind": "sold_long", "occ": closed["symbol"], "price": sold, "cash_delta": round(sold * 100 * qty, 2), "note": "rolled"})
            out.append(
                {**base, "kind": "bought_call", "occ": opened["symbol"], "price": bought, "cash_delta": round(-bought * 100 * qty, 2),
                 "note": f"rolled from {closed['symbol']}" if closed else None}
            )
            return out
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
        return [{**base, "kind": "sold_long" if closes_long else "cash_settled", "occ": first, "price": abs(net), "note": None}]
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
        # Always from the store: a caller's copy may predate a settlement's
        # share count, and a stale count would read as shares moved by hand.
        campaign = await self._store.get(campaign["id"]) or campaign
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
        orders = await self._book_orders(campaign, service)
        cursor_at = parse_stamp(campaign.get("orders_cursor")) or datetime.min.replace(tzinfo=UTC)

        def when(o: dict) -> datetime | None:
            return parse_stamp(o.get("filled_at") or o.get("submitted_at"))

        mine = [
            o for o in orders
            if str(o.get("underlying") or "").upper() == campaign["symbol"]
            and str(o.get("status")) in DONE_STATUSES
            and (when(o) or now) > cursor_at
        ]
        mine.sort(key=lambda o: when(o) or now)
        recorded = False
        newest = cursor_at
        share_events = False
        for order in mine:
            if await self._store.has_order_event(campaign["id"], str(order.get("id"))):
                continue
            for ev in order_events(campaign, order):
                at = parse_stamp(ev.get("at")) or now
                await self._store.add_event(
                    campaign["id"], int(campaign["user_id"]), ev["kind"], at=at, occ=ev.get("occ"), qty=int(ev.get("qty") or 0),
                    price=ev.get("price"), cash_delta=ev.get("cash_delta"), order_id=ev.get("order_id"),
                    note=ev.get("note") or order.get("note"),
                )
                recorded = True
                if ev["kind"] in ("assigned", "called_away"):
                    share_events = True
            stamp = when(order)
            if stamp is not None and stamp > newest:
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
        if newest != cursor_at:
            fields["orders_cursor"] = newest.isoformat()
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

    async def _book_orders(self, campaign: dict, service) -> list[dict]:
        """The account's done option orders in the simulated book's row shape.
        The simulated book already speaks it; a broker account's orders go
        through alpaca_adapter, and its assignments, expirations and
        exercises -- which are activities there, not orders -- are appended
        as settlement rows."""
        raw = await service.orders("closed")
        if campaign["account"] == "sim":
            return list(raw)
        out = [b for b in (book_order_from_alpaca(o) for o in raw if isinstance(o, dict)) if b is not None]
        if hasattr(service, "activities"):
            try:
                activities = await service.activities(after=campaign.get("created_at"))
            except Exception:
                logger.exception("Playbook activities fetch failed for %s", campaign["symbol"])
                activities = []
            out.extend(b for b in (book_order_from_activity(a) for a in activities if isinstance(a, dict)) if b is not None)
        return out

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
        infos = list((await service.expiries(campaign["symbol"])).get("expiries", []))
        core = playbook.chain_windows(params)
        windows = [(lo - WINDOW_SLACK_BEFORE, hi + WINDOW_SLACK_AFTER) for lo, hi in core]

        def _parse(info) -> tuple[date, int]:
            expiry = info["expiry"] if isinstance(info, dict) else info.expiry
            dte = info["dte"] if isinstance(info, dict) else info.dte
            if isinstance(expiry, str):
                expiry = date.fromisoformat(expiry)
            return expiry, int(dte)

        listed: list[tuple[date, int]] = [(e, d) for e, d in map(_parse, infos) if d >= 1]
        # A window beyond the strip's end (a LEAPS window): that part of the
        # far strip, where the service has one.
        farthest = max((d for _e, d in listed), default=0)
        for lo, hi in windows:
            if hi <= farthest or not hasattr(service, "far_expiries"):
                continue
            try:
                far = await service.far_expiries(campaign["symbol"], max(lo, farthest + 1), hi)
            except Exception:
                logger.exception("Playbook far expiries failed for %s", campaign["symbol"])
                far = []
            have = {x for x, _ in listed}
            listed.extend((e, d) for e, d in map(_parse, far) if d >= 1 and e not in have)
        # The held legs' expiries first, then each window's nearest expiries
        # with the slots shared between the windows -- a name with weekly
        # expiries would otherwise fill every slot from the near window and
        # leave a script's LEAPS window without a chain.
        held = sorted({leg.expiry for leg in (*snapshot.open_legs, *snapshot.long_legs) if leg.dte >= 0})
        per_window = max(2, (MAX_CHAINS - len(held)) // max(len(windows), 1))
        wanted = list(held)
        for (lo, hi), (core_lo, core_hi) in zip(windows, core):
            # The window proper first, nearest first; the slack either side
            # only with slots to spare -- a name with weekly expiries would
            # otherwise fill the slots with expiries the script cannot use.
            in_window = [
                e for e, dte in sorted(listed, key=lambda x: (not core_lo <= x[1] <= core_hi, x[1]))
                if lo <= dte <= hi and e not in wanted
            ]
            wanted.extend(in_window[:per_window])
        wanted = sorted(wanted[:MAX_CHAINS])
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

        # A held leg's delta from its chain when the positions carry none
        # (the simulated book's do not): a poor man's wheel watches its long
        # call's delta.
        def with_delta(leg):
            if leg.delta is not None or not chain.has(leg.expiry):
                return leg
            return replace(leg, delta=chain.delta(leg.kind, leg.strike, leg.expiry))

        open_legs = tuple(with_delta(l) for l in snapshot.open_legs)
        long_legs = tuple(with_delta(l) for l in snapshot.long_legs)

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
            open_legs=open_legs,
            premiums_collected=snapshot.premiums_collected,
            realized_pnl=snapshot.realized_pnl,
            events=events_from_rows(await self._store.events(campaign["id"])),
            chain=chain,
            calendar=CalendarView(next_earnings=next_earnings, macro=macro, iv_rank=None),
            params=params,
            phase=snapshot.phase,
            cash_available=cash,
            long_legs=long_legs,
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
            # The legs as the proposal saw them, so the tab can show a long
            # call standing in for shares without a new column.
            proposal["open_legs"] = [
                {
                    "occ": l.occ, "side": l.side, "kind": l.kind, "strike": l.strike, "expiry": l.expiry.isoformat(), "dte": l.dte,
                    "qty": l.qty, "entry_credit": l.entry_credit, "mark": l.mark, "profit_pct": l.profit_pct, "delta": l.delta,
                }
                for l in (*ctx.open_legs, *ctx.long_legs)
            ]
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
