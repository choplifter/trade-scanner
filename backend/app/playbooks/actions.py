"""What a playbook may answer with, and how an answer becomes something the
widget can load.

An action is an intention, not an order: SellPut / SellCall become an
ordinary income ticket (the shape OptionsWidget.loadStructure already
takes), Roll becomes a RollRequest for the roll ticket, Close a
CloseSpreadRequest, Hold a sentence. render() adds the sentence the
Playbooks tab shows, so a script never formats anything itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal, Union

Kind = Literal["call", "put"]


@dataclass(frozen=True)
class SellPut:
    expiry: date
    strike: float
    qty: int
    est_credit: float | None
    reason: str


@dataclass(frozen=True)
class SellCall:
    expiry: date
    strike: float
    qty: int
    est_credit: float | None
    reason: str


@dataclass(frozen=True)
class Roll:
    close_occ: str
    qty: int
    new_kind: Kind
    new_strike: float
    new_expiry: date
    est_net: float | None  # per share, positive = credit
    reason: str


@dataclass(frozen=True)
class Close:
    occ: str
    qty: int
    reason: str


@dataclass(frozen=True)
class Hold:
    reason: str


Action = Union[SellPut, SellCall, Roll, Close, Hold]


def _day(expiry: date) -> str:
    return f"{expiry.day} {expiry:%b}"


def _strike(strike: float) -> str:
    return f"{strike:g}"


def _income_ticket(symbol: str, kind: Kind, expiry: date, strike: float, qty: int) -> dict:
    return {
        "underlying": symbol,
        "strategy": "cash_secured_put" if kind == "put" else "covered_call",
        "expiry": expiry.isoformat(),
        "qty": qty,
        "legs": [{"kind": kind, "strike": strike, "side": "sell"}],
    }


def render(action: Action, symbol: str) -> dict:
    """The proposal the tab shows and the widget loads: a kind, a sentence, a
    reason, and one of ticket / roll / close (or none for a hold)."""
    if isinstance(action, Hold):
        return {"kind": "hold", "sentence": "Hold", "reason": action.reason, "ticket": None, "roll": None, "close": None}
    if isinstance(action, (SellPut, SellCall)):
        kind: Kind = "put" if isinstance(action, SellPut) else "call"
        credit = f", mid {action.est_credit:.2f} ({action.est_credit / action.strike * 100:.1f} % of strike)" if action.est_credit else ""
        return {
            "kind": "sell_put" if kind == "put" else "sell_call",
            "sentence": f"Sell {action.qty}× {_day(action.expiry)} {_strike(action.strike)}{kind[0].upper()}{credit}",
            "reason": action.reason,
            "ticket": _income_ticket(symbol, kind, action.expiry, action.strike, action.qty),
            "roll": None,
            "close": None,
        }
    if isinstance(action, Roll):
        net = ""
        if action.est_net is not None:
            net = f", net {'credit' if action.est_net >= 0 else 'debit'} {abs(action.est_net):.2f}"
        return {
            "kind": "roll",
            "sentence": (
                f"Roll {action.qty}× {action.close_occ} → {_day(action.new_expiry)} "
                f"{_strike(action.new_strike)}{action.new_kind[0].upper()}{net}"
            ),
            "reason": action.reason,
            "ticket": None,
            "roll": {
                "close": {"legs": [{"symbol": action.close_occ, "qty": -action.qty}], "qty": action.qty},
                "open": _income_ticket(symbol, action.new_kind, action.new_expiry, action.new_strike, action.qty),
            },
            "close": None,
        }
    if isinstance(action, Close):
        return {
            "kind": "close",
            "sentence": f"Close {action.qty}× {action.occ}",
            "reason": action.reason,
            "ticket": None,
            "roll": None,
            "close": {"legs": [{"symbol": action.occ, "qty": -action.qty}], "qty": action.qty},
        }
    raise TypeError(f"not an action: {action!r}")
