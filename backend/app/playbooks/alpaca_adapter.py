"""Alpaca's orders and activities in the shape the playbook runner reads.

The runner reconciles a campaign from *the simulated book's* order rows:
one row per package with a `strategy` (cash_secured_put, covered_call,
close, roll, expiry, assigned, called_away), a signed `net_fill_price` per
share, `legs` with their position_intent, and `filled_at`. Alpaca reports
option orders in its own shape (a single-leg order, or an MLEG parent with
legs) and settlements not as orders at all but as account activities. This
module translates both, purely, so runner.order_events stays one mapping.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

from app.options.occ import try_parse_occ
from app.services.market_clock import ET


def _number(value) -> float | None:
    try:
        return float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def parse_stamp(value) -> datetime | None:
    """An ISO timestamp (with 'Z' or an offset, or naive = UTC) as an aware
    datetime; None when it is not one. Cursors are compared as datetimes,
    never as strings: Alpaca stamps end in 'Z', the app's in '+00:00'."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)


def _leg_rows(order: dict) -> list[dict]:
    legs = order.get("legs")
    if legs:
        return [l for l in legs if isinstance(l, dict)]
    return [order]


def book_order_from_alpaca(order: dict) -> dict | None:
    """One filled Alpaca option order as a simulated-book order row. None for
    anything not filled, not an option, or without a fill price.

    strategy: a lone sell-to-open put is `cash_secured_put`, a lone
    sell-to-open call `covered_call`, a lone close `close`, an MLEG with one
    leg closing and one opening `roll`, an MLEG closing everything `close`;
    everything else keeps Alpaca's order class as its strategy and lands as
    a note in the campaign (the playbook did not ask for it)."""
    if str(order.get("status", "")).lower() != "filled":
        return None
    legs_in = _leg_rows(order)
    legs_out: list[dict] = []
    underlying = None
    for leg in legs_in:
        symbol = str(leg.get("symbol") or "").upper()
        parsed = try_parse_occ(symbol)
        if parsed is None:
            return None
        underlying = underlying or parsed.underlying
        price = _number(leg.get("filled_avg_price"))
        if price is None:
            return None
        legs_out.append(
            {
                "symbol": symbol,
                "kind": parsed.kind,
                "strike": parsed.strike,
                "expiry": parsed.expiry.isoformat(),
                "side": str(leg.get("side") or "").lower(),
                "position_intent": str(leg.get("position_intent") or "").lower() or None,
                "ratio_qty": int(_number(leg.get("ratio_qty")) or 1),
                "fill_price": price,
            }
        )
    if not legs_out:
        return None
    qty = int(round(_number(order.get("filled_qty")) or 0))
    if qty <= 0:
        return None
    if len(legs_out) > 1:
        # An MLEG parent's filled_qty is the package count; a leg's is the
        # package count times its ratio.
        qty = int(round(_number(order.get("filled_qty")) or qty))
    signed = 0.0
    for leg in legs_out:
        signed += (leg["fill_price"] if leg["side"] == "buy" else -leg["fill_price"]) * leg["ratio_qty"]

    intents = [l["position_intent"] or "" for l in legs_out]
    if len(legs_out) == 1:
        leg = legs_out[0]
        intent = intents[0]
        if intent == "sell_to_open" or (not intent and leg["side"] == "sell"):
            strategy = "cash_secured_put" if leg["kind"] == "put" else "covered_call"
        elif intent.endswith("_to_close"):
            strategy = "close"
        else:
            strategy = f"long_{leg['kind']}"
    elif any(i.endswith("_to_close") for i in intents) and any(i.endswith("_to_open") for i in intents):
        strategy = "roll"
    elif intents and all(i.endswith("_to_close") for i in intents):
        strategy = "close"
    else:
        strategy = str(order.get("order_class") or "mleg")

    filled_at = parse_stamp(order.get("filled_at")) or parse_stamp(order.get("updated_at")) or parse_stamp(order.get("submitted_at"))
    return {
        "id": str(order.get("id")),
        "underlying": underlying,
        "strategy": strategy,
        "status": "filled",
        "qty": qty,
        "net_fill_price": round(signed, 4),
        "legs": legs_out,
        "symbol": legs_out[0]["symbol"] if len(legs_out) == 1 else None,
        "filled_at": filled_at.isoformat() if filled_at else None,
        "submitted_at": order.get("submitted_at"),
    }


def _activity_when(activity: dict) -> datetime | None:
    stamp = parse_stamp(activity.get("transaction_time")) or parse_stamp(activity.get("date"))
    if stamp is not None and isinstance(activity.get("date"), str) and len(str(activity.get("date"))) == 10:
        # A date-only activity: the settlement moment, 16:00 ET that day.
        day = date.fromisoformat(str(activity["date"]))
        return datetime.combine(day, time(16, 0), tzinfo=ET)
    return stamp


def book_order_from_activity(activity: dict, shares_long: int = 0) -> dict | None:
    """An assignment / expiration / exercise activity on an option contract
    as a settlement order row, the way the simulated book writes one:
    OPASN on a put -> `assigned`, OPASN on a call -> `called_away`, OPEXP ->
    `expiry`, OPEXC (a long exercised) -> `cash_settled` at price 0 with a
    note. Activities on the stock symbol (the share side) are skipped: the
    shares are read from the positions."""
    kind = str(activity.get("activity_type") or "").upper()
    if kind not in ("OPASN", "OPEXP", "OPEXC"):
        return None
    symbol = str(activity.get("symbol") or "").upper()
    parsed = try_parse_occ(symbol)
    if parsed is None:
        return None
    contracts = int(round(abs(_number(activity.get("qty")) or 0))) or 1
    when = _activity_when(activity)
    if kind == "OPASN":
        strategy = "assigned" if parsed.kind == "put" else "called_away"
        status = "assigned"
    elif kind == "OPEXP":
        strategy = "expiry"
        status = "expired"
    else:
        strategy = "cash_settled"
        status = "expired"
    return {
        "id": str(activity.get("id")),
        "underlying": parsed.underlying,
        "strategy": strategy,
        "status": status,
        "qty": contracts,
        "net_fill_price": 0.0,
        "legs": [
            {
                "symbol": symbol,
                "kind": parsed.kind,
                "strike": parsed.strike,
                "expiry": parsed.expiry.isoformat(),
                "side": "buy",
                "position_intent": "buy_to_close",
                "ratio_qty": 1,
                "fill_price": 0.0,
            }
        ],
        "symbol": symbol,
        "filled_at": when.isoformat() if when else None,
        "submitted_at": when.isoformat() if when else None,
        "note": str(activity.get("description") or "") or None,
    }
