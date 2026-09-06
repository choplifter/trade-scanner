"""What a playbook may answer with, and how an answer becomes something the
widget can load.

An action is an intention, not an order: SellPut / SellCall become an
ordinary income ticket (the shape OptionsWidget.loadStructure already
takes), BuyCall a long-call ticket (the LEAPS a poor man's wheel holds in
place of shares), Roll becomes a RollRequest for the roll ticket (a short
leg replaced by a short leg, or a long leg by a long leg), Close a
CloseSpreadRequest over one or more held legs, Hold a sentence. render()
adds the sentence the Playbooks tab shows, so a script never formats
anything itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal, Union

Kind = Literal["call", "put"]
Side = Literal["long", "short"]


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
class BuyCall:
    """A long call bought outright -- the LEAPS a poor man's wheel holds
    instead of shares. est_debit is per share."""

    expiry: date
    strike: float
    qty: int
    est_debit: float | None
    reason: str
    est_delta: float | None = None


@dataclass(frozen=True)
class Roll:
    """Close a held leg and open its replacement. close_side says how the
    old leg is held; new_side whether the new one is sold (a short put or
    call, as the wheel rolls) or bought (a long call rolled out, as the
    poor man's wheel rolls its LEAPS)."""

    close_occ: str
    qty: int
    new_kind: Kind
    new_strike: float
    new_expiry: date
    est_net: float | None  # per share, positive = credit
    reason: str
    close_side: Side = "short"
    new_side: Literal["buy", "sell"] = "sell"


@dataclass(frozen=True)
class Close:
    """Close held legs as one package: (occ, signed qty as held) per leg --
    a short leg negative, a long leg positive -- and the packages to close."""

    legs: tuple[tuple[str, int], ...]
    qty: int
    reason: str

    @property
    def occ(self) -> str:
        return self.legs[0][0]


@dataclass(frozen=True)
class Hold:
    reason: str


Action = Union[SellPut, SellCall, BuyCall, Roll, Close, Hold]


def close_one(occ: str, qty: int, reason: str, side: Side = "short") -> Close:
    """A Close of one held leg."""
    return Close(legs=((occ, -qty if side == "short" else qty),), qty=qty, reason=reason)


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


def _long_ticket(symbol: str, kind: Kind, expiry: date, strike: float, qty: int) -> dict:
    return {
        "underlying": symbol,
        "strategy": "long_call" if kind == "call" else "long_put",
        "expiry": expiry.isoformat(),
        "qty": qty,
        "long_strike": strike,
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
    if isinstance(action, BuyCall):
        debit = f", mid {action.est_debit:.2f}" if action.est_debit else ""
        delta = f" (Δ {action.est_delta:.2f})" if action.est_delta is not None else ""
        return {
            "kind": "buy_call",
            "sentence": f"Buy {action.qty}× {_day(action.expiry)} {_strike(action.strike)}C{debit}{delta}",
            "reason": action.reason,
            "ticket": _long_ticket(symbol, "call", action.expiry, action.strike, action.qty),
            "roll": None,
            "close": None,
        }
    if isinstance(action, Roll):
        net = ""
        if action.est_net is not None:
            net = f", net {'credit' if action.est_net >= 0 else 'debit'} {abs(action.est_net):.2f}"
        held_qty = -action.qty if action.close_side == "short" else action.qty
        opened = (
            _income_ticket(symbol, action.new_kind, action.new_expiry, action.new_strike, action.qty)
            if action.new_side == "sell"
            else _long_ticket(symbol, action.new_kind, action.new_expiry, action.new_strike, action.qty)
        )
        return {
            "kind": "roll",
            "sentence": (
                f"Roll {action.qty}× {action.close_occ} → {_day(action.new_expiry)} "
                f"{_strike(action.new_strike)}{action.new_kind[0].upper()}{' (long)' if action.new_side == 'buy' else ''}{net}"
            ),
            "reason": action.reason,
            "ticket": None,
            "roll": {
                "close": {"legs": [{"symbol": action.close_occ, "qty": held_qty}], "qty": action.qty},
                "open": opened,
            },
            "close": None,
        }
    if isinstance(action, Close):
        return {
            "kind": "close",
            "sentence": f"Close {action.qty}× " + " + ".join(occ for occ, _q in action.legs),
            "reason": action.reason,
            "ticket": None,
            "roll": None,
            "close": {"legs": [{"symbol": occ, "qty": q} for occ, q in action.legs], "qty": action.qty},
        }
    raise TypeError(f"not an action: {action!r}")
