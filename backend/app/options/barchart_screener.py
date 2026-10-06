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

# Chains fetched per import at most: one per (symbol, expiry) among the rows
# enriched. A 4,787-row export is mostly the same few dozen chains.
MAX_CHAINS = 20


async def enrich(rows: list[ScreenRow], service, *, today: date, limit: int, iv_store=None, earnings_calendar=None) -> list[dict]:
    """The first `limit` rows as dicts, each with an "ours" block: the loss
    probability under the chain's smile and under the plain lognormal (what
    Barchart's figure assumes), our expected value, the earnings date and
    whether it falls inside the life of the structure, and our IV rank.
    Rows past the chain budget, or whose chain fails, carry a note instead."""
    from app.options.distribution import Distribution
    from app.options.iv_history_store import COMPARABLE_DTE
    from app.options.screener import atm_iv_of, structure_outcome

    chains: dict[tuple[str, date], object] = {}
    earnings: dict[str, date | None] = {}
    out = []
    for row in rows[:limit]:
        item = row.to_dict()
        key = (row.symbol, row.expiry)
        if key not in chains:
            if len(chains) >= MAX_CHAINS:
                item["ours"] = {"note": f"not checked: {MAX_CHAINS} chains per import"}
                out.append(item)
                continue
            try:
                chains[key] = await service.chain(row.symbol, row.expiry)
            except Exception as exc:
                chains[key] = exc
        chain = chains[key]
        if isinstance(chain, Exception):
            item["ours"] = {"note": f"chain unavailable: {chain}"}
            out.append(item)
            continue
        years = max((row.expiry - today).days, 0) / 365
        atm = atm_iv_of(chain.rows, chain.spot)
        ours: dict = {"atm_iv": None if atm is None else round(atm, 4), "spot": chain.spot}
        if atm and years > 0 and row.net is not None:
            legs = [{"kind": l.kind, "strike": l.strike, "side": l.side, "iv": atm} for l in row.legs]
            flat = structure_outcome(legs, row.net, chain.spot, atm, years, row.expiry)
            dist = Distribution.from_chain(chain.rows, chain.spot, atm, years)
            skewed = structure_outcome(legs, row.net, chain.spot, atm, years, row.expiry, dist=dist)
            if flat and skewed:
                ours.update(
                    loss_prob_flat=flat["loss_probability"],
                    loss_prob_skew=skewed["loss_probability"],
                    skew_fitted=dist.skewed,
                    expected_value=skewed["expected_value"],
                )
        if earnings_calendar is not None and row.symbol not in earnings:
            try:
                next_report = await earnings_calendar.next_earnings(row.symbol)
                earnings[row.symbol] = next_report.report_date if next_report else None
            except Exception:
                earnings[row.symbol] = None
        report = earnings.get(row.symbol)
        ours["earnings_date"] = report.isoformat() if report else None
        ours["earnings_inside"] = bool(report and report <= row.expiry)
        if iv_store is not None and atm and row.dte is not None and COMPARABLE_DTE[0] <= row.dte <= COMPARABLE_DTE[1]:
            try:
                rank, _samples = await iv_store.rank(row.symbol, atm)
                ours["iv_rank"] = None if rank is None else round(rank.percent, 1)
            except Exception:
                pass
        item["ours"] = ours
        out.append(item)
    return out
