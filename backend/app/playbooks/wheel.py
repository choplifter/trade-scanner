"""The Wheel: sell a cash-secured put; if assigned, hold the shares and sell
covered calls above the cost basis; if called away, start again.

Pure rules over a PlaybookContext, in this order:

  1. An open short leg that has earned `take_profit_pct` of its credit, or
     has `roll_at_dte` days or fewer left, is rolled to the next expiry in
     the DTE window at the target delta again (a call lifted to the cost
     basis) -- the strike follows the stock, so a put does not sit forty
     points below a rally collecting cents. No expiry in the window that
     avoids earnings: hold.
     An in-the-money leg near expiry is *not* rolled while
     `accept_assignment` is on: being assigned the shares, or having them
     called away, is the wheel turning, not a leg in trouble.
  2. Fewer than 100 shares per contract and no short put: sell a put at
     `put_delta`, stepping down while the strike's collateral exceeds the
     budget or the cash available; none fits: hold.
  3. Shares held and no short call: sell a call at `call_delta`, but never
     below the cost basis (`min_call_above_basis`): the first strike at or
     above it, else hold.
  4. Otherwise hold, and say how the open leg stands.

Nothing here is executed by this file: the runner proposes what it returns.
"""

from __future__ import annotations

from app.playbooks.actions import Action, Hold, Roll, SellCall, SellPut
from app.playbooks.context import OpenLeg, PlaybookContext
from app.playbooks.loader import ParamSpec

NAME = "Wheel"
DESCRIPTION = (
    "Sell a cash-secured put at a target delta; when assigned, sell covered calls above the cost basis; when called "
    "away, start again. Legs are rolled at a profit target or near expiry, earnings expiries can be skipped."
)
ENABLED = True

PARAMS = [
    ParamSpec("qty", "int", 1, "Contracts", min=1, max=50, step=1, help="Packages per leg; 100 shares each when assigned."),
    ParamSpec("put_delta", "float", 0.30, "Put delta", min=0.05, max=0.50, step=0.05, help="Target |delta| of the put sold."),
    ParamSpec("call_delta", "float", 0.30, "Call delta", min=0.05, max=0.60, step=0.05, help="Target delta of the covered call."),
    ParamSpec("min_dte", "int", 21, "Min DTE", min=1, max=120, step=1, help="Earliest expiry to sell."),
    ParamSpec("max_dte", "int", 45, "Max DTE", min=2, max=180, step=1, help="Latest expiry to sell."),
    ParamSpec("roll_at_dte", "int", 7, "Roll at DTE", min=0, max=60, step=1, help="Roll a leg with this many days or fewer left."),
    ParamSpec("take_profit_pct", "float", 50.0, "Take profit %", min=10, max=100, step=5, help="Roll once this share of the credit is earned."),
    ParamSpec("avoid_earnings", "bool", True, "Avoid earnings", help="Never sell an expiry held through the next report."),
    ParamSpec("budget", "float", 0.0, "Budget $", min=0, step=500, help="Most collateral per put (strike × 100 × qty); 0 = no cap."),
    ParamSpec("min_call_above_basis", "bool", True, "Call ≥ basis", help="Never sell a call below the cost basis."),
    ParamSpec(
        "accept_assignment", "bool", True, "Accept assignment",
        help="An in-the-money leg near expiry is left to be assigned or called away -- the wheel's turn -- rather than rolled. Off: roll it.",
    ),
]


def _window(ctx: PlaybookContext) -> list:
    avoid = ctx.calendar.next_earnings if ctx.param("avoid_earnings", True) else None
    return ctx.chain.expiry_in(int(ctx.param("min_dte", 21)), int(ctx.param("max_dte", 45)), avoid=avoid)


def _roll_target_strike(ctx: PlaybookContext, leg: OpenLeg, expiry) -> float | None:
    """The strike the leg is rolled to: the target delta on the new expiry,
    a call lifted to the cost basis. Re-picked every roll rather than kept
    -- a leg rolled at its profit target is far out of the money by then,
    and keeping its strike would leave the next one earning nothing."""
    kind = leg.kind
    delta = float(ctx.param("call_delta" if kind == "call" else "put_delta", 0.30))
    strike = ctx.chain.strike_at_delta(kind, delta, expiry)
    if kind == "call" and strike is not None and ctx.param("min_call_above_basis", True) and ctx.cost_basis is not None and strike < ctx.cost_basis:
        strike = ctx.chain.first_strike_at_or_above(expiry, ctx.cost_basis, "call")
    return strike


def _roll_due(ctx: PlaybookContext, leg: OpenLeg) -> str | None:
    take = float(ctx.param("take_profit_pct", 50.0))
    at_dte = int(ctx.param("roll_at_dte", 7))
    if leg.profit_pct is not None and leg.profit_pct >= take:
        return f"{leg.profit_pct:.0f} % of the credit earned (target {take:.0f} %)"
    if leg.dte <= at_dte:
        return f"{leg.dte} day{'s' if leg.dte != 1 else ''} to expiry (roll at {at_dte})"
    return None


def next_step(ctx: PlaybookContext) -> Action | None:
    qty = int(ctx.param("qty", 1))
    window = _window(ctx)
    earnings_note = (
        f"; expiries on or after earnings ({ctx.calendar.next_earnings.isoformat()}) skipped"
        if ctx.param("avoid_earnings", True) and ctx.calendar.next_earnings is not None
        else ""
    )

    # 1. Roll what is due -- unless it is in the money near expiry and the
    #    campaign accepts assignment: that is the wheel turning, not a leg
    #    in trouble. A profit-target roll is by construction out of the money.
    for leg in ctx.open_legs:
        why = _roll_due(ctx, leg)
        if why is None:
            continue
        if ctx.leg_is_itm(leg) and ctx.param("accept_assignment", True):
            turn = "assigned into shares" if leg.kind == "put" else "called away"
            return Hold(f"{leg.occ}: in the money with {leg.dte} day{'s' if leg.dte != 1 else ''} left; left to be {turn} at {leg.strike:g}.")
        later = [e for e in window if e > leg.expiry]
        if not later:
            return Hold(f"{leg.occ}: {why}, but no expiry {ctx.param('min_dte')}–{ctx.param('max_dte')} days out to roll to{earnings_note}.")
        new_expiry = later[0]
        strike = _roll_target_strike(ctx, leg, new_expiry)
        if strike is None:
            return Hold(f"{leg.occ}: {why}, but no quoted {leg.kind} strike on {new_expiry.isoformat()} fits the rule.")
        new_mid = ctx.chain.mid(leg.kind, strike, new_expiry)
        est_net = (new_mid - leg.mark) if new_mid is not None and leg.mark is not None else None
        return Roll(
            close_occ=leg.occ,
            qty=leg.qty,
            new_kind=leg.kind,
            new_strike=strike,
            new_expiry=new_expiry,
            est_net=est_net,
            reason=f"{why}. The {ctx.param('call_delta' if leg.kind == 'call' else 'put_delta')} delta strike on the new expiry{' (at or above the cost basis)' if leg.kind == 'call' and ctx.param('min_call_above_basis', True) and ctx.cost_basis is not None else ''}.",
        )

    # 2. No shares: sell a put.
    if ctx.shares < 100 * qty and not ctx.short_puts:
        if not window:
            return Hold(f"No expiry {ctx.param('min_dte')}–{ctx.param('max_dte')} days out to sell a put on{earnings_note}.")
        expiry = window[0]
        strike = ctx.chain.strike_at_delta("put", float(ctx.param("put_delta", 0.30)), expiry)
        if strike is None:
            return Hold(f"No quoted put strikes on {expiry.isoformat()}.")
        budget = float(ctx.param("budget", 0.0) or 0.0)
        cap = min(budget, ctx.cash_available) if budget > 0 else ctx.cash_available
        contracts = max(1, qty - ctx.shares // 100)
        stepped = False
        while strike is not None and strike * 100 * contracts > cap:
            strike = ctx.chain.last_strike_at_or_below(expiry, strike - 1e-6, "put")
            stepped = True
        if strike is None:
            return Hold(
                f"No put strike on {expiry.isoformat()} whose collateral fits "
                f"{'the budget' if budget > 0 and budget <= ctx.cash_available else 'the cash available'} (${cap:,.0f})."
            )
        mid = ctx.chain.mid("put", strike, expiry)
        return SellPut(
            expiry=expiry,
            strike=strike,
            qty=contracts,
            est_credit=mid,
            reason=(
                f"No shares held, no put open: sell the {ctx.param('put_delta')} delta put {ctx.chain.dte(expiry)} days out"
                + (" (stepped down to fit the collateral cap)" if stepped else "")
                + earnings_note
                + "."
            ),
        )

    # 3. Shares held: sell a call.
    if ctx.shares >= 100 and not ctx.short_calls:
        if not window:
            return Hold(f"Shares held, but no expiry {ctx.param('min_dte')}–{ctx.param('max_dte')} days out to sell a call on{earnings_note}.")
        expiry = window[0]
        contracts = min(ctx.shares // 100, qty)
        strike = ctx.chain.strike_at_delta("call", float(ctx.param("call_delta", 0.30)), expiry)
        floor_note = ""
        if strike is not None and ctx.param("min_call_above_basis", True) and ctx.cost_basis is not None and strike < ctx.cost_basis:
            strike = ctx.chain.first_strike_at_or_above(expiry, ctx.cost_basis, "call")
            floor_note = f", lifted to the first strike at or above the cost basis {ctx.cost_basis:.2f}"
        if strike is None:
            basis = f"{ctx.cost_basis:.2f}" if ctx.cost_basis is not None else "—"
            return Hold(f"Shares held, but no call strike on {expiry.isoformat()} at or above the cost basis {basis}.")
        mid = ctx.chain.mid("call", strike, expiry)
        return SellCall(
            expiry=expiry,
            strike=strike,
            qty=contracts,
            est_credit=mid,
            reason=f"{ctx.shares} shares held, no call open: sell the {ctx.param('call_delta')} delta call {ctx.chain.dte(expiry)} days out{floor_note}{earnings_note}.",
        )

    # 4. Nothing to do.
    if ctx.open_legs:
        leg = ctx.open_legs[0]
        earned = f"{leg.profit_pct:.0f} % of the credit earned" if leg.profit_pct is not None else "no mark"
        return Hold(f"{leg.occ} open: {leg.dte} days to expiry, {earned}; roll at {ctx.param('take_profit_pct')} % or {ctx.param('roll_at_dte')} DTE.")
    return Hold("Nothing to do.")
