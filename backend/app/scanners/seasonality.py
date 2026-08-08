"""Day-of-week / OpEx-week historical stats for a single symbol.

These are exploratory descriptive stats, not signals to trade on -- weekday
buckets over a couple of years land around ~100 samples each, so every
bucket always carries its sample size (`n`) rather than hiding it, and a
bucket with no observations reports None stats instead of a fabricated 0.0.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from app.alpaca.client import AlpacaClients
from app.market_data.bars import get_daily_bars_multi
from app.services.market_clock import ET, day_of_week, is_opex_week

_WEEKDAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
_OPEX_ORDER = ["OpEx Week", "Non-OpEx Week"]


@dataclass
class SeasonalityBucket:
    label: str
    mean_return_pct: float | None
    median_return_pct: float | None
    win_rate_pct: float | None
    n: int


@dataclass
class SeasonalityResult:
    symbol: str
    lookback_days: int
    by_weekday: list[SeasonalityBucket]
    by_opex: list[SeasonalityBucket]


def _empty_buckets(labels: list[str]) -> list[SeasonalityBucket]:
    return [
        SeasonalityBucket(label=label, mean_return_pct=None, median_return_pct=None, win_rate_pct=None, n=0)
        for label in labels
    ]


def _empty_result(symbol: str, lookback_days: int) -> SeasonalityResult:
    return SeasonalityResult(
        symbol=symbol,
        lookback_days=lookback_days,
        by_weekday=_empty_buckets(_WEEKDAY_ORDER),
        by_opex=_empty_buckets(_OPEX_ORDER),
    )


def _bucket_stats(returns: pd.Series) -> tuple[float | None, float | None, float | None, int]:
    n = int(returns.count())
    if n == 0:
        return None, None, None, 0
    mean = float(returns.mean())
    median = float(returns.median())
    win_rate = float((returns > 0).mean() * 100.0)
    return mean, median, win_rate, n


def _buckets_for(df: pd.DataFrame, group_col: str, labels: list[str]) -> list[SeasonalityBucket]:
    buckets = []
    for label in labels:
        mean, median, win_rate, n = _bucket_stats(df.loc[df[group_col] == label, "return_pct"])
        buckets.append(
            SeasonalityBucket(label=label, mean_return_pct=mean, median_return_pct=median, win_rate_pct=win_rate, n=n)
        )
    return buckets


async def compute_seasonality(
    clients: AlpacaClients, symbol: str, lookback_days: int = 730
) -> SeasonalityResult:
    symbol = symbol.upper()
    bars_by_symbol = await get_daily_bars_multi(clients, [symbol], lookback_days=lookback_days)
    bars = bars_by_symbol.get(symbol, [])
    if len(bars) < 2:
        return _empty_result(symbol, lookback_days)

    df = pd.DataFrame(
        {
            "date": [b.timestamp.astimezone(ET).date() for b in bars],
            "close": [b.close for b in bars],
        }
    ).sort_values("date")
    df["return_pct"] = df["close"].pct_change() * 100.0
    df = df.dropna(subset=["return_pct"])

    df["weekday"] = df["date"].apply(day_of_week)
    df["opex"] = df["date"].apply(lambda d: "OpEx Week" if is_opex_week(d) else "Non-OpEx Week")

    return SeasonalityResult(
        symbol=symbol,
        lookback_days=lookback_days,
        by_weekday=_buckets_for(df, "weekday", _WEEKDAY_ORDER),
        by_opex=_buckets_for(df, "opex", _OPEX_ORDER),
    )
