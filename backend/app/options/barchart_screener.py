"""Barchart's option-screener exports (the iron condor and vertical spread
screeners' "download"), read so they can be set beside our own numbers.

The export is one row per structure:

    Symbol, Price~, Exp Date, DTE,
    Leg1 Strike, Type, Ask1,  Leg2 Strike, Type, Bid2,  ...
    BE+, BE-, Max Profit, Max Loss, Risk/Reward, IV Rank, Loss Prob

Each leg is three columns -- strike, Put/Call, and a price headed AskN for a
leg bought or BidN for a leg sold (Barchart prices at the natural). "Type"
repeats once per leg, so the header is read by position, not by name.
Max Profit and Max Loss are per share; Loss Prob is Barchart's, from the
at-the-money lognormal -- the same model our chance used before the skew
(checked on 784 of its condors, 2026-10-03, to a point).

Pure: text in, rows out. Finding files, enriching rows with our chains and
the endpoint are in the router.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

# Barchart names a screener export "<screen>-option-screener[-<view>]-<mm-dd-yyyy>.csv".
FILE_PATTERN = re.compile(r"^[a-z0-9\-]+-screener[a-z0-9\-]*-\d{2}-\d{2}-\d{4}(?: \(\d+\))?\.csv$", re.IGNORECASE)
_LEG = re.compile(r"^Leg(\d+) Strike$", re.IGNORECASE)
_AS_OF = re.compile(r"Downloaded from Barchart\.com as of (\d{2})-(\d{2})-(\d{4})", re.IGNORECASE)


def export_date(text: str) -> date | None:
    """The session the export's prices are from, out of Barchart's trailer
    line ("Downloaded from Barchart.com as of 10-03-2026 ...")."""
    match = _AS_OF.search(text[-400:])
    if not match:
        return None
    month, day, year = (int(g) for g in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


@dataclass(frozen=True)
class Leg:
    strike: float
    kind: str  # put / call
    side: str  # buy / sell
    price: float | None  # per share, at the natural


@dataclass
class ScreenRow:
    symbol: str
    price: float | None
    expiry: date
    dte: int | None
    legs: list[Leg]
    breakeven_up: float | None
    breakeven_down: float | None
    max_profit: float | None  # per share
    max_loss: float | None  # per share
    iv_rank: float | None  # 0-100, Barchart's
    loss_prob: float | None  # 0-1, Barchart's
    strategy: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def net(self) -> float | None:
        """Per share at the natural: positive a credit received."""
        if any(leg.price is None for leg in self.legs):
            return None
        return round(sum(leg.price if leg.side == "sell" else -leg.price for leg in self.legs), 4)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "price": self.price,
            "expiry": self.expiry.isoformat(),
            "dte": self.dte,
            "strategy": self.strategy,
            "legs": [{"strike": l.strike, "kind": l.kind, "side": l.side, "price": l.price} for l in self.legs],
            "net": self.net,
            "breakeven_up": self.breakeven_up,
            "breakeven_down": self.breakeven_down,
            "max_profit": self.max_profit,
            "max_loss": self.max_loss,
            "iv_rank": self.iv_rank,
            "loss_prob": self.loss_prob,
            "ticket": ticket_for(self),
        }


def is_screener_export(name: str) -> bool:
    return bool(FILE_PATTERN.match(name))


def list_exports(directory: Path) -> list[dict]:
    """Screener exports in `directory`, newest first."""
    if not directory.is_dir():
        return []
    out = []
    for path in directory.iterdir():
        if path.is_file() and is_screener_export(path.name):
            stat = path.stat()
            out.append({"name": path.name, "modified": stat.st_mtime, "size": stat.st_size})
    return sorted(out, key=lambda f: -f["modified"])


def _number(raw: str | None) -> float | None:
    if raw is None:
        return None
    text = raw.strip().replace(",", "").replace("%", "")
    if not text or text in {"-", "N/A", "unch"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def classify(legs: list[Leg]) -> str | None:
    """The ticket strategy a leg set is, or None for a shape we do not trade."""
    puts = sorted((l for l in legs if l.kind == "put"), key=lambda l: l.strike)
    calls = sorted((l for l in legs if l.kind == "call"), key=lambda l: l.strike)
    sides = lambda group: [l.side for l in group]  # noqa: E731
    if len(puts) == 2 and len(calls) == 2 and sides(puts) == ["buy", "sell"] and sides(calls) == ["sell", "buy"]:
        return "iron_condor"
    if len(legs) == 2 and len(puts) == 2:
        return {("buy", "sell"): "bull_put", ("sell", "buy"): "bear_put"}.get(tuple(sides(puts)))
    if len(legs) == 2 and len(calls) == 2:
        return {("buy", "sell"): "bull_call", ("sell", "buy"): "bear_call"}.get(tuple(sides(calls)))
    return None


def ticket_for(row: ScreenRow) -> dict | None:
    """The spread ticket the widget loads, or None when the shape has none."""
    puts = sorted((l for l in row.legs if l.kind == "put"), key=lambda l: l.strike)
    calls = sorted((l for l in row.legs if l.kind == "call"), key=lambda l: l.strike)
    base = {"underlying": row.symbol, "strategy": row.strategy, "expiry": row.expiry.isoformat(), "qty": 1}
    if row.strategy == "iron_condor":
        return {**base, "put_long_strike": puts[0].strike, "put_short_strike": puts[1].strike,
                "call_short_strike": calls[0].strike, "call_long_strike": calls[1].strike}
    legs = puts or calls
    if row.strategy in ("bull_put", "bear_put", "bull_call", "bear_call"):
        long = next(l for l in legs if l.side == "buy")
        short = next(l for l in legs if l.side == "sell")
        return {**base, "long_strike": long.strike, "short_strike": short.strike}
    return None


def parse(text: str) -> tuple[list[ScreenRow], list[str]]:
    """(rows, problems). Rows Barchart's trailer or a malformed line spoil
    are left out and named, not guessed at."""
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    try:
        header = [h.strip() for h in next(reader)]
    except StopIteration:
        return [], ["empty file"]
    col = {name: i for i, name in reversed(list(enumerate(header)))}  # first occurrence wins
    legs_at = [(i, int(m.group(1))) for i, h in enumerate(header) if (m := _LEG.match(h))]
    required = ("Symbol", "Exp Date")
    if any(r not in col for r in required) or not legs_at:
        return [], [f"not a spread screener export (columns: {', '.join(header[:8])}...)"]

    rows: list[ScreenRow] = []
    problems: list[str] = []
    for n, raw in enumerate(reader, start=2):
        if not raw or len(raw) < len(header):
            if raw and not raw[0].startswith("Downloaded"):
                problems.append(f"line {n}: {len(raw)} columns, expected {len(header)}")
            continue
        get = lambda name: raw[col[name]] if name in col else None  # noqa: E731
        try:
            expiry = date.fromisoformat(get("Exp Date").strip())
        except ValueError:
            problems.append(f"line {n}: expiry {get('Exp Date')!r}")
            continue
        legs: list[Leg] = []
        for i, _number_in_header in legs_at:
            strike = _number(raw[i])
            kind = (raw[i + 1] or "").strip().lower()
            price_head = header[i + 2].lower()
            if strike is None or kind not in ("put", "call"):
                legs = []
                break
            legs.append(Leg(strike, kind, "buy" if price_head.startswith("ask") else "sell", _number(raw[i + 2])))
        if not legs:
            problems.append(f"line {n}: a leg without strike or type")
            continue
        loss = _number(get("Loss Prob"))
        dte = _number(get("DTE"))
        row = ScreenRow(
            symbol=get("Symbol").strip().upper(),
            price=_number(get("Price~")) or _number(get("Price")) or _number(get("Latest")),
            expiry=expiry,
            dte=None if dte is None else int(dte),
            legs=legs,
            breakeven_up=_number(get("BE+")),
            breakeven_down=_number(get("BE-")),
            max_profit=_number(get("Max Profit")),
            max_loss=_number(get("Max Loss")),
            iv_rank=_number(get("IV Rank")),
            loss_prob=None if loss is None else loss / 100,
        )
        row.strategy = classify(legs)
        rows.append(row)
    return rows, problems


# --- set beside our own numbers ----------------------------------------------


def natural_net(row: ScreenRow, chain) -> float | None:
    """Per share, the structure crossed at today's quotes: bids for the legs
    sold, asks for the legs bought (as Barchart prices). None when a leg is
    not quoted on both sides."""
    total = 0.0
    for leg in row.legs:
        quote = chain.quote(leg.kind, leg.strike)
        if quote is None:
            return None
        price = quote.bid if leg.side == "sell" else quote.ask
        if price is None or price <= 0:
            return None
        total += price if leg.side == "sell" else -price
    return round(total, 4)

# Chains fetched per import at most, by default: one per (symbol, expiry)
# among the rows checked. A 4,787-row export is mostly the same few dozen
# chains, so rows far outnumber chains.
MAX_CHAINS = 40
# Chain fetches in flight at once: the source caches for five minutes and
# rate-limits beyond that, and a screen of chains is no reason to burst.
CHAIN_CONCURRENCY = 6


async def enrich(
    rows: list[ScreenRow],
    service,
    *,
    today: date,
    limit: int,
    max_chains: int = MAX_CHAINS,
    iv_store=None,
    earnings_calendar=None,
    clients=None,
) -> list[dict]:
    """The first `limit` rows as dicts, each with an "ours" block: the loss
    probability under the chain's smile and under the plain lognormal (what
    Barchart's figure assumes), our expected value, the earnings date and
    whether it falls inside the life of the structure, and our IV rank.

    The chains the rows need are fetched first -- the first `max_chains`
    distinct (symbol, expiry) in file order, CHAIN_CONCURRENCY at a time --
    and everything a chain decides (ATM IV, the smile's distribution, the
    rank) is worked out once per chain, not once per row. Rows whose chain
    is past the budget or failed carry a note instead."""
    import asyncio

    from app.options.distribution import Distribution
    from app.options.iv_context import forecast_vol
    from app.options.iv_history_store import COMPARABLE_DTE
    from app.options.screener import atm_iv_of, structure_outcome

    from app.options.chain_fetch import STRIKE_WIDTHS

    picked = rows[:limit]
    keys: list[tuple[str, date]] = []
    # How far from the money each chain has to reach: a condor's wings sit
    # well outside the default +/-10 % band (MU's 800/1300 around 1,075),
    # and a leg missing from the chain cannot be priced at today's quotes.
    reach: dict[tuple[str, date], float] = {}
    for row in picked:
        key = (row.symbol, row.expiry)
        if key not in keys and len(keys) < max_chains:
            keys.append(key)
        if row.price:
            far = max(abs(leg.strike / row.price - 1) for leg in row.legs)
            reach[key] = max(reach.get(key, 0.0), far)

    def width_for(key) -> float:
        need = reach.get(key, STRIKE_WIDTHS[-1]) + 0.01
        return next((w for w in STRIKE_WIDTHS if w >= need), STRIKE_WIDTHS[-1])

    gate = asyncio.Semaphore(CHAIN_CONCURRENCY)

    async def fetch(key):
        async with gate:
            try:
                return key, await service.chain(key[0], key[1], width_for(key))
            except Exception as exc:
                return key, exc

    chains = dict(await asyncio.gather(*(fetch(k) for k in keys)))

    # Per chain: (atm, years, distribution, rank).
    per_chain: dict[tuple[str, date], tuple] = {}
    for key, chain in chains.items():
        if isinstance(chain, Exception):
            continue
        years = max((key[1] - today).days, 0) / 365
        atm = atm_iv_of(chain.rows, chain.spot)
        dist = Distribution.from_chain(chain.rows, chain.spot, atm, years) if atm and years > 0 else None
        rank = None
        dte = (key[1] - today).days
        if iv_store is not None and atm and COMPARABLE_DTE[0] <= dte <= COMPARABLE_DTE[1]:
            try:
                found, _samples = await iv_store.rank(key[0], atm)
                rank = None if found is None else round(found.percent, 1)
            except Exception:
                rank = None
        per_chain[key] = (atm, years, dist, rank)

    # Closes per symbol for the realised-vol forecast behind "EV (RV)": a
    # year and a bit, one batched call. Without a market-data client the
    # column is simply absent.
    closes: dict[str, list[float]] = {}
    if clients is not None and per_chain:
        try:
            from app.market_data.bars import get_daily_bars_multi
            from app.playbooks.backtest import closes_by_session

            bars = await get_daily_bars_multi(clients, sorted({k[0] for k in per_chain}), lookback_days=400)
            closes = {s.upper(): [c for _, c in closes_by_session(rows)] for s, rows in bars.items()}
        except Exception:
            closes = {}

    earnings: dict[str, date | None] = {}
    if earnings_calendar is not None:
        for symbol in {k[0] for k in per_chain}:
            try:
                report = await earnings_calendar.next_earnings(symbol)
                earnings[symbol] = report.report_date if report else None
            except Exception:
                earnings[symbol] = None

    out = []
    for row in picked:
        item = row.to_dict()
        key = (row.symbol, row.expiry)
        if key not in chains:
            item["ours"] = {"note": f"not checked: {max_chains} chains per import"}
            out.append(item)
            continue
        chain = chains[key]
        if isinstance(chain, Exception):
            item["ours"] = {"note": f"chain unavailable: {chain}"}
            out.append(item)
            continue
        atm, years, dist, rank = per_chain[key]
        ours: dict = {"atm_iv": None if atm is None else round(atm, 4), "spot": chain.spot, "iv_rank": rank}
        # Priced at today's natural for the same strikes, not at the file's
        # prices: an export a few days old set against today's chain made a
        # MU condor worth +$394 on paper (3-day-old credit, today's odds).
        # Barchart's own net stays beside it.
        net_now = natural_net(row, chain)
        ours["net_now"] = net_now
        net = net_now if net_now is not None else row.net
        ours["priced_at"] = "today" if net_now is not None else "file"
        if atm and years > 0 and net is not None:
            legs = [{"kind": l.kind, "strike": l.strike, "side": l.side, "iv": atm} for l in row.legs]
            flat = structure_outcome(legs, net, chain.spot, atm, years, row.expiry)
            skewed = structure_outcome(legs, net, chain.spot, atm, years, row.expiry, dist=dist)
            if flat and skewed:
                ours.update(
                    loss_prob_flat=flat["loss_probability"],
                    loss_prob_skew=skewed["loss_probability"],
                    skew_fitted=bool(dist and dist.skewed),
                    expected_value=skewed["expected_value"],
                )
            # The same structure with the odds from the realised-vol forecast
            # (iv_context.forecast_vol, blended toward the year by DTE): what
            # it earns on average if the stock moves as its history says --
            # the yardstick for a screen that selects implied over realised.
            forecast = forecast_vol(closes.get(row.symbol, []), (row.expiry - today).days)
            if forecast is not None and forecast.forecast > 0:
                at_rv = structure_outcome(legs, net, chain.spot, forecast.forecast, years, row.expiry)
                if at_rv:
                    ours.update(expected_value_rv=at_rv["expected_value"], rv_forecast=round(forecast.forecast, 4))
        report = earnings.get(row.symbol)
        ours["earnings_date"] = report.isoformat() if report else None
        ours["earnings_inside"] = bool(report and report <= row.expiry)
        item["ours"] = ours
        out.append(item)
    return out
