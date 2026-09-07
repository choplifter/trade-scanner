"""The Poor Man's Wheel: the wheel's covered-call side with a long-dated,
deep in-the-money call (a LEAPS) standing in for the 100 shares. Buy the
call at `leaps_delta` nine to eighteen months out; sell shorter calls
against it at `call_delta`; roll the short call at a profit target or near
expiry; roll the LEAPS itself out when it gets close to expiry or its delta
has fallen too far. The capital tied up is the call's debit, not the
shares' price.

Pure rules over a PlaybookContext, in this order:

  1. A short call that has earned `take_profit_pct` of its credit, or has
     `roll_at_dte` days or fewer left, is rolled to the next expiry in the
     DTE window (before the LEAPS expires) at the target delta again,
     lifted to the floor -- the strike follows the stock. The floor is the
     cost basis (the
     LEAPS strike plus its net debit per share, `min_call_above_basis`) and
     always above the LEAPS strike. In the money near expiry with
     `turn_on_itm` on, the wheel turns instead: both legs are closed as one
     package (the long call has taken the move the short call gave away)
     and the next round starts with a fresh LEAPS. Nothing to roll to: the
     short call is closed alone rather than left to be assigned.
  2. The LEAPS with `leaps_roll_at_dte` days or fewer left, or a delta
     below `leaps_min_delta`, is rolled to the first expiry in the LEAPS
     window at `leaps_delta`.
  3. No cover (no long call, fewer than 100 shares): buy the LEAPS -- the
     first expiry `leaps_min_dte`..`leaps_max_dte` days out, the strike at
     `leaps_delta`; a debit over the budget or the cash available: hold.
  4. Cover held and no short call: sell the call at `call_delta` on the
     first expiry in the window, at or above the floor.
  5. Otherwise hold, and say how the legs stand.

Nothing here is executed by this file: the runner proposes what it returns.
"""

from __future__ import annotations

from datetime import date

from app.playbooks.actions import Action, BuyCall, Close, Hold, Roll, SellCall, close_one
from app.playbooks.context import OpenLeg, PlaybookContext
from app.playbooks.loader import ParamSpec

NAME = "Poor Man's Wheel"
DESCRIPTION = (
    "Buy a deep in-the-money call nine to eighteen months out in place of the shares, and sell shorter calls against it "
    "at a target delta. The short call is rolled at a profit target or near expiry; in the money near expiry the wheel "
    "turns: both legs are closed and a fresh long call is bought. The long call is rolled out near its own expiry or "
    "when its delta has fallen too far."
)
ENABLED = True

PARAMS = [
    ParamSpec("qty", "int", 1, "Contracts", min=1, max=50, step=1, help="Long calls held, and short calls sold against them."),
    ParamSpec("leaps_delta", "float", 0.80, "LEAPS delta", min=0.60, max=0.95, step=0.05, help="Target delta of the long call bought."),
    ParamSpec("leaps_min_dte", "int", 270, "LEAPS min DTE", min=90, max=720, step=30, help="Earliest expiry for the long call."),
    ParamSpec("leaps_max_dte", "int", 540, "LEAPS max DTE", min=120, max=1000, step=30, help="Latest expiry for the long call."),
    ParamSpec("leaps_roll_at_dte", "int", 90, "Roll LEAPS at DTE", min=30, max=365, step=10, help="Roll the long call out with this many days or fewer left."),
    ParamSpec(
        "leaps_min_delta", "float", 0.60, "LEAPS min delta", min=0.0, max=0.80, step=0.05,
        help="Roll the long call when its delta has fallen below this (the stock dropped); 0 = never.",
    ),
    ParamSpec("call_delta", "float", 0.25, "Call delta", min=0.05, max=0.60, step=0.05, help="Target delta of the short call."),
    ParamSpec("min_dte", "int", 21, "Min DTE", min=1, max=120, step=1, help="Earliest expiry to sell the call on."),
    ParamSpec("max_dte", "int", 45, "Max DTE", min=2, max=180, step=1, help="Latest expiry to sell the call on."),
    ParamSpec("roll_at_dte", "int", 7, "Roll at DTE", min=0, max=60, step=1, help="Roll (or turn) with this many days or fewer left on the short call."),
    ParamSpec("take_profit_pct", "float", 50.0, "Take profit %", min=10, max=100, step=5, help="Roll the short call once this share of its credit is earned."),
    ParamSpec("avoid_earnings", "bool", True, "Avoid earnings", help="Never sell a short call on an expiry held through the next report."),
    ParamSpec(
        "min_call_above_basis", "bool", True, "Call ≥ basis",
        help="Never sell a call below the cost basis: the long call's strike plus its net debit per share.",
    ),
    ParamSpec("budget", "float", 0.0, "Budget $", min=0, step=500, help="Most debit for the long call (mid × 100 × qty); 0 = no cap."),
    ParamSpec(
        "turn_on_itm", "bool", True, "Turn when ITM",
        help="A short call in the money near expiry closes both legs -- the wheel's turn -- and a fresh long call is bought. Off: roll it up and out.",
    ),
]


def chain_windows(params: dict) -> list[tuple[int, int]]:
    return [
        (int(params.get("min_dte", 21)), int(params.get("max_dte", 45))),
        (int(params.get("leaps_min_dte", 270)), int(params.get("leaps_max_dte", 540))),
    ]


def _window(ctx: PlaybookContext) -> list[date]:
    avoid = ctx.calendar.next_earnings if ctx.param("avoid_earnings", True) else None
    return ctx.chain.expiry_in(int(ctx.param("min_dte", 21)), int(ctx.param("max_dte", 45)), avoid=avoid)


def _leaps_window(ctx: PlaybookContext) -> list[date]:
    return ctx.chain.expiry_in(int(ctx.param("leaps_min_dte", 270)), int(ctx.param("leaps_max_dte", 540)))


def _leaps(ctx: PlaybookContext) -> OpenLeg | None:
    """The long call standing in for the shares: the latest-dated, lowest
    strike when several are held."""
    calls = ctx.long_calls
    return min(calls, key=lambda l: (-l.dte, l.strike)) if calls else None


def _floor(ctx: PlaybookContext, leaps: OpenLeg | None) -> float | None:
    """The lowest strike a short call may have: above the long call's
    strike, and at or above the cost basis when `min_call_above_basis`."""
    floor = None
    if leaps is not None:
        floor = leaps.strike + 1e-6
    if ctx.param("min_call_above_basis", True) and ctx.cost_basis is not None:
        floor = max(floor or 0.0, ctx.cost_basis)
    return floor


def _short_strike(ctx: PlaybookContext, expiry: date, floor: float | None) -> tuple[float | None, str]:
    """The strike for a short call on `expiry`: the target-delta strike,
    lifted to the floor. Re-picked every roll, so the strike follows the
    stock. Returns (strike, note)."""
    strike = ctx.chain.strike_at_delta("call", float(ctx.param("call_delta", 0.25)), expiry)
    if strike is not None and floor is not None and strike < floor - 1e-9:
        lifted = ctx.chain.first_strike_at_or_above(expiry, floor, "call")
        return lifted, f", lifted to the first strike at or above the floor {floor:.2f}"
    return strike, ""


def _due(ctx: PlaybookContext, leg: OpenLeg) -> str | None:
    take = float(ctx.param("take_profit_pct", 50.0))
    at_dte = int(ctx.param("roll_at_dte", 7))
    if leg.profit_pct is not None and leg.profit_pct >= take:
        return f"{leg.profit_pct:.0f} % of the credit earned (target {take:.0f} %)"
    if leg.dte <= at_dte:
        return f"{leg.dte} day{'s' if leg.dte != 1 else ''} to expiry (roll at {at_dte})"
    return None


def _days(n: int) -> str:
    return f"{n} day{'s' if n != 1 else ''}"


def next_step(ctx: PlaybookContext) -> Action | None:
    qty = int(ctx.param("qty", 1))
    leaps = _leaps(ctx)
    cover = (ctx.shares // 100) + sum(l.qty for l in ctx.long_calls)
    window = _window(ctx)
    if leaps is not None:
        window = [e for e in window if e < leaps.expiry]
    earnings_note = (
        f"; expiries on or after earnings ({ctx.calendar.next_earnings.isoformat()}) skipped"
        if ctx.param("avoid_earnings", True) and ctx.calendar.next_earnings is not None
        else ""
    )
    floor = _floor(ctx, leaps)

    # 1. The short call: due for a roll, or in the money near expiry.
    for leg in ctx.short_calls:
        why = _due(ctx, leg)
        if why is None:
            continue
        itm = ctx.leg_is_itm(leg)
        near = leg.dte <= int(ctx.param("roll_at_dte", 7))
        if itm and near and ctx.param("turn_on_itm", True) and leaps is not None:
            n = min(leg.qty, leaps.qty)
            return Close(
                legs=((leg.occ, -n), (leaps.occ, n)),
                qty=n,
                reason=(
                    f"{leg.occ} is in the money with {_days(leg.dte)} left: the wheel turns -- close the short call and the "
                    f"long call together (spot {ctx.spot:.2f}, the long call's gain carries the move), then start again."
                ),
            )
        later = [e for e in window if e > leg.expiry]
        if not later:
            if itm and near:
                return close_one(
                    leg.occ, leg.qty,
                    f"{leg.occ}: in the money with {_days(leg.dte)} left and no expiry {ctx.param('min_dte')}–{ctx.param('max_dte')} "
                    f"days out before the long call's to roll to{earnings_note}: closed rather than assigned.",
                )
            return Hold(f"{leg.occ}: {why}, but no expiry {ctx.param('min_dte')}–{ctx.param('max_dte')} days out before the long call's to roll to{earnings_note}.")
        new_expiry = later[0]
        strike, note = _short_strike(ctx, new_expiry, floor)
        if strike is None:
            if itm and near:
                return close_one(leg.occ, leg.qty, f"{leg.occ}: in the money with {_days(leg.dte)} left and no call strike on {new_expiry.isoformat()} above the floor: closed rather than assigned.")
            return Hold(f"{leg.occ}: {why}, but no quoted call strike on {new_expiry.isoformat()} fits the floor.")
        new_mid = ctx.chain.mid("call", strike, new_expiry)
        est_net = (new_mid - leg.mark) if new_mid is not None and leg.mark is not None else None
        return Roll(
            close_occ=leg.occ, qty=leg.qty, new_kind="call", new_strike=strike, new_expiry=new_expiry, est_net=est_net,
            reason=f"{why}. The {ctx.param('call_delta')} delta strike on the new expiry{note}.",
        )

    # 2. The long call: near its own expiry, or its delta has fallen away.
    if leaps is not None:
        why = None
        out_of_time = leaps.dte <= int(ctx.param("leaps_roll_at_dte", 90))
        if out_of_time:
            why = f"{_days(leaps.dte)} to expiry (roll at {ctx.param('leaps_roll_at_dte')})"
        min_delta = float(ctx.param("leaps_min_delta", 0.60) or 0.0)
        if why is None and min_delta > 0 and leaps.delta is not None and abs(leaps.delta) < min_delta:
            why = f"delta {abs(leaps.delta):.2f} below {min_delta:.2f}"
        if why is not None:
            # Out of time: a later expiry. Delta fallen: any expiry in the
            # window that outlasts the short call, the same one at a lower
            # strike included -- but never the leg already held.
            latest_short = max((l.expiry for l in ctx.short_calls), default=date.min)
            targets = [e for e in _leaps_window(ctx) if e > latest_short and (e > leaps.expiry if out_of_time else e >= leaps.expiry)]
            target = float(ctx.param("leaps_delta", 0.80))
            picked = next(
                ((e, s) for e in targets for s in [ctx.chain.strike_at_delta("call", target, e)] if s is not None and (e, s) != (leaps.expiry, leaps.strike)),
                None,
            )
            if picked is None:
                return Hold(f"{leaps.occ}: {why}, but no expiry {ctx.param('leaps_min_dte')}–{ctx.param('leaps_max_dte')} days out with a quoted {target} delta strike to roll to.")
            new_expiry, strike = picked
            new_mid = ctx.chain.mid("call", strike, new_expiry)
            est_net = (leaps.mark - new_mid) if new_mid is not None and leaps.mark is not None else None
            return Roll(
                close_occ=leaps.occ, qty=leaps.qty, new_kind="call", new_strike=strike, new_expiry=new_expiry, est_net=est_net,
                close_side="long", new_side="buy",
                reason=f"{why}. Roll the long call out to the {ctx.param('leaps_delta')} delta strike {ctx.chain.dte(new_expiry)} days out.",
            )

    # 3. No cover: buy the long call.
    if cover < qty and not ctx.long_calls:
        targets = _leaps_window(ctx)
        if not targets:
            return Hold(f"No expiry {ctx.param('leaps_min_dte')}–{ctx.param('leaps_max_dte')} days out to buy the long call on.")
        expiry = targets[0]
        strike = ctx.chain.strike_at_delta("call", float(ctx.param("leaps_delta", 0.80)), expiry)
        if strike is None:
            return Hold(f"No quoted call strikes on {expiry.isoformat()}.")
        contracts = max(1, qty - ctx.shares // 100)
        mid = ctx.chain.mid("call", strike, expiry)
        budget = float(ctx.param("budget", 0.0) or 0.0)
        cap = min(budget, ctx.cash_available) if budget > 0 else ctx.cash_available
        if mid is not None and mid * 100 * contracts > cap:
            return Hold(
                f"The {ctx.param('leaps_delta')} delta call {ctx.chain.dte(expiry)} days out costs ${mid * 100 * contracts:,.0f}, over "
                f"{'the budget' if budget > 0 and budget <= ctx.cash_available else 'the cash available'} (${cap:,.0f})."
            )
        return BuyCall(
            expiry=expiry, strike=strike, qty=contracts, est_debit=mid, est_delta=ctx.chain.delta("call", strike, expiry),
            reason=f"No cover held: buy the {ctx.param('leaps_delta')} delta call {ctx.chain.dte(expiry)} days out in place of the shares.",
        )

    # 4. Cover held, no short call: sell one.
    if cover >= 1 and not ctx.short_calls:
        if not window:
            return Hold(f"Cover held, but no expiry {ctx.param('min_dte')}–{ctx.param('max_dte')} days out before the long call's to sell a call on{earnings_note}.")
        expiry = window[0]
        contracts = min(cover, qty)
        strike, note = _short_strike(ctx, expiry, floor)
        if strike is None:
            return Hold(f"Cover held, but no call strike on {expiry.isoformat()} at or above the floor {floor:.2f}." if floor is not None else f"No quoted call strikes on {expiry.isoformat()}.")
        mid = ctx.chain.mid("call", strike, expiry)
        what = f"{leaps.qty}× {leaps.occ} held" if leaps is not None else f"{ctx.shares} shares held"
        return SellCall(
            expiry=expiry, strike=strike, qty=contracts, est_credit=mid,
            reason=f"{what}, no call open: sell the {ctx.param('call_delta')} delta call {ctx.chain.dte(expiry)} days out{note}{earnings_note}.",
        )

    # 5. Nothing to do.
    parts = []
    if leaps is not None:
        d = f"Δ {abs(leaps.delta):.2f}, " if leaps.delta is not None else ""
        parts.append(f"{leaps.occ}: {d}{_days(leaps.dte)} to expiry (roll at {ctx.param('leaps_roll_at_dte')})")
    for leg in ctx.short_calls:
        earned = f"{leg.profit_pct:.0f} % of the credit earned" if leg.profit_pct is not None else "no mark"
        parts.append(f"{leg.occ}: {_days(leg.dte)} to expiry, {earned}; roll at {ctx.param('take_profit_pct')} % or {ctx.param('roll_at_dte')} DTE")
    return Hold("; ".join(parts) + "." if parts else "Nothing to do.")
