"""Historical news, keyed to the trading day it could have been acted on.

This is the piece every catalyst claim in this app has been missing. The
1.15x boost in formulas._CATALYST_BOOST rests on +9.1pp measured over three
trading days of live scanner_history; the daily backtest has always passed
has_headline=False and said so, because Alpaca's news API is a live feed and
nothing here fetched dated history. FMP's /stable/news/stock accepts from/to,
so a real historical catalyst flag is now buildable.

The mapping from "published at T" to "actionable on day D" is the whole
design, and it mirrors the live rule exactly (fmp_news.recent_news_cutoff): a
session counts as catalyst-backed if a qualifying story landed between 18
hours before its open and its close. That window deliberately includes the
prior evening -- an after-close earnings release is the classic overnight
gap driver, and attributing it to the session that already ended would be
backwards.

Anything the live filter rejects is rejected here too, for the same reason it
matters more in a backtest than live: 30% of raw FMP items are litigation
notices published *after* a collapse, so counting them would manufacture a
correlation between "had news" and "already fell" and then report it as a
catalyst effect.
"""

import hashlib
import logging
import pickle
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

from app.market_data.fmp_news import is_low_signal_headline, parse_published
from app.services.market_clock import ET, trading_hours_for

logger = logging.getLogger(__name__)

_URL = "https://financialmodelingprep.com/stable/news/stock"
_LIMIT = 1000
# Same buffer the live cutoff uses -- see recent_news_cutoff.
_PRE_SESSION_BUFFER = timedelta(hours=18)
DEFAULT_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / ".cache" / "news"
# One symbol per request. The batched endpoint shares `limit` across symbols
# and a single prolific name swallows it (CAPR took 212 of 250 in a
# five-symbol request), which for a *historical* pull would silently leave
# whole symbols with no catalyst history at all -- indistinguishable from
# "had no news", and quietly biasing the very thing being measured.
_CONCURRENCY = 4


def _cache_path(cache_dir: Path, symbol: str, start: date, end: date) -> Path:
    digest = hashlib.sha256(f"{symbol}:{start}:{end}".encode()).hexdigest()[:20]
    return cache_dir / f"{digest}.pkl"


async def fetch_symbol_news(
    client: httpx.AsyncClient,
    api_key: str,
    symbol: str,
    start: date,
    end: date,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    max_age_hours: float = 24.0,
) -> list[dict]:
    """Raw dated items for one symbol over a window, disk-cached.

    Cached because a catalyst backtest over a few hundred symbols is a few
    hundred requests, and the answer for a *past* window doesn't change --
    re-running the same measurement shouldn't re-spend the quota.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(cache_dir, symbol, start, end)
    if path.exists():
        try:
            with path.open("rb") as f:
                cached = pickle.load(f)
            if (time.time() - cached["fetched_at"]) / 3600 < max_age_hours:
                return cached["items"]
        except Exception:
            pass  # Corrupt entry -- refetch rather than fail.

    try:
        response = await client.get(
            _URL,
            params={
                "symbols": symbol,
                "from": start.isoformat(),
                "to": end.isoformat(),
                "limit": _LIMIT,
                "apikey": api_key,
            },
        )
        response.raise_for_status()
        items = response.json()
    except Exception:
        logger.exception("Historical news fetch failed for %s", symbol)
        return []

    if not isinstance(items, list):
        return []
    try:
        with path.open("wb") as f:
            pickle.dump({"fetched_at": time.time(), "items": items}, f)
    except Exception:
        logger.exception("Failed writing news cache for %s", symbol)
    return items


# Wires that carry a company's *own* announcements -- earnings, FDA
# decisions, contracts, offerings. Everything else in the feed is somebody
# writing *about* the company, which is a different thing: coverage tends to
# follow a move rather than cause it, so counting it as a catalyst risks
# measuring "was already interesting" instead of "had a reason".
PRIMARY_WIRES = frozenset(
    {"globenewswire", "business wire", "businesswire", "prnewswire", "pr newswire",
     "newsfile corp", "newsfile", "accesswire", "globe newswire"}
)


def is_primary_wire(publisher: str | None) -> bool:
    return bool(publisher) and publisher.strip().lower() in PRIMARY_WIRES


def catalyst_days(
    items: list[dict], sessions: list[date], primary_wire_only: bool = False
) -> dict[date, str]:
    """{trading_date: the headline that could have driven it}.

    A session is catalyst-backed when a qualifying story landed inside
    [open - 18h, close]. Walking sessions rather than items keeps the
    attribution one-way: a story maps to the next session that had not yet
    opened when it published, so a Thursday-evening release lands on Friday
    and never on the Thursday it followed.
    """
    dated: list[tuple[datetime, str]] = []
    for item in items:
        title = (item.get("title") or "").strip()
        if not title or is_low_signal_headline(title, item.get("publisher")):
            continue
        if primary_wire_only and not is_primary_wire(item.get("publisher")):
            continue
        published = parse_published(item.get("publishedDate"))
        if published is not None:
            dated.append((published, title))
    if not dated:
        return {}
    dated.sort(key=lambda p: p[0])

    result: dict[date, str] = {}
    for session in sessions:
        hours = trading_hours_for(session)
        if hours is None:
            continue
        open_at, close_at = hours
        window_start = open_at - _PRE_SESSION_BUFFER
        # Latest qualifying story in the window wins: it's the freshest thing
        # a trader could have known at the point the session was tradeable.
        for published, title in reversed(dated):
            local = published.astimezone(ET)
            if window_start <= local <= close_at:
                result[session] = title
                break
    return result
