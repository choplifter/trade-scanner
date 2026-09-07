"""The Poor Man's Wheel without I/O: its rules over hand-built chains with a
LEAPS board, the snapshot's long side, the runner's mapping of long-leg
orders, and the request shapes its actions render to."""

from datetime import date, datetime, timezone

import pytest

from app.options.chain import Chain, LegQuote, StrikeRow
from app.options.models import CloseSpreadRequest, RollRequest, SpreadTicket
from app.options.occ import format_occ
from app.playbooks import loader
from app.playbooks.actions import BuyCall, Close, Hold, Roll, SellCall, render
from app.playbooks.context import CalendarView, ChainView, OpenLeg, PlaybookContext
from app.playbooks.runner import order_events
from app.playbooks.snapshot import build_snapshot

TODAY = date(2026, 9, 8)
NOW = datetime(2026, 9, 8, 15, 0, tzinfo=timezone.utc)
E_SHORT = date(2026, 9, 18)  # 10 dte
E_MID = date(2026, 10, 9)  # 31 dte
E_LATE = date(2026, 10, 30)  # 52 dte
E_LEAPS = date(2027, 6, 18)  # 283 dte
E_LEAPS2 = date(2027, 12, 17)  # 465 dte
SPOT = 100.0
STRIKES = (70, 75, 80, 85, 90, 92.5, 95, 100, 105, 110, 115)


def _quote(symbol, kind, strike, expiry, mid, delta):
    return LegQuote(
        symbol=symbol, strike=strike, kind=kind, expiry=expiry, bid=round(mid - 0.05, 2), ask=round(mid + 0.05, 2), mid=mid, last=mid,
        bid_size=5, ask_size=5, delta=delta, gamma=None, theta=None, iv=0.3, open_interest=100, tradable=True,
    )


def _chain(expiry, strikes=STRIKES, spot=SPOT) -> Chain:
    rows = []
    for s in strikes:
        # Deltas fall off with distance; mids are intrinsic plus a time value
        # that shrinks with distance and grows with the square root of time.
        dist = (s - spot) / spot
        put_delta = -max(0.03, min(0.95, 0.5 + dist * 4))
        call_delta = max(0.03, min(0.95, 0.5 - dist * 4))
        dte = (expiry - TODAY).days
        time_value = max(0.05, (2.0 - abs(dist) * 10) * (dte / 30) ** 0.5)
        call_mid = round(max(spot - s, 0.0) + time_value, 2)
        put_mid = round(max(s - spot, 0.0) + time_value, 2)
        rows.append(
            StrikeRow(
                strike=float(s),
                call=_quote(format_occ("XYZ", expiry, "call", float(s)), "call", float(s), expiry, call_mid, call_delta),
                put=_quote(format_occ("XYZ", expiry, "put", float(s)), "put", float(s), expiry, put_mid, put_delta),
            )
        )
    return Chain(underlying="XYZ", expiry=expiry, spot=spot, feed="sim", as_of=NOW, rows=rows)


def _view(expiries=(E_SHORT, E_MID, E_LATE, E_LEAPS, E_LEAPS2), spot=SPOT) -> ChainView:
    return ChainView({e: _chain(e, spot=spot) for e in expiries}, TODAY, now=NOW)


def _pmw():
    pb = loader.get_playbook("poor_mans_wheel")
    assert pb is not None
    return pb


def _leg(kind, strike, expiry, entry, mark, side="short", delta=None, qty=1) -> OpenLeg:
    profit = (entry - mark) / entry * 100 if side == "short" else (mark - entry) / entry * 100
    return OpenLeg(
        occ=format_occ("XYZ", expiry, kind, strike), kind=kind, strike=strike, expiry=expiry, dte=(expiry - TODAY).days, qty=qty,
        entry_credit=entry, mark=mark, profit_pct=profit, delta=delta, side=side,
    )


def _leaps(entry=11.0, mark=11.5, delta=0.8, expiry=E_LEAPS, strike=92.5) -> OpenLeg:
    return _leg("call", strike, expiry, entry, mark, side="long", delta=delta)


def _ctx(*, shares=0, avg_entry=None, legs=(), longs=(), premiums=0.0, cash=50_000.0, params=None, calendar=None, view=None, spot=SPOT) -> PlaybookContext:
    pb = _pmw()
    basis = None
    if shares and avg_entry is not None:
        basis = (avg_entry * shares - premiums) / shares
    elif longs:
        contracts = sum(l.qty for l in longs)
        basis = min(l.strike for l in longs) + (sum(l.entry_credit * 100 * l.qty for l in longs) - premiums) / (100 * contracts)
    has_call = any(l.kind == "call" for l in legs)
    phase = "diagonal" if longs and has_call else "long_call" if longs else "covered_call" if has_call else "cash"
    return PlaybookContext(
        symbol="XYZ", account="sim", now=NOW, today=TODAY, spot=spot, shares=shares, shares_avg_entry=avg_entry, cost_basis=basis,
        open_legs=tuple(legs), premiums_collected=premiums, realized_pnl=premiums, events=(), chain=view or _view(spot=spot),
        calendar=calendar or CalendarView(), params=pb.resolve_params(params or {}), phase=phase, cash_available=cash,
        long_legs=tuple(longs),
    )


# --- loader ----------------------------------------------------------------------------


def test_the_loader_offers_the_poor_mans_wheel_with_two_chain_windows():
    playbooks, errors = loader.load_playbooks()
    assert errors == []
    pmw = next(p for p in playbooks if p.stem == "poor_mans_wheel")
    assert pmw.name == "Poor Man's Wheel" and any(p.name == "leaps_delta" for p in pmw.params)
    params = pmw.resolve_params({"leaps_min_dte": 300})
    assert pmw.chain_windows(params) == [(21, 45), (300, 540)]
    # The wheel has no window hook and falls back to its DTE window.
    wheel = next(p for p in playbooks if p.stem == "wheel")
    assert wheel.chain_windows(wheel.resolve_params({"max_dte": 60})) == [(21, 60)]


# --- the rules -------------------------------------------------------------------------


def test_with_no_cover_it_buys_the_delta_call_in_the_leaps_window():
    action = _pmw().next_step(_ctx())
    assert isinstance(action, BuyCall)
    assert action.expiry == E_LEAPS and action.strike == 92.5 and action.qty == 1 and action.est_delta == pytest.approx(0.8)
    proposal = render(action, "XYZ")
    assert proposal["kind"] == "buy_call" and proposal["ticket"]["strategy"] == "long_call"
    ticket = SpreadTicket(**proposal["ticket"])
    assert ticket.long_strike == 92.5 and ticket.expiry == E_LEAPS and ticket.leg_specs() == [("call", 92.5, "buy")]
    assert "Buy 1×" in proposal["sentence"] and "92.5C" in proposal["sentence"]


def test_the_call_is_not_bought_over_the_budget_or_the_cash():
    held = _pmw().next_step(_ctx(params={"budget": 500}))
    assert isinstance(held, Hold) and "over the budget" in held.reason
    held = _pmw().next_step(_ctx(cash=400.0))
    assert isinstance(held, Hold) and "cash available" in held.reason


def test_with_the_leaps_held_it_sells_the_delta_call_at_or_above_the_basis_before_the_leaps_expiry():
    # 92.5 strike bought at 11.00: basis 103.50. The 0.25 delta call (105) is above it.
    action = _pmw().next_step(_ctx(longs=[_leaps(entry=11.0)]))
    assert isinstance(action, SellCall) and action.expiry == E_MID and action.strike == 105.0 and action.qty == 1
    assert render(action, "XYZ")["ticket"]["strategy"] == "covered_call"
    # Bought dearer (basis 106.50): lifted to the first strike at or above it.
    action = _pmw().next_step(_ctx(longs=[_leaps(entry=14.0)]))
    assert isinstance(action, SellCall) and action.strike == 110.0 and "lifted" in action.reason
    # Floor off: the delta strike, still above the long strike.
    action = _pmw().next_step(_ctx(longs=[_leaps(entry=14.0)], params={"min_call_above_basis": False}))
    assert isinstance(action, SellCall) and action.strike == 105.0
    # Premiums lower the basis: 14.00 paid, 4.00 collected -> 102.50 -> 105 fits again.
    action = _pmw().next_step(_ctx(longs=[_leaps(entry=14.0)], premiums=400.0))
    assert isinstance(action, SellCall) and action.strike == 105.0
    # Only expiries before the long call's are considered: with the long
    # call on E_LATE, E_LATE itself is out even when the window holds it.
    action = _pmw().next_step(_ctx(longs=[_leaps(expiry=E_LATE)], params={"max_dte": 60, "leaps_roll_at_dte": 30}))
    assert isinstance(action, SellCall) and action.expiry == E_MID
    action = _pmw().next_step(_ctx(longs=[_leaps(expiry=E_LATE)], params={"min_dte": 50, "max_dte": 60, "leaps_roll_at_dte": 30}))
    assert isinstance(action, Hold) and "before the long call" in action.reason


def test_the_short_call_is_rolled_at_the_profit_target_to_the_delta_strike_again():
    # 110 has earned 60 % of its credit; the 0.25 delta call on the next
    # expiry is 105, above the basis 103.50 -- the strike follows the stock.
    short = _leg("call", 110.0, E_SHORT, entry=2.0, mark=0.8)
    action = _pmw().next_step(_ctx(longs=[_leaps()], legs=[short]))
    assert isinstance(action, Roll) and action.close_occ == short.occ and action.new_expiry == E_MID and action.new_strike == 105.0
    assert action.new_side == "sell" and action.close_side == "short"
    rendered = render(action, "XYZ")
    assert rendered["roll"]["close"]["legs"] == [{"symbol": short.occ, "qty": -1}] and rendered["roll"]["open"]["strategy"] == "covered_call"
    RollRequest(**rendered["roll"])


def test_in_the_money_near_expiry_the_wheel_turns_or_rolls_up_and_out():
    leaps = _leaps()
    itm = _leg("call", 95.0, E_SHORT, entry=3.0, mark=5.5)
    action = _pmw().next_step(_ctx(longs=[leaps], legs=[itm], params={"roll_at_dte": 10}))
    assert isinstance(action, Close) and action.legs == ((itm.occ, -1), (leaps.occ, 1)) and action.qty == 1
    assert "wheel turns" in action.reason
    rendered = render(action, "XYZ")
    request = CloseSpreadRequest(**rendered["close"])
    assert [l.qty for l in request.legs] == [-1, 1] and rendered["kind"] == "close"
    assert rendered["sentence"] == f"Close 1× {itm.occ} + {leaps.occ}"
    # Turning off: rolled up and out to the delta strike on the next expiry.
    action = _pmw().next_step(_ctx(longs=[leaps], legs=[itm], params={"roll_at_dte": 10, "turn_on_itm": False}))
    assert isinstance(action, Roll) and action.new_expiry == E_MID and action.new_strike == 105.0
    # Nothing to roll to: closed alone rather than assigned.
    action = _pmw().next_step(_ctx(longs=[leaps], legs=[itm], params={"roll_at_dte": 10, "turn_on_itm": False, "min_dte": 60}))
    assert isinstance(action, Close) and action.legs == ((itm.occ, -1),) and "closed rather than assigned" in action.reason
    # Once the wheel has turned nothing is held: the next step buys again.
    action = _pmw().next_step(_ctx())
    assert isinstance(action, BuyCall)


def test_the_leaps_is_rolled_out_near_its_expiry_and_down_when_its_delta_has_fallen():
    # 283 days left, roll at 300: out to the next expiry in the window.
    action = _pmw().next_step(_ctx(longs=[_leaps()], params={"leaps_roll_at_dte": 300}))
    assert isinstance(action, Roll) and action.close_side == "long" and action.new_side == "buy"
    assert action.new_expiry == E_LEAPS2 and action.new_strike == 92.5 and "to expiry" in action.reason
    rendered = render(action, "XYZ")
    assert rendered["roll"]["close"]["legs"] == [{"symbol": _leaps().occ, "qty": 1}]
    assert rendered["roll"]["open"]["strategy"] == "long_call" and rendered["roll"]["open"]["long_strike"] == 92.5
    RollRequest(**rendered["roll"])
    assert "(long)" in rendered["sentence"]
    # The stock fell to 90 and the delta with it: rolled down on the same expiry.
    fallen = _leaps(delta=0.5, mark=6.0)
    action = _pmw().next_step(_ctx(longs=[fallen], spot=90.0))
    assert isinstance(action, Roll) and action.new_expiry == E_LEAPS and action.new_strike == 85.0 and "delta 0.50" in action.reason
    # The delta rule can be switched off.
    action = _pmw().next_step(_ctx(longs=[fallen], spot=90.0, params={"leaps_min_delta": 0}))
    assert isinstance(action, SellCall)
    # A short call is dealt with first.
    short = _leg("call", 105.0, E_SHORT, entry=2.0, mark=0.5)
    action = _pmw().next_step(_ctx(longs=[_leaps()], legs=[short], params={"leaps_roll_at_dte": 300}))
    assert isinstance(action, Roll) and action.close_occ == short.occ


def test_a_comfortable_pair_is_held_with_a_status_line():
    short = _leg("call", 105.0, E_MID, entry=2.0, mark=1.8)
    action = _pmw().next_step(_ctx(longs=[_leaps()], legs=[short]))
    assert isinstance(action, Hold) and _leaps().occ in action.reason and short.occ in action.reason and "Δ 0.80" in action.reason


def test_shares_count_as_cover_too():
    action = _pmw().next_step(_ctx(shares=100, avg_entry=100.0))
    assert isinstance(action, SellCall) and action.strike == 105.0 and "100 shares held" in action.reason


# --- snapshot --------------------------------------------------------------------------


def test_the_snapshot_reads_the_long_side_and_derives_the_leaps_basis():
    leaps = format_occ("XYZ", E_LEAPS, "call", 92.5)
    short = format_occ("XYZ", E_MID, "call", 105.0)
    events = [
        {"at": "2026-08-01T14:00:00+00:00", "kind": "bought_call", "occ": leaps, "qty": 1, "price": 11.0, "cash_delta": -1100.0, "note": None},
        {"at": "2026-08-02T14:00:00+00:00", "kind": "sold_call", "occ": short, "qty": 1, "price": 1.5, "cash_delta": 150.0, "note": None},
    ]
    marks = [
        {"symbol": leaps, "qty": "1", "avg_entry_price": "11", "current_price": "12.1", "delta": 0.8},
        {"symbol": short, "qty": "-1", "avg_entry_price": "1.5", "current_price": "0.6"},
    ]
    snap = build_snapshot("XYZ", marks=marks, share_position=None, events=events, today=TODAY)
    assert snap.phase == "diagonal" and snap.shares == 0
    assert len(snap.open_legs) == 1 and snap.open_legs[0].side == "short" and snap.open_legs[0].profit_pct == pytest.approx(60.0)
    assert len(snap.long_legs) == 1 and snap.long_legs[0].side == "long" and snap.long_legs[0].profit_pct == pytest.approx(10.0)
    assert snap.long_legs[0].delta == 0.8
    assert snap.premiums_collected == 150.0 and snap.long_pnl == -1100.0 and snap.realized_pnl == -950.0
    assert snap.cost_basis == pytest.approx(92.5 + (1100.0 - 150.0) / 100)
    assert snap.to_dict()["open_legs"][1]["side"] == "long"
    # The wheel turned: both legs sold, the long side's gain realized.
    events += [
        {"at": "2026-09-01T14:00:00+00:00", "kind": "closed", "occ": short, "qty": 1, "price": 5.0, "cash_delta": -500.0, "note": None},
        {"at": "2026-09-01T14:00:00+00:00", "kind": "sold_long", "occ": leaps, "qty": 1, "price": 18.0, "cash_delta": 1800.0, "note": None},
    ]
    done = build_snapshot("XYZ", marks=[], share_position=None, events=events, today=TODAY)
    assert done.phase == "cash" and done.premiums_collected == -350.0 and done.long_pnl == 700.0 and done.realized_pnl == 350.0
    assert done.cost_basis is None
    only_long = build_snapshot("XYZ", marks=marks[:1], share_position=None, events=events[:1], today=TODAY)
    assert only_long.phase == "long_call"


# --- the runner's mapping of long-leg orders -----------------------------------------------


def test_long_leg_orders_become_the_long_sides_events():
    campaign = {"symbol": "XYZ"}
    leaps = format_occ("XYZ", E_LEAPS, "call", 92.5)
    leaps2 = format_occ("XYZ", E_LEAPS2, "call", 92.5)
    short = format_occ("XYZ", E_MID, "call", 105.0)
    bought = {"id": "o1", "underlying": "XYZ", "status": "filled", "strategy": "long_call", "qty": "1", "net_fill_price": 11.0, "symbol": leaps, "filled_at": "t1"}
    (ev,) = order_events(campaign, bought)
    assert ev["kind"] == "bought_call" and ev["cash_delta"] == -1100.0 and ev["price"] == 11.0
    sold = {
        "id": "o2", "underlying": "XYZ", "status": "filled", "strategy": "close", "qty": "1", "net_fill_price": -12.0,
        "legs": [{"symbol": leaps, "position_intent": "sell_to_close", "fill_price": 12.0}],
    }
    (ev,) = order_events(campaign, sold)
    assert ev["kind"] == "sold_long" and ev["cash_delta"] == 1200.0
    turned = {
        "id": "o3", "underlying": "XYZ", "status": "filled", "strategy": "close", "qty": "1", "net_fill_price": -13.0,
        "legs": [
            {"symbol": short, "position_intent": "buy_to_close", "fill_price": 5.0},
            {"symbol": leaps, "position_intent": "sell_to_close", "fill_price": 18.0},
        ],
    }
    first, second = order_events(campaign, turned)
    assert first["kind"] == "closed" and first["occ"] == short and first["cash_delta"] == -500.0
    assert second["kind"] == "sold_long" and second["occ"] == leaps and second["cash_delta"] == 1800.0
    rolled = {
        "id": "o4", "underlying": "XYZ", "status": "filled", "strategy": "roll", "qty": "1", "net_fill_price": 2.0,
        "legs": [
            {"symbol": leaps, "position_intent": "sell_to_close", "fill_price": 12.0},
            {"symbol": leaps2, "position_intent": "buy_to_open", "fill_price": 14.0},
        ],
    }
    old, new = order_events(campaign, rolled)
    assert old["kind"] == "sold_long" and old["cash_delta"] == 1200.0 and new["kind"] == "bought_call" and new["cash_delta"] == -1400.0
    settled = {
        "id": "o5", "underlying": "XYZ", "status": "expired", "strategy": "cash_settled", "qty": "1", "net_fill_price": -7.5,
        "legs": [{"symbol": leaps, "position_intent": "sell_to_close", "fill_price": 7.5}],
    }
    (ev,) = order_events(campaign, settled)
    assert ev["kind"] == "sold_long" and ev["cash_delta"] == 750.0
    # The simulated service's public shape carries the leg's fill as a money string.
    sim_turned = {
        **turned, "id": "o7",
        "legs": [
            {"symbol": short, "position_intent": "buy_to_close", "filled_avg_price": "5.00"},
            {"symbol": leaps, "position_intent": "sell_to_close", "filled_avg_price": "18.00"},
        ],
    }
    first, second = order_events(campaign, sim_turned)
    assert first["cash_delta"] == -500.0 and second["cash_delta"] == 1800.0
    # A short leg's close is still a `closed`.
    short_close = {**sold, "id": "o6", "net_fill_price": 0.5, "legs": [{"symbol": short, "position_intent": "buy_to_close", "fill_price": 0.5}]}
    (ev,) = order_events(campaign, short_close)
    assert ev["kind"] == "closed" and ev["cash_delta"] == -50.0
