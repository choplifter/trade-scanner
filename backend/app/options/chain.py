"""Option chains: what the picker shows and what a spread's legs are priced
from. This module holds the *shapes* and the pure normalisation; the
fetch-and-cache half lives in app.options.chain_fetch so the pure part is
testable without any SDK object.

A LegQuote is one contract as the picker sees it. Greeks and IV may be
None: Alpaca cannot compute them for same-day expiry (time-to-expiry is
zero) and skips contracts whose IV solver did not converge -- see the
docstring of app.market_data.gamma_exposure, where this was verified live.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable

from app.options.occ import Kind


@dataclass(frozen=True)
class ContractMeta:
    """From TradingClient.get_option_contracts: the contract's identity plus
    the two things the data API does not carry, open interest and whether
    Alpaca lets it trade at all."""

    symbol: str
    underlying: str
    root: str
    expiry: date
    kind: Kind
    strike: float
    open_interest: int
    tradable: bool
    close_price: float | None


@dataclass(frozen=True)
class LegQuote:
    symbol: str
    strike: float
    kind: Kind
    expiry: date
    bid: float | None
    ask: float | None
    mid: float | None
    last: float | None
    bid_size: int | None
    ask_size: int | None
    delta: float | None
    gamma: float | None
    theta: float | None
    iv: float | None
    open_interest: int
    tradable: bool

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "strike": self.strike,
            "kind": self.kind,
            "expiry": self.expiry.isoformat(),
            "bid": self.bid,
            "ask": self.ask,
            "mid": self.mid,
            "last": self.last,
            "bid_size": self.bid_size,
            "ask_size": self.ask_size,
            "delta": self.delta,
            "gamma": self.gamma,
            "theta": self.theta,
            "iv": self.iv,
            "open_interest": self.open_interest,
            "tradable": self.tradable,
        }


@dataclass(frozen=True)
class StrikeRow:
    strike: float
    call: LegQuote | None
    put: LegQuote | None

    def to_dict(self) -> dict:
        return {
            "strike": self.strike,
            "call": self.call.to_dict() if self.call else None,
            "put": self.put.to_dict() if self.put else None,
        }


@dataclass
class Chain:
    underlying: str
    expiry: date
    spot: float
    feed: str
    as_of: datetime
    rows: list[StrikeRow] = field(default_factory=list)

    def quote(self, kind: Kind, strike: float) -> LegQuote | None:
        for row in self.rows:
            if abs(row.strike - strike) < 1e-6:
                return row.call if kind == "call" else row.put
        return None

    def to_dict(self) -> dict:
        return {
            "underlying": self.underlying,
            "expiry": self.expiry.isoformat(),
            "spot": self.spot,
            "feed": self.feed,
            "as_of": self.as_of.isoformat(),
            "rows": [row.to_dict() for row in self.rows],
        }


@dataclass(frozen=True)
class ExpiryInfo:
    expiry: date
    dte: int
    contract_count: int

    def to_dict(self) -> dict:
        return {"expiry": self.expiry.isoformat(), "dte": self.dte, "contract_count": self.contract_count}


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None  # NaN guard


def mid_price(bid: float | None, ask: float | None, last: float | None) -> float | None:
    """The picker's price for a contract: the bid/ask midpoint when both
    sides are quoted, else the last trade, else nothing. A one-sided quote
    (bid 0, ask 1.20) is not a market and must not become a 0.60 "mid"."""
    if bid is not None and ask is not None and bid > 0 and ask > 0 and ask >= bid:
        return round((bid + ask) / 2, 4)
    if last is not None and last > 0:
        return last
    return None


def quote_from_snapshot(meta: ContractMeta, snap: Any) -> LegQuote:
    """One OptionsSnapshot (or nothing) joined to its ContractMeta. Every
    attribute is read defensively: the SDK models carry Optional fields and
    the tests hand in duck-typed fakes."""
    latest_quote = getattr(snap, "latest_quote", None) if snap is not None else None
    latest_trade = getattr(snap, "latest_trade", None) if snap is not None else None
    greeks = getattr(snap, "greeks", None) if snap is not None else None
    bid = _num(getattr(latest_quote, "bid_price", None))
    ask = _num(getattr(latest_quote, "ask_price", None))
    last = _num(getattr(latest_trade, "price", None))
    bid_size = getattr(latest_quote, "bid_size", None)
    ask_size = getattr(latest_quote, "ask_size", None)
    return LegQuote(
        symbol=meta.symbol,
        strike=meta.strike,
        kind=meta.kind,
        expiry=meta.expiry,
        bid=bid,
        ask=ask,
        mid=mid_price(bid, ask, last),
        last=last,
        bid_size=int(bid_size) if bid_size is not None else None,
        ask_size=int(ask_size) if ask_size is not None else None,
        delta=_num(getattr(greeks, "delta", None)),
        gamma=_num(getattr(greeks, "gamma", None)),
        theta=_num(getattr(greeks, "theta", None)),
        iv=_num(getattr(snap, "implied_volatility", None)) if snap is not None else None,
        open_interest=meta.open_interest,
        tradable=meta.tradable,
    )


def build_chain_rows(
    contracts: dict[str, ContractMeta], snapshots: dict[str, Any], expiry: date
) -> list[StrikeRow]:
    """Contracts of one expiry, joined to their snapshots by symbol, one row
    per strike with the call and the put side by side, ascending strike. A
    contract without a snapshot still gets a row (with empty quotes): the
    strike exists and can be traded, it just has no market right now."""
    by_strike: dict[float, dict[str, LegQuote]] = {}
    for meta in contracts.values():
        if meta.expiry != expiry:
            continue
        quote = quote_from_snapshot(meta, snapshots.get(meta.symbol))
        by_strike.setdefault(meta.strike, {})[meta.kind] = quote
    return [
        StrikeRow(strike=strike, call=sides.get("call"), put=sides.get("put"))
        for strike, sides in sorted(by_strike.items())
    ]


def expiries_from_contracts(contracts: Iterable[ContractMeta], today: date) -> list[ExpiryInfo]:
    counts: dict[date, int] = {}
    for meta in contracts:
        counts[meta.expiry] = counts.get(meta.expiry, 0) + 1
    return [
        ExpiryInfo(expiry=expiry, dte=(expiry - today).days, contract_count=count)
        for expiry, count in sorted(counts.items())
        if expiry >= today
    ]
