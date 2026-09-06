"""What a playbook is handed, and how it reads the chain.

Mirrors app.strategies.context: a playbook file is given a context and asked
for an action, never asked to fetch anything or to decide whether it should
have been called. Everything here is pure and network-free -- the runner
(runner.py) and the synthetic backtest (backtest.py) both build a
PlaybookContext, and every rule can be tested against hand-built chains.

ChainView is the one piece with behaviour: the questions a campaign asks of
a chain ("the expiry 21 to 45 days out that is not held through earnings",
"the put strike nearest 0.30 delta", "the first call strike at or above
my cost basis") answered once, here, rather than re-derived in every
script.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from app.options.chain import Chain, LegQuote, StrikeRow
from app.options.payoff import bs_greeks

Kind = Literal["call", "put"]

# Without a delta from the feed and without an IV to solve one, an
# out-of-the-money strike this far from the spot stands in for "about 0.30
# delta on a month out" -- a coarse default, used only when the chain says
# nothing better.
_FALLBACK_OTM_FRACTION = 0.05


@dataclass(frozen=True)
class OpenLeg:
    """A short leg the campaign holds, as the runner sees it: the entry
    credit per share, today's mark, and the profit taken so far as a share
    of the credit (100 % = the option is worthless)."""

    occ: str
    kind: Kind
    strike: float
    expiry: date
    dte: int
    qty: int
    entry_credit: float
    mark: float | None
    profit_pct: float | None
    delta: float | None = None


@dataclass(frozen=True)
class Event:
    at: datetime
    kind: str
    occ: str | None
    qty: int
    price: float | None
    cash_delta: float | None
    note: str | None


@dataclass(frozen=True)
class CalendarView:
    """The dates a campaign steers around: the next earnings report, the
    tracked macro releases (date, label), and the IV rank when known."""

    next_earnings: date | None = None
    macro: tuple[tuple[date, str], ...] = ()
    iv_rank: float | None = None


def _quoted(quote: LegQuote | None) -> bool:
    return quote is not None and quote.tradable and quote.mid is not None and quote.mid > 0


class ChainView:
    """Several expiries of one underlying's chain, read the way a playbook
    needs them. `chains` is keyed by expiry; `today` fixes the days-to-expiry
    arithmetic (the replay's day in a replay, the walk's day in a backtest)."""

    def __init__(self, chains: dict[date, Chain], today: date, *, now: datetime | None = None) -> None:
        self._chains = dict(chains)
        self.today = today
        self.now = now
        spots = [c.spot for c in chains.values() if c.spot]
        self.spot: float = float(spots[0]) if spots else 0.0

    @property
    def expiries(self) -> list[date]:
        return sorted(self._chains)

    def dte(self, expiry: date) -> int:
        return (expiry - self.today).days

    def has(self, expiry: date) -> bool:
        return expiry in self._chains

    def expiry_in(self, min_dte: int, max_dte: int, *, avoid: date | None = None) -> list[date]:
        """Listed expiries `min_dte`..`max_dte` days out, nearest first,
        leaving out every expiry on or after `avoid` (an earnings date: a
        contract expiring on the report day is held through it)."""
        out = [e for e in self.expiries if min_dte <= self.dte(e) <= max_dte and (avoid is None or e < avoid)]
        return out

    def rows(self, expiry: date) -> list[StrikeRow]:
        chain = self._chains.get(expiry)
        return list(chain.rows) if chain else []

    def strikes(self, expiry: date, kind: Kind) -> list[float]:
        """The strikes of `kind` with a two-sided, tradable market."""
        return sorted(r.strike for r in self.rows(expiry) if _quoted(r.call if kind == "call" else r.put))

    def quote(self, kind: Kind, strike: float, expiry: date) -> LegQuote | None:
        chain = self._chains.get(expiry)
        return chain.quote(kind, strike) if chain else None

    def mid(self, kind: Kind, strike: float, expiry: date) -> float | None:
        q = self.quote(kind, strike, expiry)
        return float(q.mid) if _quoted(q) else None

    def delta(self, kind: Kind, strike: float, expiry: date) -> float | None:
        """The feed's delta, or one solved from the quote's IV when the feed
        has none; None when neither is possible."""
        q = self.quote(kind, strike, expiry)
        if q is None:
            return None
        if q.delta is not None:
            return float(q.delta)
        if q.iv and q.iv > 0 and self.spot > 0:
            years = max(self.dte(expiry), 0) / 365.0
            if years > 0:
                d, _g, _t = bs_greeks(kind, self.spot, strike, years, float(q.iv))
                return d
        return None

    def strike_at_delta(self, kind: Kind, target_abs_delta: float, expiry: date) -> float | None:
        """The quoted strike whose |delta| is nearest `target_abs_delta`. With
        no deltas at all, the out-of-the-money strike nearest 5 % from the
        spot -- said as such in the docstring, because it is a guess."""
        candidates = [(s, self.delta(kind, s, expiry)) for s in self.strikes(expiry, kind)]
        with_delta = [(s, abs(d)) for s, d in candidates if d is not None]
        if with_delta:
            return min(with_delta, key=lambda sd: abs(sd[1] - target_abs_delta))[0]
        strikes = [s for s, _ in candidates]
        if not strikes or self.spot <= 0:
            return None
        target = self.spot * (1 - _FALLBACK_OTM_FRACTION) if kind == "put" else self.spot * (1 + _FALLBACK_OTM_FRACTION)
        otm = [s for s in strikes if (s < self.spot if kind == "put" else s > self.spot)] or strikes
        return min(otm, key=lambda s: abs(s - target))

    def first_strike_at_or_above(self, expiry: date, price: float, kind: Kind = "call") -> float | None:
        above = [s for s in self.strikes(expiry, kind) if s >= price - 1e-9]
        return above[0] if above else None

    def last_strike_at_or_below(self, expiry: date, price: float, kind: Kind = "put") -> float | None:
        below = [s for s in self.strikes(expiry, kind) if s <= price + 1e-9]
        return below[-1] if below else None


@dataclass(frozen=True)
class PlaybookContext:
    """Everything a playbook is allowed to look at, at one moment.

    shares / shares_avg_entry: the underlying held long and its average
    entry (an assignment enters at the strike). cost_basis: per share, the
    average entry less every premium the campaign has collected -- what the
    shares have to be called away above for the campaign to have made
    money; None before any shares are held. cash_available: the account's
    options buying power, collateral of open positions already deducted.
    """

    symbol: str
    account: str
    now: datetime
    today: date
    spot: float
    shares: int
    shares_avg_entry: float | None
    cost_basis: float | None
    open_legs: tuple[OpenLeg, ...]
    premiums_collected: float
    realized_pnl: float
    events: tuple[Event, ...]
    chain: ChainView
    calendar: CalendarView
    params: dict
    phase: str
    cash_available: float

    @property
    def short_puts(self) -> tuple[OpenLeg, ...]:
        return tuple(l for l in self.open_legs if l.kind == "put")

    @property
    def short_calls(self) -> tuple[OpenLeg, ...]:
        return tuple(l for l in self.open_legs if l.kind == "call")

    def leg_is_itm(self, leg: OpenLeg) -> bool:
        return leg.strike > self.spot if leg.kind == "put" else leg.strike < self.spot

    def param(self, name: str, default=None):
        return self.params.get(name, default)
