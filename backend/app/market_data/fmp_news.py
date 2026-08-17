"""FMP news, used as a fallback where Alpaca's Benzinga feed has nothing.

Why a fallback at all. Measured live over 8 scanner symbols in a 48h window,
Alpaca returned a headline for 2 of them; FMP returned one for 7, 60 items in
total. The gap is not random -- Benzinga's coverage thins out on exactly the
small and mid caps this scanner surfaces, which is where a catalyst matters
most.

Why only a fallback, and never a replacement. FMP's volume comes with a
different and worse noise profile. Classifying 200 items across 12 symbols:

    law-firm "investor alert"   30%
    analyst/opinion pieces       8%
    13F / holdings churn         1%
    price target / rating        1%
    plausibly real              60%

Thirty percent securities-litigation spam is not merely useless as a catalyst
signal, it is actively backwards: those notices are published *after* a stock
has already collapsed, so treating one as a reason-for-the-move would
systematically mark past losers as catalyst-backed. is_low_signal_headline
below filters those classes out before a headline is ever offered as a
catalyst -- measured live, it rejects 83% of raw FMP items while still
covering every symbol Alpaca missed.

Analyst upgrades, downgrades and price targets are deliberately *kept*. They
are a genuine reason a stock moved today, which is the question the catalyst
boost asks -- unlike a lawsuit notice or a 13F filing, which describe
something that already happened.

Known limitation this module cannot fix: FMP occasionally mis-tags a story.
A Western Union article was observed returned under IMXI, correctly filtered
by neither symbol matching (FMP itself labelled it IMXI) nor the noise
patterns. Treat FMP-sourced catalysts as lower-confidence than Alpaca's for
that reason as well as the noise rate.
"""

import logging
import re
from datetime import datetime, timedelta, timezone

import httpx

from app.services.market_clock import ET, trading_hours_for

logger = logging.getLogger(__name__)

_URL = "https://financialmodelingprep.com/stable/news/stock"
# Enough per request to have several candidates per symbol after filtering,
# without pulling a page of history nobody reads.
_LIMIT_PER_REQUEST = 250
# Small on purpose. `limit` is shared across every symbol in the request, and
# coverage is wildly uneven: asking for 25 symbols at limit=250 came back with
# 250 items covering only 20 of them -- NVDA alone took 40, GOOG 34, AAPL 27 --
# and CPRT, which had 40 of its own articles available, got zero. A quiet
# symbol batched with loud ones is simply never reached. Five symbols to a
# request gives each one room even when its neighbours are noisy.
_BATCH_SIZE = 5

# How far before the relevant session's open a story still counts. Wide
# enough to catch a pre-market announcement or an after-hours release from
# the session before, which is when a good share of catalysts land.
_PRE_SESSION_BUFFER = timedelta(hours=18)


def parse_published(value: str | None) -> datetime | None:
    """FMP publishes "YYYY-MM-DD HH:MM:SS" with no zone. It is **US/Eastern**,
    not UTC, and reading it as UTC shifts every story four hours earlier.

    That is not cosmetic. Eton's Q2 release is stamped 2026-08-13 16:05 --
    five minutes after the close, which is when earnings land. Read as UTC it
    becomes 12:05 ET, mid-session, and fell the wrong side of a cutoff
    anchored to the session open, so the actual catalyst behind a +44.98%
    move was discarded as stale. The pre-market releases stamped 06:50 say
    the same thing: 06:50 ET is a standard release slot, 02:50 ET is not.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).replace(tzinfo=ET).astimezone(timezone.utc)
    except ValueError:
        return None


def recent_news_cutoff(now: datetime | None = None) -> datetime:
    """The oldest a story can be and still explain the move on screen.

    Anchored to the most recent trading session rather than a fixed number
    of hours back, because a fixed window is wrong exactly when it matters.
    A flat 48h measured from a Monday morning reaches back only to Saturday
    -- excluding the whole of Friday's session, which is precisely the
    session the scanner is showing while the market is closed. That is not
    hypothetical: it cut flagged rows from 46 to 7 the first time it ran,
    with the market closed over a weekend.

    So: back to the open of the last session that has actually begun, less a
    buffer for pre-market and prior-evening releases. During a live session
    that's this morning's open; on a Monday it's Friday's.
    """
    now = now or datetime.now(timezone.utc)
    day = now.astimezone(ET).date()
    for _ in range(10):  # a 10-day walk clears any holiday-extended weekend
        hours = trading_hours_for(day)
        if hours is not None and hours[0] <= now.astimezone(ET):
            return (hours[0] - _PRE_SESSION_BUFFER).astimezone(timezone.utc)
        day -= timedelta(days=1)
    # No session found at all (a data gap) -- fall back to a plain window
    # rather than returning nothing and silently dropping every headline.
    return now - timedelta(days=4)

# The noise classes measured above. Matched against the headline only -- the
# body isn't fetched, and these templates are recognisable from the title by
# design, which is what makes them cheap to reject.
_LOW_SIGNAL = re.compile(
    r"""
    investor\s+alert
  | class\s+action
  | shareholder\s+(alert|rights|investigation)
  | law\s+(firm|offices)
  | reminds\s+investors
  | encourages\s+.*investors
  | (lead\s+plaintiff|securities\s+fraud)
  | deadline\s+(approach|remind)
  | have\s+rights
  # Litigation also arrives via the primary wires, which the publisher check
  # deliberately trusts -- "Capricor Therapeutics, Inc. Sued for Securities
  # Law Violations" came through PRNewsWire and slipped past the patterns
  # above, so the phrasings have to be caught on the title alone.
  | \bsued\b
  | securities\s+law\s+violation
  | (investigat\w+)\s+(of|into)\s+.*\b(inc|corp|ltd|plc|holdings)\b
  | on\s+behalf\s+of\s+(investors|shareholders)
  | (purchases|sells|acquires|buys|sold|bought)\s+[\d,]+\s+shares
  | (raises|lowers|boosts|trims)\s+(stake|position|holdings)
  | (stake|position)\s+in\s+.+\s+(increased|lowered|raised)
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Opinion/listicle templates. Separate from _LOW_SIGNAL because they're a
# judgement call rather than clearly-not-news: "Is It Too Late To Buy X" is
# commentary *about* a move, not the cause of one.
_OPINION = re.compile(
    r"""
    ^(is\s+|why\s+|should\s+you|better\s+|here'?s\s+why|what\s+to\s+know)
  | (a|the)\s+(buy|sell)\s*\??$
  | moving\s+to\s+(buy|sell|hold)
  | (growth|value)\s+play
  | too\s+risky
  | ^\d+\s+(stocks?|things|reasons)
  | stocks?\s+to\s+(buy|watch|consider)
  | \bstock\s+is\s+(soaring|surging|plunging|sinking)
  | couldn'?t\s+stop\s+buzzing
    """,
    re.IGNORECASE | re.VERBOSE,
)


# Publishers whose entire output is commentary rather than news. This is a
# far more reliable signal than title wording, which the first version of
# this filter relied on alone and which let "Is Cipher Digital (CIFR) a Buy
# as Wall Street Analysts Look Optimistic?" and "Sable Offshore: Too Risky,
# Moving To Sell" through -- a headline template can be phrased a thousand
# ways, but Seeking Alpha never publishes a company's own announcement.
#
# Measured over 250 FMP items for 9 symbols, these accounted for 71 of the
# 109 that survived title filtering alone.
#
# Kept: GlobeNewsWire, Business Wire, PRNewsWire, Newsfile and friends carry
# the company's own releases, which is exactly the catalyst class wanted;
# Benzinga, Reuters and Bloomberg are newsrooms that break real stories.
_COMMENTARY_PUBLISHERS = frozenset(
    {
        "seeking alpha",
        "zacks investment research",
        "gurufocus",
        "marketbeat",
        "24/7 wall street",
        "the motley fool",
        "investor place",
        "investorplace",
        "defense world",
        "schaeffers research",
        "invezz",
        "simply wall st",
        "insider monkey",
    }
)


def is_commentary_publisher(publisher: str | None) -> bool:
    return bool(publisher) and publisher.strip().lower() in _COMMENTARY_PUBLISHERS


def is_low_signal_headline(headline: str, publisher: str | None = None) -> bool:
    """True for anything that must never count as a catalyst.

    Two independent tests, because they catch different things: the
    publisher rejects whole commentary outlets regardless of phrasing, and
    the title patterns catch litigation notices and 13F churn, which arrive
    from the same wires as real news and so can't be filtered by source.

    Deliberately bounded in scope. A filter aggressive enough to catch every
    dubious headline would also start dropping the earnings/FDA/contract
    stories the whole feature exists to surface, so anything not clearly in
    one of the measured noise classes is left through.
    """
    if not headline:
        return True
    if is_commentary_publisher(publisher):
        return True
    return bool(_LOW_SIGNAL.search(headline) or _OPINION.search(headline))


async def fetch_fmp_headlines(
    client: httpx.AsyncClient,
    api_key: str,
    symbols: list[str],
    cutoff: datetime | None = None,
) -> dict[str, str]:
    """{symbol: most recent non-noise headline}. Symbols with nothing usable
    are absent rather than present-with-None, so a caller can tell "FMP had
    nothing" from "FMP wasn't asked".

    Best-effort throughout, matching news.fetch_headlines: this is a
    fallback, and a failure here should leave the Alpaca answer standing
    rather than propagate.
    """
    if not symbols or not api_key:
        return {}

    cutoff = cutoff or recent_news_cutoff()
    found: dict[str, str] = {}
    await _collect(client, api_key, symbols, cutoff, found, _BATCH_SIZE)

    # Second pass, one symbol at a time, for whatever the batched pass missed.
    # Batching alone isn't enough however small the batch: `limit` is shared,
    # and a single prolific name can swallow it -- CAPR took 212 of 250 items
    # in a five-symbol request, starving neighbours that had news of their
    # own. Retrying individually is bounded work (only the stragglers) and is
    # what actually guarantees a symbol with news gets looked at.
    missing = [s for s in symbols if s not in found]
    if missing:
        await _collect(client, api_key, missing, cutoff, found, 1)
    return found


async def _collect(
    client: httpx.AsyncClient,
    api_key: str,
    symbols: list[str],
    cutoff: datetime,
    found: dict[str, str],
    batch_size: int,
) -> None:
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i : i + batch_size]
        try:
            response = await client.get(
                _URL,
                params={"symbols": ",".join(batch), "limit": _LIMIT_PER_REQUEST, "apikey": api_key},
            )
            response.raise_for_status()
            items = response.json()
        except Exception:
            logger.exception("FMP news fetch failed for a batch of %d symbols", len(batch))
            continue

        if not isinstance(items, list):
            continue
        # Newest first is FMP's own ordering; taking the first usable one per
        # symbol therefore yields the most recent real story rather than the
        # most recent anything.
        for item in items:
            symbol = item.get("symbol")
            title = (item.get("title") or "").strip()
            if symbol in found or symbol not in batch or not title:
                continue
            if is_low_signal_headline(title, item.get("publisher")):
                continue
            published = parse_published(item.get("publishedDate"))
            if published is None or published < cutoff:
                continue
            found[symbol] = title
