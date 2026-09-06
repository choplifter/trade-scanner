"""Walk a playbook over months of daily closes with synthetic chains.

Each session, in order: settle the legs that have expired (the same
decision table the simulated book uses, app.trading.sim.settlement),
estimate the day's volatility from the trailing closes, build the day's
chains (synthetic_chain), rebuild the campaign snapshot from the paper
book and the events (snapshot.build_snapshot -- the same arithmetic the
live runner uses), ask the playbook for its next step and carry it out at
the synthetic bid/ask. The output is the equity curve against buying the
shares outright, the events, and a summary.

What this is not: a market backtest. The prices are Black-Scholes on a
realized-volatility estimate times a factor, one flat sigma per day, no
skew, no dividends, European exercise, fills at the synthetic bid/ask. The
result says how the rules behave -- how often a put is assigned, how many
rolls, how long the shares are held -- not what they would have earned.
The response carries `synthetic: true` and the frontend says so.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone

from pydantic import BaseModel, Field

from app.options.occ import format_occ, try_parse_occ
from app.playbooks import loader
from app.playbooks.actions import Close, Hold, Roll, SellCall, SellPut
from app.playbooks.context import CalendarView, ChainView, PlaybookContext
from app.playbooks.snapshot import build_snapshot
from app.playbooks.synthetic_chain import VOL_FLOOR, VOL_WINDOW, build_chain, realized_vol, synthetic_expiries
from app.services.market_clock import ET
from app.trading.errors import OrderRejected
from app.trading.sim.settlement import decide_settlement

logger = logging.getLogger(__name__)

MAX_MONTHS = 24
MIN_SESSIONS = VOL_WINDOW + 5


class BacktestRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=12)
    playbook: str = Field(default="wheel", min_length=1, max_length=64)
    params: dict = Field(default_factory=dict)
    months: int = Field(default=12, ge=1, le=MAX_MONTHS)
    # sigma = realized vol x iv_premium: options usually trade above what
    # the stock then realizes; 1.15 is a common rule of thumb, not a fact.
    iv_premium: float = Field(default=1.15, ge=0.5, le=3.0)
    starting_cash: float = Field(default=100_000.0, gt=0)
    # Bid/ask as a fraction of the mid.
    spread_frac: float = Field(default=0.02, ge=0.0, le=0.2)


@dataclass
class _Leg:
    occ: str
    kind: str
    strike: float
    expiry: date
    qty: int
    entry_credit: float


@dataclass
class _Book:
    cash: float
    shares: int = 0
    avg_entry: float | None = None
    legs: list[_Leg] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    def event(self, day: date, kind: str, *, occ=None, qty=0, price=None, cash_delta=None, note=None) -> None:
        self.events.append(
            {
                "at": datetime.combine(day, time(16, 0), tzinfo=timezone.utc).isoformat(),
                "kind": kind, "occ": occ, "qty": qty, "price": price, "cash_delta": cash_delta, "note": note,
            }
        )
        self.counts[kind] = self.counts.get(kind, 0) + 1

    def buy_shares(self, qty: int, price: float) -> None:
        total = (self.avg_entry or 0.0) * self.shares + price * qty
        self.shares += qty
        self.avg_entry = total / self.shares if self.shares else None
        self.cash -= price * qty

    def sell_shares(self, qty: int, price: float) -> None:
        self.shares -= qty
        self.cash += price * qty
        if self.shares <= 0:
            self.shares = 0
            self.avg_entry = None


def closes_by_session(bars) -> list[tuple[date, float]]:
    out: dict[date, float] = {}
    for bar in bars or []:
        ts = getattr(bar, "timestamp", None)
        close = getattr(bar, "close", None)
        if ts is None or close is None:
            continue
        day = (ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)).astimezone(ET).date() if isinstance(ts, datetime) else ts
        out[day] = float(close)
    return sorted(out.items())


def _settle(book: _Book, day: date, spot: float) -> None:
    """Every leg whose expiry has passed (the first session on or after it),
    settled the way the simulated book settles it."""
    for leg in [l for l in book.legs if l.expiry <= day]:
        book.legs.remove(leg)
        for s in decide_settlement(side="short", kind=leg.kind, strike=leg.strike, spot=spot, contracts=leg.qty, shares_long=book.shares):
            if s.outcome == "expired":
                book.event(day, "expired", occ=leg.occ, qty=s.contracts, price=0.0, cash_delta=0.0)
            elif s.outcome == "cash_settled":
                cost = s.option_price * 100 * s.contracts
                book.cash -= cost
                book.event(day, "cash_settled", occ=leg.occ, qty=s.contracts, price=s.option_price, cash_delta=-cost)
            elif s.outcome == "assigned":
                book.buy_shares(s.share_qty, s.share_price or leg.strike)
                book.event(day, "assigned", occ=leg.occ, qty=s.share_qty, price=s.share_price, cash_delta=0.0)
            elif s.outcome == "called_away":
                book.sell_shares(s.share_qty, s.share_price or leg.strike)
                book.event(day, "called_away", occ=leg.occ, qty=-s.share_qty, price=s.share_price, cash_delta=0.0)


def _marks(book: _Book, chain: ChainView) -> list[dict]:
    marks = []
    for leg in book.legs:
        mid = chain.mid(leg.kind, leg.strike, leg.expiry)
        marks.append({"symbol": leg.occ, "qty": str(-leg.qty), "avg_entry_price": str(leg.entry_credit), "current_price": str(mid if mid is not None else leg.entry_credit)})
    return marks


def _reserved(book: _Book) -> float:
    return sum(l.strike * 100 * l.qty for l in book.legs if l.kind == "put")


def _sell(book: _Book, day: date, chain: ChainView, kind: str, strike: float, expiry: date, qty: int, spread_frac: float, event_kind: str, symbol: str) -> float | None:
    quote = chain.quote(kind, strike, expiry)
    if quote is None or quote.bid is None:
        return None
    price = float(quote.bid)
    occ = format_occ(symbol, expiry, kind, strike)
    book.legs.append(_Leg(occ=occ, kind=kind, strike=strike, expiry=expiry, qty=qty, entry_credit=price))
    credit = price * 100 * qty
    book.cash += credit
    book.event(day, event_kind, occ=occ, qty=qty, price=price, cash_delta=credit)
    return price


def _close(book: _Book, day: date, chain: ChainView, leg: _Leg, event_kind: str, note: str | None = None) -> float:
    quote = chain.quote(leg.kind, leg.strike, leg.expiry)
    price = float(quote.ask) if quote is not None and quote.ask is not None else max(chain.spot - leg.strike, 0.0) if leg.kind == "call" else max(leg.strike - chain.spot, 0.0)
    book.legs.remove(leg)
    cost = price * 100 * leg.qty
    book.cash -= cost
    book.event(day, event_kind, occ=leg.occ, qty=leg.qty, price=price, cash_delta=-cost, note=note)
    return price


def walk(symbol: str, playbook, params: dict, sessions: list[tuple[date, float]], *, iv_premium: float, starting_cash: float, spread_frac: float, earnings_dates: list[date] | None = None) -> dict:
    """The pure walk over (day, close) pairs. Raises OrderRejected when
    there are too few sessions to estimate a volatility from."""
    if len(sessions) < MIN_SESSIONS:
        raise OrderRejected(f"Only {len(sessions)} sessions of history for {symbol}; the walk needs {MIN_SESSIONS}.", field="months")
    symbol = symbol.upper()
    book = _Book(cash=starting_cash)
    closes: list[float] = []
    equity: list[dict] = []
    first_spot = None
    days_in_shares = 0
    walked = 0
    earnings = sorted(earnings_dates or [])
    for day, spot in sessions:
        closes.append(spot)
        if len(closes) <= VOL_WINDOW:
            continue
        walked += 1
        if first_spot is None:
            first_spot = spot
        _settle(book, day, spot)
        sigma = max((realized_vol(closes) or VOL_FLOOR) * iv_premium, VOL_FLOOR)
        # Only the expiries the script can act on -- its DTE window with some
        # slack -- plus the ones it holds; the whole board every day would be
        # most of the walk's cost for chains nobody reads.
        lo = int(params.get("min_dte", 21)) - 7
        hi = int(params.get("max_dte", 45)) + 14
        board = synthetic_expiries(day)
        # The script takes the nearest expiry in its window (and rolls to the
        # next one after a held leg), so three in the window are plenty.
        expiries = set([e for e in board if lo <= (e - day).days <= hi][:3]) | {l.expiry for l in book.legs if l.expiry > day}
        if not expiries:
            expiries = set(board[:3])
        chains = {e: build_chain(symbol, day, spot, sigma, e, spread_frac=spread_frac) for e in sorted(expiries)}
        chain = ChainView(chains, day)
        snapshot = build_snapshot(
            symbol,
            marks=_marks(book, chain),
            share_position={"qty": book.shares, "avg_entry_price": book.avg_entry} if book.shares > 0 else None,
            events=book.events,
            today=day,
        )
        next_earnings = next((d for d in earnings if d >= day), None)
        ctx = PlaybookContext(
            symbol=symbol, account="backtest", now=datetime.combine(day, time(15, 30), tzinfo=ET), today=day, spot=spot,
            shares=snapshot.shares, shares_avg_entry=snapshot.shares_avg_entry, cost_basis=snapshot.cost_basis,
            open_legs=snapshot.open_legs, premiums_collected=snapshot.premiums_collected, realized_pnl=snapshot.realized_pnl,
            events=(), chain=chain, calendar=CalendarView(next_earnings=next_earnings), params=params, phase=snapshot.phase,
            cash_available=max(book.cash - _reserved(book), 0.0),
        )
        action = playbook.next_step(ctx)
        if isinstance(action, SellPut):
            _sell(book, day, chain, "put", action.strike, action.expiry, action.qty, spread_frac, "sold_put", symbol)
        elif isinstance(action, SellCall):
            _sell(book, day, chain, "call", action.strike, action.expiry, action.qty, spread_frac, "sold_call", symbol)
        elif isinstance(action, Roll):
            leg = next((l for l in book.legs if l.occ == action.close_occ), None)
            if leg is not None:
                closed_at = _close(book, day, chain, leg, "closed", note="rolled")
                opened_at = _sell(book, day, chain, action.new_kind, action.new_strike, action.new_expiry, action.qty, spread_frac, "rolled", symbol)
                if opened_at is not None:
                    book.events[-1]["note"] = f"closed {leg.occ} at {closed_at:.2f}, net {(opened_at - closed_at):+.2f}"
        elif isinstance(action, Close):
            leg = next((l for l in book.legs if l.occ == action.occ), None)
            if leg is not None:
                _close(book, day, chain, leg, "closed")
        elif isinstance(action, Hold) or action is None:
            pass
        if book.shares > 0:
            days_in_shares += 1
        legs_value = sum((chain.mid(l.kind, l.strike, l.expiry) or 0.0) * 100 * l.qty for l in book.legs)
        total = book.cash + book.shares * spot - legs_value
        benchmark = starting_cash / first_spot * spot if first_spot else starting_cash
        equity.append({"date": day.isoformat(), "equity": round(total, 2), "benchmark": round(benchmark, 2), "spot": spot, "shares": book.shares, "legs": len(book.legs)})

    if not equity:
        raise OrderRejected("Nothing to walk: no session after the volatility window.", field="months")
    premiums = sum(e["cash_delta"] or 0.0 for e in book.events if e["kind"] in ("sold_put", "sold_call", "closed", "rolled", "cash_settled"))
    final = equity[-1]["equity"]
    peak = -float("inf")
    max_dd = 0.0
    for point in equity:
        peak = max(peak, point["equity"])
        if peak > 0:
            max_dd = max(max_dd, (peak - point["equity"]) / peak * 100)
    last_snapshot = build_snapshot(
        symbol, marks=[], share_position={"qty": book.shares, "avg_entry_price": book.avg_entry} if book.shares > 0 else None, events=book.events, today=sessions[-1][0]
    )
    return {
        "symbol": symbol,
        "playbook": playbook.stem,
        "params": params,
        "synthetic": True,
        "iv_premium": iv_premium,
        "spread_frac": spread_frac,
        "sessions": walked,
        "from": equity[0]["date"],
        "to": equity[-1]["date"],
        "equity": equity,
        "events": book.events,
        "open_legs": [{"occ": l.occ, "kind": l.kind, "strike": l.strike, "expiry": l.expiry.isoformat(), "qty": l.qty, "entry_credit": l.entry_credit} for l in book.legs],
        "summary": {
            "starting_cash": starting_cash,
            "final_equity": round(final, 2),
            "total_return_pct": round((final / starting_cash - 1) * 100, 2),
            "buy_and_hold_return_pct": round((equity[-1]["benchmark"] / starting_cash - 1) * 100, 2),
            "premiums": round(premiums, 2),
            "realized_pnl": round(last_snapshot.realized_pnl, 2),
            "puts_sold": book.counts.get("sold_put", 0),
            "calls_sold": book.counts.get("sold_call", 0),
            "rolls": book.counts.get("rolled", 0),
            "assignments": book.counts.get("assigned", 0),
            "called_away": book.counts.get("called_away", 0),
            "expired": book.counts.get("expired", 0),
            "max_drawdown_pct": round(max_dd, 2),
            "days_in_shares_pct": round(days_in_shares / walked * 100, 1) if walked else 0.0,
            "shares_at_end": book.shares,
            "cost_basis_at_end": round(last_snapshot.cost_basis, 2) if last_snapshot.cost_basis is not None else None,
        },
        "disclaimer": (
            "Synthetic prices: Black-Scholes on the trailing 20-session realized volatility x the IV premium factor, one "
            "flat sigma per day, no skew, no dividends, European exercise, fills at a fixed fraction around the mid. This "
            "shows how the rules behave, not what they would have earned."
        ),
    }


async def run_backtest(clients, req: BacktestRequest, *, earnings_calendar=None) -> dict:
    """Load the bars and walk. Raises OrderRejected for an unknown playbook,
    bad parameters or too little history."""
    playbook = loader.get_playbook(req.playbook)
    if playbook is None:
        raise OrderRejected(f"No playbook named {req.playbook!r}.", field="playbook")
    try:
        params = playbook.resolve_params(req.params)
    except (ValueError, TypeError) as exc:
        raise OrderRejected(str(exc), field="params") from exc
    from app.market_data.bars import get_daily_bars_multi

    symbol = req.symbol.upper()
    bars = (await get_daily_bars_multi(clients, [symbol], lookback_days=req.months * 31 + 45)).get(symbol, [])
    sessions = closes_by_session(bars)
    earnings_dates: list[date] = []
    if earnings_calendar is not None:
        try:
            earnings_dates = list(await earnings_calendar.report_dates(symbol))
        except Exception:
            logger.exception("Backtest earnings dates failed for %s", symbol)
    return walk(
        symbol, playbook, params, sessions, iv_premium=req.iv_premium, starting_cash=req.starting_cash, spread_frac=req.spread_frac,
        earnings_dates=earnings_dates,
    )


def occ_underlying(occ: str) -> str | None:
    parsed = try_parse_occ(occ)
    return parsed.underlying if parsed else None
