import asyncio
import json
import logging
from datetime import datetime

import anthropic
from pydantic import BaseModel, Field

from app.alpaca.client import AlpacaClients
from app.market_data.bars import get_daily_bars_multi, get_intraday_minute_bars_multi
from app.market_data.momentum import MOMENTUM_WINDOW, pct_change_over_window
from app.market_data.news import fetch_headlines
from app.market_data.vwap import SessionVwapState
from app.scanners.schemas import ScannerRow
from app.services.market_clock import ET

logger = logging.getLogger(__name__)

_MODEL = "claude-opus-5"
_CANDIDATE_POOL = 15
_TOP_N = 3
_CONTINUATION_LOOKBACK_DAYS = 20
_CONTINUATION_WINDOW = 5
_ATR_WINDOW = 10

# Framed as descriptive scanner annotation, not investment advice -- this is
# a personal dashboard, not a licensed advisory product. The prohibitions
# here are load-bearing, not decorative: no price predictions, no buy/sell
# directives, nothing that reads as a recommendation. "Select the top 3" is
# a ranking by quantifiable scanner criteria (gap %, relative volume, dollar
# volume, high-of-day, news catalyst, VWAP position, float, short interest),
# not a judgment about which stock will perform best.
_SYSTEM_PROMPT = """You annotate a day-trading scanner dashboard. You are given a candidate list of stocks currently showing unusual price/volume activity, as JSON. Each candidate may include a recent news headline, its position relative to today's session VWAP (volume-weighted average price), its price change over the last 15 minutes, its current bid-ask spread as a % of price, multi-day context (consecutive up days and % change over the prior 5 trading days, both excluding today), its average daily trading range over roughly the last 10 sessions as a % of price, its public float (shares available for trading), market capitalization, and short interest as a % of float. Select the 3 that stand out most for a trader's watchlist, weighing: the size of gap %, relative volume, dollar volume (liquidity), high-of-day status, whether there's a clear news catalyst behind the move, whether price is holding above VWAP (trend intact) rather than fading below it, whether the move is still active in the last 15 minutes rather than having already stalled or reversed, how tight or wide the bid-ask spread is, whether today's move is a fresh breakout versus a continuation of an already-extended multi-day run, whether today's gap is unusual even relative to that specific stock's own typical daily range (a 40% gap on a name that normally moves 2%/day is a very different thing from the same gap on a name that swings double digits routinely), how small the float is relative to a large-cap/large-float name (a low-float stock tends to move more per dollar of volume, which is why the same dollar volume means something different on a 5M-share float than a 500M-share float), and how elevated the short interest is (a high % of float sold short raises the odds that continued buying pressure forces short covering, which can itself accelerate a move). You are not a financial advisor and this is not investment advice.

A stock with a visible catalyst (an actual headline, not just "a big move"), price holding above VWAP, momentum that's still positive in the last 15 minutes, and a tight spread is more noteworthy than one with similar all-day numbers that has since gone flat, reversed, or trades with a wide spread that would make it an awkward, expensive fill. A wide spread is a real quality concern worth naming, not just a footnote. Multi-day context, average daily range, float, market cap, and short interest are neutral, descriptive information, not a "good" or "bad" label -- a stock already up several days running and one gapping fresh off a flat base are both worth naming factually, without implying one is a better setup than the other; a stock that's normally volatile isn't "worse" than a normally calm one, just different context for interpreting the same gap %; and a small float or high short interest is a factual characteristic of the setup (more prone to fast, volatile moves either direction), not a signal that the stock is a better or safer pick than a large-float, low-short-interest one. Only cite any of this when the data actually shows it. Missing headline, VWAP, 15-minute-change, spread, multi-day, range, float, market cap, or short interest data just means that data isn't available for this candidate right now, not that anything is wrong with it -- never treat absence of data as a negative signal.

Ranking is purely about which of these signals are most extreme/notable in this candidate set, not a prediction of which stock will go up. Do not predict future price movement, state a price target, or use directive language like "buy", "sell", "should", "will", "a good entry". If the data doesn't clearly support an observation, keep the reason generic and purely descriptive.

For each of the 3 selected symbols, write:
- headline: 4-8 words summarizing the setup (e.g. "Large premarket gap on earnings beat")
- reason: one or two sentences citing the specific numbers (and catalyst/VWAP/float/short-interest context when present) that got it selected over the other candidates, in plain language
- signal_score: an integer 1-10 for how many of the available data points (gap size, relative volume, dollar volume, HOD, catalyst, VWAP position, 15-minute momentum, spread tightness, multi-day continuation, range-relative-to-normal, float size, short interest) corroborate each other for this specific pick, and how strong they are, relative to the other candidates in this set. This is a measure of data confluence, not a probability of profit, a confidence level in the trade working out, or a recommendation strength -- a candidate can score high because many signals are present and pointing the same way, and low because signals are sparse, missing, or conflicting (e.g. a big gap but a wide spread and no catalyst).

Also write one overall disclaimer sentence reminding the reader this is an automated ranking of scanner data, not trading advice, and they should do their own research before acting on anything."""


class TradeIdea(BaseModel):
    symbol: str
    headline: str
    reason: str
    signal_score: int = Field(ge=1, le=10)


class TradeIdeasResponse(BaseModel):
    ideas: list[TradeIdea]
    disclaimer: str


def _row_to_payload(
    row: ScannerRow, headline: str | None, intraday_ctx: dict | None, continuation_ctx: dict | None
) -> dict:
    ctx = intraday_ctx or {}
    cont = continuation_ctx or {}
    return {
        "symbol": row.symbol,
        "last_price": round(row.last_price, 2),
        "pct_change_from_prior_close": round(row.pct_change, 2),
        "relative_volume_vs_20d_avg": round(row.rvol, 2),
        "volume_today": int(row.volume_today),
        "dollar_volume_today": int(row.dollar_volume_today),
        "at_high_of_day": row.is_hod,
        "at_low_of_day": row.is_lod,
        "recent_headline": headline,
        "vwap_price": ctx.get("vwap_price"),
        "pct_from_vwap": ctx.get("pct_from_vwap"),
        "pct_change_last_15m": ctx.get("pct_change_last_15m"),
        "bid_ask_spread_pct": round(row.spread_pct, 3) if row.spread_pct is not None else None,
        "consecutive_up_days": cont.get("consecutive_up_days"),
        "pct_change_prior_5d": cont.get("pct_change_prior_5d"),
        "avg_daily_range_pct": cont.get("avg_daily_range_pct"),
        "float_shares": int(row.float_shares) if row.float_shares is not None else None,
        "market_cap": int(row.market_cap) if row.market_cap is not None else None,
        "short_interest_pct_of_float": (
            round(row.short_interest_pct, 2) if row.short_interest_pct is not None else None
        ),
    }


async def _fetch_intraday_context(
    alpaca: AlpacaClients, symbols: list[str], last_prices: dict[str, float]
) -> dict[str, dict]:
    """Today's session VWAP and ~15-minute momentum per candidate, both
    derived from the same batch of today's minute bars (one HTTP call, not
    two). Best-effort for the same reason as `fetch_headlines`. VWAP is
    None before the regular session opens (see SessionVwapState), and
    15-minute momentum is None when there isn't yet 15 minutes of bars to
    compare against (e.g. just after premarket open) -- both are normal,
    not failures.
    """
    if not symbols:
        return {}
    try:
        bars_by_symbol = await get_intraday_minute_bars_multi(alpaca, symbols)
    except Exception:
        logger.exception("Intraday context fetch failed for trade ideas")
        return {}

    context: dict[str, dict] = {}
    for symbol, bars in bars_by_symbol.items():
        if not bars:
            continue
        entry: dict = {}

        vwap = SessionVwapState(symbol=symbol).seed_from_bars(bars)
        last = last_prices.get(symbol)
        if vwap is not None and last is not None:
            entry["vwap_price"] = round(vwap, 2)
            entry["pct_from_vwap"] = round((last - vwap) / vwap * 100, 2)

        momentum = pct_change_over_window(bars, MOMENTUM_WINDOW)
        if momentum is not None:
            entry["pct_change_last_15m"] = momentum

        if entry:
            context[symbol] = entry
    return context


def _avg_daily_range_pct(completed_bars: list) -> float | None:
    """Average True Range over up to the last `_ATR_WINDOW` completed daily
    bars, expressed as a % of the average close in that window -- lets the
    model judge whether today's gap is unusual for *this* stock specifically
    (some names routinely swing double digits) rather than just a big number
    in isolation.
    """
    window = completed_bars[-(_ATR_WINDOW + 1) :]
    if len(window) < 2:
        return None
    true_ranges = []
    closes = []
    for i in range(1, len(window)):
        high = window[i].high
        low = window[i].low
        prev_close = window[i - 1].close
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        closes.append(window[i].close)
    avg_close = sum(closes) / len(closes)
    if avg_close <= 0:
        return None
    return round(sum(true_ranges) / len(true_ranges) / avg_close * 100, 2)


async def _fetch_continuation_context(alpaca: AlpacaClients, symbols: list[str]) -> dict[str, dict]:
    """Multi-day price context per candidate -- distinguishes a fresh mover
    from one that's already run for days, and reports this stock's own
    typical daily range so a big gap % can be judged against what's normal
    *for that name* rather than in isolation. Best-effort like the other
    optional context. Deliberately excludes today's still-forming daily bar:
    today's move is already conveyed via pct_change_from_prior_close, so
    this signal is only about what happened *before* today.
    """
    if not symbols:
        return {}
    try:
        bars_by_symbol = await get_daily_bars_multi(
            alpaca, symbols, lookback_days=_CONTINUATION_LOOKBACK_DAYS
        )
    except Exception:
        logger.exception("Continuation context fetch failed for trade ideas")
        return {}

    today_et = datetime.now(ET).date()
    context: dict[str, dict] = {}
    for symbol, bars in bars_by_symbol.items():
        completed = [b for b in bars if b.timestamp.astimezone(ET).date() < today_et]
        if len(completed) < 2:
            continue

        consecutive_up = 0
        for i in range(len(completed) - 1, 0, -1):
            if completed[i].close > completed[i - 1].close:
                consecutive_up += 1
            else:
                break

        window = completed[-_CONTINUATION_WINDOW:]
        entry: dict = {"consecutive_up_days": consecutive_up}
        if len(window) >= 2 and window[0].close:
            entry["pct_change_prior_5d"] = round(
                (window[-1].close - window[0].close) / window[0].close * 100, 2
            )

        range_pct = _avg_daily_range_pct(completed)
        if range_pct is not None:
            entry["avg_daily_range_pct"] = range_pct

        context[symbol] = entry
    return context


async def generate_trade_ideas(
    client: anthropic.AsyncAnthropic, alpaca: AlpacaClients, rows: list[ScannerRow]
) -> TradeIdeasResponse:
    candidates = rows[:_CANDIDATE_POOL]
    symbols = [r.symbol for r in candidates]
    last_prices = {r.symbol: r.last_price for r in candidates}

    headlines, intraday_context, continuation_context = await asyncio.gather(
        fetch_headlines(alpaca, symbols),
        _fetch_intraday_context(alpaca, symbols, last_prices),
        _fetch_continuation_context(alpaca, symbols),
    )

    payload = [
        _row_to_payload(
            r,
            headlines.get(r.symbol),
            intraday_context.get(r.symbol),
            continuation_context.get(r.symbol),
        )
        for r in candidates
    ]

    response = await client.messages.parse(
        model=_MODEL,
        max_tokens=2000,
        system=_SYSTEM_PROMPT,
        output_format=TradeIdeasResponse,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Here are today's top {len(candidates)} movers from a stock scanner, "
                    f"as JSON. Select the {_TOP_N} that stand out most and write one entry "
                    "each, ranked best first.\n\n" + json.dumps(payload)
                ),
            }
        ],
    )
    return response.parsed_output
