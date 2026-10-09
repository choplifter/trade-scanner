"""0DTE put credit spreads on SPY, backtested on Alpaca's real option bars.

Each trading day: at a fixed entry time, sell the same-day put nearest a
target delta and buy the one `width` dollars below it; close early if the
spread's value reaches a stop multiple of the credit (or falls to a profit
target), otherwise let it settle on SPY's 16:00 close.

What the data can and cannot say, because it decides how far to trust the
result:

- Prices are Alpaca's option *trade* bars (one-minute OHLC of prints).
  Alpaca keeps no historical bid/ask for options, so a fill is the last
  trade at or before the minute, and the crossing cost is modelled as a
  fixed `slippage` per leg per side. SPY's same-day puts trade every few
  seconds near the money, so a recent print is close to the mid; far out
  of the money they print less often, which is why a price older than
  `stale_minutes` is not used.
- Delta at entry comes from the IV solved out of that print (Black-Scholes,
  time to the 16:00 close in calendar minutes). It picks the strike the way
  a trader looking at the chain would; it is not the broker's delta.
- The stop is checked on minute closes of both legs, each no older than
  `stale_minutes`. A gap through the stop inside a minute fills at that
  minute's value, not at the stop -- the real fill is often worse still.
- Settlement is cash at intrinsic on SPY's last regular-session close. SPY
  options are physically settled: a close between the strikes leaves an
  assignment (shares over the night) the backtest does not price. Those
  days are counted as `pinned` so the risk is visible.
- History starts where Alpaca's option bars do (early 2024).

Pure core (simulate_day, summarize) and an async fetcher (load_day) that
caches each day's bars on disk, so a second run with other parameters
fetches nothing.
"""

from __future__ import annotations

import asyncio
import logging
import math
import pickle
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from app.options.payoff import bs_greeks, implied_vol
from app.services.market_clock import ET, sessions_between

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / ".cache" / "zero_dte"
MINUTES_PER_YEAR = 365 * 24 * 60
# How far below the spot the fetched strikes reach: a 0.05-delta 0DTE put
# sits well inside 5 % even on a wild morning.
STRIKE_REACH = 0.05
# Alpaca's option bar history begins here.
FIRST_DAY = date(2024, 2, 1)


@dataclass(frozen=True)
class Params:
    underlying: str = "SPY"
    entry: time = time(10, 0)  # ET
    short_delta: float = 0.10
    width: float = 2.0  # dollars between the strikes
    stop_mult: float | None = 2.0  # close when the spread is worth this x the credit
    take_profit: float | None = None  # close when this share of the credit is earned
    slippage: float = 0.01  # per leg per side, per share
    fee: float = 0.03  # per contract per leg per side (regulatory and clearing)
    stale_minutes: int = 5
    min_credit: float = 0.05  # per share; below it the day is skipped


@dataclass
class Trade:
    day: str
    spot: float
    short_strike: float
    long_strike: float
    short_delta: float
    credit: float  # per share, after slippage
    exit_value: float  # per share, what was paid to close (0 when it expired worthless)
    exit: str  # "stop" | "target" | "expiry"
    exit_at: str
    close: float  # SPY at the exit
    pnl: float  # dollars per spread, after slippage and fees
    pinned: bool = False


@dataclass
class DayBars:
    """One day's minute closes: SPY, and each fetched put by strike. Keys
    are minutes since midnight ET."""

    spot: dict[int, float]
    puts: dict[float, dict[int, float]] = field(default_factory=dict)


def occ_symbol(underlying: str, expiry: date, strike: float, kind: str = "P") -> str:
    return f"{underlying}{expiry:%y%m%d}{kind}{int(round(strike * 1000)):08d}"


def _minute(t: time) -> int:
    return t.hour * 60 + t.minute


def _last(series: dict[int, float], minute: int, stale: int) -> float | None:
    """The latest close at or before `minute`, no older than `stale`."""
    for m in range(minute, minute - stale - 1, -1):
        if m in series:
            return series[m]
    return None


def simulate_day(day: date, bars: DayBars, p: Params, close_minute: int = 16 * 60) -> Trade | None:
    """One day's trade, or None when no spread fits (no prints, no strike
    near the delta, a credit under the minimum)."""
    entry = _minute(p.entry)
    spot = _last(bars.spot, entry, p.stale_minutes)
    if spot is None:
        return None
    years = (close_minute - entry) / MINUTES_PER_YEAR
    candidates = []
    for strike, series in bars.puts.items():
        if strike >= spot:
            continue
        px = _last(series, entry, p.stale_minutes)
        sigma = implied_vol("put", px, spot, strike, years)
        if sigma is None:
            continue
        delta, _, _ = bs_greeks("put", spot, strike, years, sigma)
        candidates.append((abs(abs(delta) - p.short_delta), strike, px, delta))
    if not candidates:
        return None
    _, short_k, short_px, short_delta = min(candidates)
    long_k = short_k - p.width
    long_px = _last(bars.puts.get(long_k, {}), entry, p.stale_minutes)
    if long_px is None:
        return None
    credit = short_px - long_px - 2 * p.slippage
    if credit < p.min_credit:
        return None

    short_s, long_s = bars.puts[short_k], bars.puts[long_k]
    exit_kind, exit_value, exit_minute = "expiry", None, close_minute
    for m in range(entry + 1, close_minute):
        a, b = _last(short_s, m, p.stale_minutes), _last(long_s, m, p.stale_minutes)
        if a is None or b is None or (m not in short_s and m not in long_s):
            continue
        value = max(0.0, a - b) + 2 * p.slippage  # what closing costs
        if p.stop_mult is not None and value >= p.stop_mult * credit:
            exit_kind, exit_value, exit_minute = "stop", value, m
            break
        if p.take_profit is not None and value <= (1 - p.take_profit) * credit:
            exit_kind, exit_value, exit_minute = "target", value, m
            break

    last_spot = max((m for m in bars.spot if m <= close_minute), default=None)
    settle = bars.spot[last_spot] if last_spot is not None else spot
    close_at = _last(bars.spot, exit_minute, 60) or settle
    pinned = False
    if exit_value is None:
        exit_value = max(0.0, short_k - settle) - max(0.0, long_k - settle)
        pinned = long_k < settle < short_k
    legs_closed = 2 if exit_kind != "expiry" else 0
    pnl = (credit - exit_value) * 100 - p.fee * (2 + legs_closed)
    return Trade(
        day=day.isoformat(),
        spot=round(spot, 2),
        short_strike=short_k,
        long_strike=long_k,
        short_delta=round(short_delta, 3),
        credit=round(credit, 4),
        exit_value=round(exit_value, 4),
        exit=exit_kind,
        exit_at=f"{exit_minute // 60:02d}:{exit_minute % 60:02d}",
        close=round(close_at, 2),
        pnl=round(pnl, 2),
        pinned=pinned,
    )


def summarize(trades: list[Trade], days_tried: int) -> dict:
    """Win rate, the sizes of wins and losses, the drawdown and the years --
    the numbers that say whether a high win rate pays for its losses."""
    if not trades:
        return {"trades": 0, "days": days_tried}
    pnls = [t.pnl for t in trades]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x <= 0]
    equity, peak, drawdown, curve = 0.0, 0.0, 0.0, []
    for t in trades:
        equity += t.pnl
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
        curve.append({"day": t.day, "equity": round(equity, 2)})
    years: dict[str, dict] = {}
    for t in trades:
        y = years.setdefault(t.day[:4], {"trades": 0, "pnl": 0.0, "wins": 0, "worst": 0.0})
        y["trades"] += 1
        y["pnl"] = round(y["pnl"] + t.pnl, 2)
        y["wins"] += t.pnl > 0
        y["worst"] = round(min(y["worst"], t.pnl), 2)
    worst = sorted(trades, key=lambda t: t.pnl)[:10]
    return {
        "days": days_tried,
        "trades": len(trades),
        "win_rate": round(len(wins) / len(trades), 4),
        "total": round(sum(pnls), 2),
        "avg": round(sum(pnls) / len(trades), 2),
        "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
        "profit_factor": round(sum(wins) / -sum(losses), 2) if losses and sum(losses) < 0 else None,
        "max_drawdown": round(drawdown, 2),
        "avg_credit": round(sum(t.credit for t in trades) / len(trades) * 100, 2),
        "stops": sum(t.exit == "stop" for t in trades),
        "targets": sum(t.exit == "target" for t in trades),
        "pinned": sum(t.pinned for t in trades),
        "by_year": years,
        "worst": [asdict(t) for t in worst],
        "equity": curve,
    }


def _minute_closes(rows) -> dict[int, float]:
    out = {}
    for bar in rows:
        ts = bar.timestamp.astimezone(ET)
        out[ts.hour * 60 + ts.minute] = float(bar.close)
    return out


async def load_day(
    clients, day: date, hours: tuple[datetime, datetime], underlying: str = "SPY", cache_dir: Path = CACHE_DIR
) -> DayBars | None:
    """SPY's regular-session minute closes and the same-day puts from the
    open's spot down STRIKE_REACH, from disk when fetched before. None on
    a day with no SPY bars (or no 0DTE listed)."""
    path = cache_dir / underlying / f"{day.isoformat()}.pkl"
    if path.exists():
        try:
            return pickle.loads(path.read_bytes())
        except Exception:
            logger.warning("Unreadable 0DTE cache %s; fetching again", path)
    from alpaca.data.requests import OptionBarsRequest, StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    start, end = hours[0].astimezone(timezone.utc), hours[1].astimezone(timezone.utc) + timedelta(minutes=1)
    stock = await asyncio.to_thread(
        clients.data.get_stock_bars,
        StockBarsRequest(symbol_or_symbols=underlying, timeframe=TimeFrame.Minute, start=start, end=end, feed=clients.feed),
    )
    spot = _minute_closes(stock.data.get(underlying, []))
    if not spot:
        return None
    first = spot[min(spot)]
    strikes = [float(k) for k in range(math.floor(first * (1 - STRIKE_REACH)), math.ceil(first) + 1)]
    symbols = {occ_symbol(underlying, day, k): k for k in strikes}
    puts: dict[float, dict[int, float]] = {}
    names = list(symbols)
    for i in range(0, len(names), 100):
        chunk = names[i : i + 100]
        result = await asyncio.to_thread(
            clients.options.get_option_bars,
            OptionBarsRequest(symbol_or_symbols=chunk, timeframe=TimeFrame.Minute, start=start, end=end),
        )
        for name, rows in result.data.items():
            closes = _minute_closes(rows)
            if closes:
                puts[symbols[name]] = closes
    bars = DayBars(spot=spot, puts=puts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pickle.dumps(bars))
    return bars


async def run(clients, start: date, end: date, p: Params, concurrency: int = 4) -> dict:
    """The backtest over [start, end]: each day loaded (cached), simulated,
    and the trades summarized. Days without data are counted, not hidden."""
    sessions = await asyncio.to_thread(sessions_between, max(start, FIRST_DAY), end)
    days = sorted(sessions)
    gate = asyncio.Semaphore(concurrency)
    missing: list[str] = []

    async def one(d: date) -> Trade | None:
        async with gate:
            try:
                bars = await load_day(clients, d, sessions[d], p.underlying)
            except Exception:
                logger.exception("0DTE bars failed for %s", d)
                bars = None
        if bars is None or not bars.puts:
            missing.append(d.isoformat())
            return None
        close = sessions[d][1]
        return simulate_day(d, bars, p, close.hour * 60 + close.minute)

    results = await asyncio.gather(*(one(d) for d in days))
    trades = [t for t in results if t is not None]
    trades.sort(key=lambda t: t.day)
    out = summarize(trades, len(days))
    out["no_data"] = len(missing)
    out["no_data_days"] = sorted(missing)[:20]
    out["params"] = {**asdict(p), "entry": p.entry.strftime("%H:%M")}
    out["generated_at"] = datetime.now(timezone.utc).isoformat()
    return out
