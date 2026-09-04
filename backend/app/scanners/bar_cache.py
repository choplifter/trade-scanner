"""Disk cache for historical bar fetches used by backtest CLI tools.

Nothing in the live app persists bar data at all (see app.market_data.bars'
module docstring) -- every live fetch is used once and discarded. That's
fine for a single poll tick, but a multi-week 5-minute-bar pull (see
get_5m_bars_multi) can take real wall-clock time, and a researcher
iterating on a backtest's parameters (threshold, horizon, wick tolerance)
would otherwise re-pay that fetch cost on every single run. This is
scripts-only tooling -- the running FastAPI app never imports this module.

Cached by (sorted symbols, lookback_days) rather than an exact date range,
since that's what callers actually vary between runs; a fetch's own
"as of" timestamp is stored alongside so a stale cache can be told apart
from a fresh one. No automatic incremental refresh (e.g. only re-fetching
today's still-forming bars) -- explicit `force_refresh` or letting
`max_age_hours` expire is simpler and safer than a partial-cache-merge
mechanism this module would then have to get right.
"""

import asyncio
import hashlib
import logging
import pickle
import time
from datetime import datetime, timezone
from pathlib import Path

from alpaca.data.requests import OptionBarsRequest
from alpaca.data.timeframe import TimeFrame

from app.alpaca.client import AlpacaClients
from app.market_data.bars import get_5m_bars_multi

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / ".cache" / "bars"


def _cache_path(cache_dir: Path, symbols: list[str], lookback_days: int) -> Path:
    key_material = f"5m:{lookback_days}:{','.join(sorted(symbols))}"
    digest = hashlib.sha256(key_material.encode()).hexdigest()[:20]
    return cache_dir / f"{digest}.pkl"


async def get_cached_5m_bars_multi(
    clients: AlpacaClients,
    symbols: list[str],
    lookback_days: int,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    force_refresh: bool = False,
    max_age_hours: float = 12.0,
) -> dict[str, list]:
    """get_5m_bars_multi, but reusing a prior fetch from disk when one
    exists for the same (symbols, lookback_days) and is younger than
    max_age_hours. Cache hit/miss is only reported via logging, not the
    return value -- callers so far only need the bars themselves.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(cache_dir, symbols, lookback_days)

    if not force_refresh and path.exists():
        try:
            with path.open("rb") as f:
                cached = pickle.load(f)
            age_hours = (time.time() - cached["fetched_at"]) / 3600
            if age_hours < max_age_hours:
                logger.info(
                    "Using cached 5m bars from %s (%.1fh old, %d symbols): %s",
                    path,
                    age_hours,
                    len(cached["bars_by_symbol"]),
                    path.name,
                )
                return cached["bars_by_symbol"]
            logger.info("Cached 5m bars at %s are %.1fh old (>%.1fh) -- refetching", path, age_hours, max_age_hours)
        except Exception:
            logger.exception("Failed reading cached bars at %s -- refetching", path)

    bars_by_symbol = await get_5m_bars_multi(clients, symbols, lookback_days=lookback_days)

    try:
        with path.open("wb") as f:
            pickle.dump({"fetched_at": time.time(), "bars_by_symbol": bars_by_symbol}, f)
    except Exception:
        logger.exception("Failed writing bar cache to %s -- continuing without it", path)

    return bars_by_symbol


# Alpaca accepts many symbols per option-bars request; chunking keeps one
# request's URL and response bounded for a wide chain.
_OPTION_BARS_CHUNK = 100


def _option_cache_path(cache_dir: Path, symbols: list[str], start: datetime, end: datetime) -> Path:
    key_material = f"opt1m:{start.isoformat()}:{end.isoformat()}:{','.join(sorted(symbols))}"
    digest = hashlib.sha256(key_material.encode()).hexdigest()[:20]
    return cache_dir / f"{digest}.pkl"


async def get_cached_option_minute_bars(
    clients: AlpacaClients,
    symbols: list[str],
    start: datetime,
    end: datetime,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    force_refresh: bool = False,
) -> dict[str, list]:
    """1-minute bars of option contracts over [start, end], symbol -> bars
    -- what a history replay prices a chain from (see
    app.replay.options_engine). Cached on disk by (symbols, window) with
    no expiry once the window lies in the past: a finished session's bars
    never change, so a second replay of the same day costs no fetch.
    Contracts with no prints in the window are simply absent from the
    result."""
    if not symbols:
        return {}
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _option_cache_path(cache_dir, symbols, start, end)
    if not force_refresh and path.exists():
        try:
            with path.open("rb") as f:
                cached = pickle.load(f)
            logger.info("Using cached option minute bars from %s (%d symbols)", path.name, len(cached["bars_by_symbol"]))
            return cached["bars_by_symbol"]
        except Exception:
            logger.exception("Failed reading cached option bars at %s -- refetching", path)

    bars_by_symbol: dict[str, list] = {}
    for i in range(0, len(symbols), _OPTION_BARS_CHUNK):
        chunk = symbols[i : i + _OPTION_BARS_CHUNK]
        request = OptionBarsRequest(symbol_or_symbols=chunk, timeframe=TimeFrame.Minute, start=start, end=end)
        bar_set = await asyncio.to_thread(clients.options.get_option_bars, request)
        for symbol, bars in (bar_set.data or {}).items():
            if bars:
                bars_by_symbol[symbol] = list(bars)

    if end < datetime.now(timezone.utc):
        try:
            with path.open("wb") as f:
                pickle.dump({"fetched_at": time.time(), "bars_by_symbol": bars_by_symbol}, f)
        except Exception:
            logger.exception("Failed writing option bar cache to %s -- continuing without it", path)
    return bars_by_symbol
