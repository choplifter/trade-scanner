"""The playbook family without I/O: the loader's contract, the Wheel's rules
over hand-built chains, the snapshot arithmetic, the store, and the
runner's order-to-event mapping plus a full tick against a fake service."""

import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest

from app.options.chain import Chain, LegQuote, StrikeRow
from app.options.occ import format_occ
from app.playbooks import loader
from app.playbooks.actions import Hold, Roll, SellCall, SellPut, render
from app.playbooks.context import CalendarView, ChainView, OpenLeg, PlaybookContext
from app.playbooks.runner import PlaybookRunner, order_events, proposal_key
from app.playbooks.snapshot import build_snapshot
from app.playbooks.store import PlaybookStore

TODAY = date(2026, 9, 8)
NOW = datetime(2026, 9, 8, 15, 0, tzinfo=timezone.utc)
E_SHORT = date(2026, 9, 18)  # 10 dte
E_MID = date(2026, 10, 9)  # 31 dte
E_LATE = date(2026, 10, 30)  # 52 dte
E_FAR = date(2026, 11, 20)  # 73 dte
SPOT = 100.0


def _quote(symbol, kind, strike, expiry, mid, delta):
    return LegQuote(
        symbol=symbol, strike=strike, kind=kind, expiry=expiry, bid=round(mid - 0.05, 2), ask=round(mid + 0.05, 2), mid=mid, last=mid,
        bid_size=5, ask_size=5, delta=delta, gamma=None, theta=None, iv=0.3, open_interest=100, tradable=True,
    )


def _chain(expiry, strikes=(85, 90, 95, 100, 105, 110, 115), spot=SPOT) -> Chain:
    rows = []
    for s in strikes:
        # Deltas fall off with distance; mids scale with time and distance.
        dist = (s - spot) / spot
        put_delta = -max(0.03, min(0.95, 0.5 + dist * 4))
        call_delta = max(0.03, min(0.95, 0.5 - dist * 4))
        dte = (expiry - TODAY).days
        put_mid = round(max(0.05, (2.0 - dist * 40) * (dte / 30) ** 0.5), 2)
        call_mid = round(max(0.05, (2.0 + dist * 40) * (dte / 30) ** 0.5), 2)
        rows.append(
            StrikeRow(
                strike=float(s),
                call=_quote(format_occ("XYZ", expiry, "call", float(s)), "call", float(s), expiry, call_mid, call_delta),
                put=_quote(format_occ("XYZ", expiry, "put", float(s)), "put", float(s), expiry, put_mid, put_delta),
            )
        )
    return Chain(underlying="XYZ", expiry=expiry, spot=spot, feed="sim", as_of=NOW, rows=rows)


def _view(expiries=(E_SHORT, E_MID, E_LATE, E_FAR), spot=SPOT) -> ChainView:
    return ChainView({e: _chain(e, spot=spot) for e in expiries}, TODAY, now=NOW)


def _wheel():
    pb = loader.get_playbook("wheel")
    assert pb is not None
    return pb


def _ctx(*, shares=0, avg_entry=None, legs=(), premiums=0.0, cash=50_000.0, params=None, calendar=None, view=None, spot=SPOT) -> PlaybookContext:
    pb = _wheel()
    basis = (avg_entry * shares - premiums) / shares if shares and avg_entry is not None else None
    has_put = any(l.kind == "put" for l in legs)
    has_call = any(l.kind == "call" for l in legs)
    phase = "mixed" if has_put and has_call else "short_put" if has_put else "covered_call" if has_call else "assigned" if shares >= 100 else "cash"
    return PlaybookContext(
        symbol="XYZ", account="sim", now=NOW, today=TODAY, spot=spot, shares=shares, shares_avg_entry=avg_entry, cost_basis=basis,
        open_legs=tuple(legs), premiums_collected=premiums, realized_pnl=premiums, events=(), chain=view or _view(spot=spot),
        calendar=calendar or CalendarView(), params=pb.resolve_params(params or {}), phase=phase, cash_available=cash,
    )


def _leg(kind, strike, expiry, entry, mark) -> OpenLeg:
    return OpenLeg(
        occ=format_occ("XYZ", expiry, kind, strike), kind=kind, strike=strike, expiry=expiry, dte=(expiry - TODAY).days, qty=1,
        entry_credit=entry, mark=mark, profit_pct=(entry - mark) / entry * 100,
    )


# --- loader ---------------------------------------------------------------------


def test_the_loader_finds_the_wheel_and_resolves_its_params():
    playbooks, errors = loader.load_playbooks()
    assert errors == []
    wheel = next(p for p in playbooks if p.stem == "wheel")
    assert wheel.name == "Wheel" and any(p.name == "put_delta" for p in wheel.params)
    params = wheel.resolve_params({"put_delta": "0.25", "qty": 2, "avoid_earnings": "false"})
    assert params["put_delta"] == 0.25 and params["qty"] == 2 and params["avoid_earnings"] is False
    assert params["min_dte"] == 21  # default filled in
    with pytest.raises(ValueError):
        wheel.resolve_params({"put_delt": 0.3})
    with pytest.raises(ValueError):
        wheel.resolve_params({"put_delta": 0.9})


def test_a_broken_playbook_file_is_reported_not_swallowed(tmp_path, monkeypatch):
    bad = loader._DIR / "_zz_broken_test_playbook.py"
    bad.write_text("NAME = 'Broken'\n", encoding="utf-8")
    try:
        _playbooks, errors = loader.load_playbooks()
        assert any(e.filename == bad.name and "next_step" in e.error for e in errors)
    finally:
        bad.unlink()


# --- the chain view ----------------------------------------------------------------


def test_the_chain_view_answers_the_questions_a_wheel_asks():
    view = _view()
    assert view.expiry_in(21, 45) == [E_MID]
    assert view.expiry_in(21, 60, avoid=E_LATE) == [E_MID]
    assert view.expiry_in(21, 60) == [E_MID, E_LATE]
    # Put delta -0.30 sits at 95 in this synthetic chain (0.5 + (-0.05)*4 = 0.30).
    assert view.strike_at_delta("put", 0.30, E_MID) == 95.0
    assert view.strike_at_delta("call", 0.30, E_MID) == 105.0
    assert view.first_strike_at_or_above(E_MID, 101.0, "call") == 105.0
    assert view.last_strike_at_or_below(E_MID, 94.9, "put") == 90.0
    assert view.mid("put", 95.0, E_MID) is not None


# --- the wheel -----------------------------------------------------------------------


def test_with_no_shares_the_wheel_sells_the_delta_put_in_the_window():
    action = _wheel().next_step(_ctx())
    assert isinstance(action, SellPut)
    assert action.expiry == E_MID and action.strike == 95.0 and action.qty == 1
    proposal = render(action, "XYZ")
    assert proposal["kind"] == "sell_put" and proposal["ticket"]["strategy"] == "cash_secured_put"
    assert proposal["ticket"]["legs"] == [{"kind": "put", "strike": 95.0, "side": "sell"}]


def test_the_put_steps_down_to_fit_the_budget_and_holds_when_nothing_fits():
    stepped = _wheel().next_step(_ctx(params={"budget": 9_200}))
    assert isinstance(stepped, SellPut) and stepped.strike == 90.0
    held = _wheel().next_step(_ctx(cash=8_000.0))
    assert isinstance(held, Hold) and "collateral" in held.reason


def test_earnings_inside_the_window_are_avoided():
    calendar = CalendarView(next_earnings=E_MID)  # the report falls on the only in-window expiry
    action = _wheel().next_step(_ctx(calendar=calendar))
    assert isinstance(action, Hold) and "earnings" in action.reason
    # Every expiry after the report is held through it, so widening the
    # window later does not help; allowing a nearer expiry does.
    action = _wheel().next_step(_ctx(calendar=calendar, params={"max_dte": 60}))
    assert isinstance(action, Hold)
    action = _wheel().next_step(_ctx(calendar=calendar, params={"min_dte": 7}))
    assert isinstance(action, SellPut) and action.expiry == E_SHORT
    # Or ignore earnings.
    action = _wheel().next_step(_ctx(calendar=calendar, params={"avoid_earnings": False}))
    assert isinstance(action, SellPut) and action.expiry == E_MID


def test_with_shares_the_wheel_sells_a_call_at_or_above_the_cost_basis():
    # Assigned at 105 with 3.00 of premium collected: basis 102 -> the 0.30
    # delta call (105) is above it and is taken.
    action = _wheel().next_step(_ctx(shares=100, avg_entry=105.0, premiums=300.0))
    assert isinstance(action, SellCall) and action.strike == 105.0 and action.qty == 1
    # Assigned at 112, little premium: the 0.30 delta call (105) is below the
    # basis, so the first strike at or above it (115) is taken instead.
    action = _wheel().next_step(_ctx(shares=100, avg_entry=112.0, premiums=100.0))
    assert isinstance(action, SellCall) and action.strike == 115.0
    # No strike above a very high basis: hold and say so.
    action = _wheel().next_step(_ctx(shares=100, avg_entry=130.0, premiums=0.0))
    assert isinstance(action, Hold) and "cost basis" in action.reason
    # The floor can be switched off.
    action = _wheel().next_step(_ctx(shares=100, avg_entry=130.0, params={"min_call_above_basis": False}))
    assert isinstance(action, SellCall) and action.strike == 105.0


def test_an_open_leg_is_rolled_at_the_profit_target_or_near_expiry_else_held():
    # 60 % of the credit earned: roll to the next in-window expiry, same strike (still OTM).
    leg = _leg("put", 95.0, E_SHORT, entry=2.0, mark=0.8)
    action = _wheel().next_step(_ctx(legs=[leg]))
    assert isinstance(action, Roll) and action.new_expiry == E_MID and action.new_strike == 95.0 and action.close_occ == leg.occ
    rendered = render(action, "XYZ")
    assert rendered["roll"]["close"]["legs"] == [{"symbol": leg.occ, "qty": -1}] and rendered["roll"]["open"]["expiry"] == E_MID.isoformat()
    # In the money and near expiry: by default the wheel lets it be assigned
    # (that is the turn); with accept_assignment off it rolls and re-picks
    # the delta strike on the new expiry.
    itm = _leg("put", 105.0, E_SHORT, entry=6.0, mark=6.5)
    action = _wheel().next_step(_ctx(legs=[itm], params={"roll_at_dte": 10}))
    assert isinstance(action, Hold) and "assigned into shares" in action.reason
    action = _wheel().next_step(_ctx(legs=[itm], params={"roll_at_dte": 10, "accept_assignment": False}))
    assert isinstance(action, Roll) and action.new_strike == 95.0 and "10 days" in action.reason
    # Comfortable leg: hold.
    calm = _leg("put", 95.0, E_LATE, entry=3.0, mark=2.5)
    action = _wheel().next_step(_ctx(legs=[calm]))
    assert isinstance(action, Hold) and calm.occ in action.reason


# --- snapshot -------------------------------------------------------------------------


def test_the_snapshot_sums_premiums_and_derives_basis_phase_and_realized_pnl():
    put = format_occ("XYZ", E_SHORT, "put", 100.0)
    call = format_occ("XYZ", E_MID, "call", 105.0)
    events = [
        {"at": "2026-08-01T14:00:00+00:00", "kind": "sold_put", "occ": put, "qty": 1, "price": 2.0, "cash_delta": 200.0, "note": None},
        {"at": "2026-08-15T20:00:00+00:00", "kind": "assigned", "occ": put, "qty": 100, "price": 100.0, "cash_delta": 0.0, "note": None},
        {"at": "2026-08-16T14:00:00+00:00", "kind": "sold_call", "occ": call, "qty": 1, "price": 1.5, "cash_delta": 150.0, "note": None},
    ]
    marks = [{"symbol": call, "qty": "-1", "avg_entry_price": "1.5", "current_price": "0.6"}]
    snap = build_snapshot("XYZ", marks=marks, share_position={"qty": 100, "avg_entry_price": 100.0}, events=events, today=TODAY)
    assert snap.shares == 100 and snap.premiums_collected == 350.0
    assert snap.cost_basis == pytest.approx(100.0 - 3.5)
    assert snap.phase == "covered_call"
    assert len(snap.open_legs) == 1 and snap.open_legs[0].profit_pct == pytest.approx(60.0)

    # Called away at 105: the shares' round trip counts toward realized P&L.
    events.append({"at": "2026-09-05T20:00:00+00:00", "kind": "called_away", "occ": call, "qty": -100, "price": 105.0, "cash_delta": 0.0, "note": None})
    done = build_snapshot("XYZ", marks=[], share_position=None, events=events, today=TODAY)
    assert done.phase == "cash" and done.realized_pnl == pytest.approx(350.0 + 500.0) and done.cost_basis is None


# --- the runner's order mapping ------------------------------------------------------


def test_orders_become_events_by_their_strategy():
    campaign = {"symbol": "XYZ"}
    put = format_occ("XYZ", E_MID, "put", 95.0)
    sold = {"id": "o1", "underlying": "XYZ", "status": "filled", "strategy": "cash_secured_put", "qty": "2", "net_fill_price": -1.5, "symbol": put, "filled_at": "t1"}
    (ev,) = order_events(campaign, sold)
    assert ev["kind"] == "sold_put" and ev["cash_delta"] == 300.0 and ev["occ"] == put
    assigned = {**sold, "id": "o2", "status": "assigned", "strategy": "assigned", "qty": "2", "net_fill_price": 0.0}
    (ev,) = order_events(campaign, assigned)
    assert ev["kind"] == "assigned" and ev["qty"] == 200 and ev["price"] == 95.0
    roll = {
        "id": "o3", "underlying": "XYZ", "status": "filled", "strategy": "roll", "qty": "1", "net_fill_price": -1.2,
        "legs": [{"symbol": put, "position_intent": "buy_to_close"}, {"symbol": format_occ("XYZ", E_LATE, "put", 95.0), "position_intent": "sell_to_open"}],
    }
    (ev,) = order_events(campaign, roll)
    assert ev["kind"] == "rolled" and ev["occ"].endswith("P00095000") and E_LATE.strftime("%y%m%d") in ev["occ"] and ev["cash_delta"] == 120.0
    assert order_events(campaign, {**sold, "underlying": "ABC"}) == []
    assert order_events(campaign, {**sold, "status": "new"}) == []
    (other,) = order_events(campaign, {**sold, "strategy": "bull_put"})
    assert other["kind"] == "manual_note" and other["cash_delta"] == 0.0
    assert proposal_key({"ticket": {"a": 1}}) == proposal_key({"ticket": {"a": 1}}) and proposal_key({"kind": "hold"}) is None


# --- a full tick against a fake service ------------------------------------------------


class _Service:
    def __init__(self):
        self.orders_closed: list[dict] = []
        self.marks: list[dict] = []
        self.shares: dict | None = None
        self.submitted: list = []
        self.fail_submit = False

    async def orders(self, status="open"):
        return list(self.orders_closed) if status == "closed" else []

    async def marked_positions(self):
        return list(self.marks)

    async def share_position(self, symbol):
        return self.shares

    async def spot(self, symbol):
        return SPOT

    async def expiries(self, symbol):
        return {"expiries": [{"expiry": e.isoformat(), "dte": (e - TODAY).days, "contract_count": 14} for e in (E_SHORT, E_MID, E_LATE, E_FAR)]}

    async def chain(self, symbol, expiry):
        return _chain(expiry)

    async def account(self):
        return {"options_buying_power": 50_000.0}

    async def submit(self, ticket):
        if self.fail_submit:
            raise RuntimeError("book closed")
        self.submitted.append(ticket)
        return {"id": "sim-1", "status": "filled"}


@pytest.fixture
def store(tmp_path):
    s = PlaybookStore(str(tmp_path / "pb.sqlite3"))
    asyncio.run(s.init_schema())
    return s


def test_a_tick_reconciles_orders_proposes_and_only_executes_with_the_switch_on(store):
    runner = PlaybookRunner(store, propose_interval=timedelta(minutes=5))
    campaign = asyncio.run(store.create(1, "sim", "xyz", "wheel", _wheel().resolve_params({}), now=NOW))
    assert campaign["symbol"] == "XYZ" and campaign["status"] == "active"
    service = _Service()

    result = asyncio.run(runner.run_one(campaign, service, now=NOW, force=True))
    assert result["proposal"]["kind"] == "sell_put" and result["proposal_error"] is None
    assert result["phase"] == "cash" and service.submitted == []  # switch off: proposed, not placed

    # The user sells the put by hand; the order shows up in the book.
    put = format_occ("XYZ", E_MID, "put", 95.0)
    service.orders_closed.append(
        {"id": "o1", "underlying": "XYZ", "status": "filled", "strategy": "cash_secured_put", "qty": "1", "net_fill_price": -1.9, "symbol": put, "filled_at": (NOW + timedelta(minutes=1)).isoformat()}
    )
    service.marks.append({"symbol": put, "qty": "-1", "avg_entry_price": "1.9", "current_price": "1.7"})
    result = asyncio.run(runner.run_one(campaign, service, now=NOW + timedelta(minutes=2)))
    events = asyncio.run(store.events(campaign["id"]))
    assert [e["kind"] for e in events] == ["sold_put"] and events[0]["cash_delta"] == 190.0
    assert result["premiums_collected"] == 190.0 and result["phase"] == "short_put"
    assert result["proposal"]["kind"] == "hold" and result["orders_cursor"] == (NOW + timedelta(minutes=1)).isoformat()
    # Reconciling again records nothing twice.
    asyncio.run(runner.run_one(campaign, service, now=NOW + timedelta(minutes=3), force=True))
    assert len(asyncio.run(store.events(campaign["id"]))) == 1

    # Assignment: the settlement order, then shares in the stock book.
    service.orders_closed.append(
        {"id": "o2", "underlying": "XYZ", "status": "assigned", "strategy": "assigned", "qty": "1", "net_fill_price": 0.0, "symbol": put, "filled_at": (NOW + timedelta(days=31)).isoformat()}
    )
    service.marks.clear()
    service.shares = {"qty": 100.0, "avg_entry_price": 95.0}
    later = NOW + timedelta(days=31, minutes=1)
    result = asyncio.run(runner.run_one(campaign, service, now=later))
    kinds = [e["kind"] for e in asyncio.run(store.events(campaign["id"]))]
    assert kinds == ["sold_put", "assigned"]  # no shares_changed note: the assignment explains the shares
    assert result["shares"] == 100 and result["cost_basis"] == pytest.approx(95.0 - 1.9) and result["phase"] == "assigned"
    assert result["proposal"]["kind"] == "sell_call"

    # Switch auto-execute on: the proposal is placed once, and a failure trips the switch.
    asyncio.run(store.update(campaign["id"], auto_execute=True))
    import app.playbooks.runner as runner_module

    original = runner_module.current_session
    runner_module.current_session = lambda: "regular"
    try:
        result = asyncio.run(runner.run_one(campaign, service, now=later + timedelta(minutes=1), force=True))
        assert len(service.submitted) == 1 and service.submitted[0].strategy == "covered_call"
        assert result["executed_order_id"] == result["proposal"]["key"]
        asyncio.run(runner.run_one(campaign, service, now=later + timedelta(minutes=2), force=True))
        assert len(service.submitted) == 1  # the same proposal is not placed twice
        asyncio.run(store.update(campaign["id"], executed_order_id=None))
        service.fail_submit = True
        result = asyncio.run(runner.run_one(campaign, service, now=later + timedelta(minutes=3), force=True))
        assert result["auto_execute"] is False and "auto-execute failed" in result["last_error"]
        assert asyncio.run(store.events(campaign["id"]))[-1]["kind"] == "execute_failed"
    finally:
        runner_module.current_session = original


def test_shares_bought_by_hand_are_noted_not_fought(store):
    runner = PlaybookRunner(store)
    campaign = asyncio.run(store.create(1, "sim", "XYZ", "wheel", _wheel().resolve_params({}), now=NOW))
    service = _Service()
    service.shares = {"qty": 200.0, "avg_entry_price": 98.0}

    result = asyncio.run(runner.run_one(campaign, service, now=NOW, force=True))

    events = asyncio.run(store.events(campaign["id"]))
    assert [e["kind"] for e in events] == ["shares_changed"] and events[0]["qty"] == 200
    assert result["phase"] == "assigned" and result["proposal"]["kind"] == "sell_call"


def test_one_live_campaign_per_symbol_and_account(store):
    asyncio.run(store.create(1, "sim", "XYZ", "wheel", {}, now=NOW))
    with pytest.raises(ValueError):
        asyncio.run(store.create(1, "sim", "xyz", "wheel", {}, now=NOW))
    # Another account, or a closed one, leaves room.
    asyncio.run(store.create(1, "paper", "XYZ", "wheel", {}, now=NOW))
    first = asyncio.run(store.list_for_user(1, "sim"))[0]
    asyncio.run(store.set_status(first["id"], "closed", now=NOW))
    again = asyncio.run(store.create(1, "sim", "XYZ", "wheel", {}, now=NOW))
    assert again["id"] != first["id"]
    assert [c["id"] for c in asyncio.run(store.list_for_user(1, "sim"))] == [again["id"]]
    assert len(asyncio.run(store.list_for_user(1, "sim", include_closed=True))) == 2
    assert {c["account"] for c in asyncio.run(store.all_active("sim"))} == {"sim"}
