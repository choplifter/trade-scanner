"""Disk cache for historical bar fetches used by backtest CLI tools.

Nothing in the live app persists bar data at all (see app.market_data.bars'
module docstring) -- every live fetch is used once and discarded. That's
fine for a single poll tick, but a multi-week minute-bar pull (see
get_minute_bars_multi) can take real wall-clock time and return millions
of bars, and a researcher iterating on a backtest's parameters (threshold,
horizon, wick tolerance) would otherwise re-pay that fetch cost on every
single run. This is scripts-only tooling -- the running FastAPI app never
imports this module.

Cached by (sorted symbols, lookback_days) rather than an exact date range,
since that's what callers actually vary between runs; a fetch's own
"as of" timestamp is stored alongside so a stale cache can be told apart
from a fresh one. No automatic incremental refresh (e.g. only re-fetching
today's still-forming bars) -- explicit `force_refresh` or letting
`max_age_hours` expire is simpler and safer than a partial-cache-merge
mechanism this module would then have to get right.
"""

import hashlib
import logging
import pickle
import time
from pathlib import Path

from app.alpaca.client import AlpacaClients
from app.market_data.bars import get_minute_bars_multi

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / ".cache" / "bars"


def _cache_path(cache_dir: Path, symbols: list[str], lookback_days: int) -> Path:
    key_material = f"minute:{lookback_days}:{','.join(sorted(symbols))}"
    digest = hashlib.sha256(key_material.encode()).hexdigest()[:20]
    return cache_dir / f"{digest}.pkl"


async def get_cached_minute_bars_multi(
    clients: AlpacaClients,
    symbols: list[str],
    lookback_days: int,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    force_refresh: bool = False,
    max_age_hours: float = 12.0,
) -> dict[str, list]:
    """get_minute_bars_multi, but reusing a prior fetch from disk when one
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
                    "Using cached minute bars from %s (%.1fh old, %d symbols): %s",
                    path,
                    age_hours,
                    len(cached["bars_by_symbol"]),
                    path.name,
                )
                return cached["bars_by_symbol"]
            logger.info("Cached minute bars at %s are %.1fh old (>%.1fh) -- refetching", path, age_hours, max_age_hours)
        except Exception:
            logger.exception("Failed reading cached bars at %s -- refetching", path)

    bars_by_symbol = await get_minute_bars_multi(clients, symbols, lookback_days=lookback_days)

    try:
        with path.open("wb") as f:
            pickle.dump({"fetched_at": time.time(), "bars_by_symbol": bars_by_symbol}, f)
    except Exception:
        logger.exception("Failed writing bar cache to %s -- continuing without it", path)

    return bars_by_symbol
