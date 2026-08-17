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

import httpx

logger = logging.getLogger(__name__)

_URL = "https://financialmodelingprep.com/stable/news/stock"
# Enough per request to have several candidates per symbol after filtering,
# without pulling a page of history nobody reads.
_LIMIT_PER_REQUEST = 250
# FMP takes a comma-separated symbol list; kept modest so one oversized URL
# doesn't fail the whole batch.
_BATCH_SIZE = 25

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
    client: httpx.AsyncClient, api_key: str, symbols: list[str]
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

    found: dict[str, str] = {}
    for i in range(0, len(symbols), _BATCH_SIZE):
        batch = symbols[i : i + _BATCH_SIZE]
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
            found[symbol] = title
    return found
