from datetime import timedelta
import httpx
import pytest

from app.market_data.fmp_news import fetch_fmp_headlines, is_low_signal_headline

# Real headlines observed from FMP while measuring its noise profile -- 30%
# of 200 items across 12 symbols were litigation notices.
_LITIGATION = [
    "INVESTOR ALERT: Pomerantz Law Firm Reminds Investors with Losses on their Investment in Hertz",
    "CAPR Investor Alert: Kessler Topaz Meltzer & Check, LLP Encourages CAPR Investors to Contact",
    "HTZ Stockholders Have Rights - If You Lost Money Investing in Hertz Global",
    "Class Action Filed Against Acme Corp",
    "Lead Plaintiff Deadline Approaching for Investors",
    # Reached the panel via PRNewsWire, which the publisher check trusts --
    # so litigation phrasings have to be caught on the title alone.
    "Capricor Therapeutics, Inc. Sued for Securities Law Violations - Contact Us",
    "Investigation of Acme Holdings Inc. Announced on Behalf of Investors",
]
_HOLDINGS = [
    "Bank of America Corp DE Purchases 1,163,577 Shares of The Western Union Company",
    "Vanguard Group Inc. Raises Stake in Acme Corp",
]
_OPINION = [
    "Is It Too Late to Buy Ondas Stock?",
    "Better Space Stock: Redwire or Kratos Defense?",
    "Why Eton Pharmaceuticals Is Top of the Class Today",
    "NBIS, SMCI, CRWV and More: 5 Stocks Investors Couldn't Stop Buzzing About",
    "Super Micro Computer Stock is Soaring!",
]
# The stories the whole feature exists to surface -- these must survive.
_REAL = [
    "Capricor Therapeutics Announces FDA Approval of Deramiocel",
    "Eton Pharmaceuticals Reports Q2 Revenue Up 42%",
    "Intuitive Machines stock jumps 8% as Wall Street backs $1.8B backlog",
    "Redwire Wins $80M Department of Defense Contract",
    "Ondas Holdings Upgraded To Buy At Cantor Fitzgerald, Price Target $12",
    "HeartFlow Announces Pricing of Upsized Public Offering",
]


@pytest.mark.parametrize("headline", _LITIGATION)
def test_litigation_notices_are_rejected(headline):
    """The single biggest noise class, and backwards as a catalyst: these are
    published *after* a collapse, so counting one would mark past losers as
    catalyst-backed."""
    assert is_low_signal_headline(headline)


@pytest.mark.parametrize("headline", _HOLDINGS)
def test_13f_holdings_churn_is_rejected(headline):
    assert is_low_signal_headline(headline)


@pytest.mark.parametrize("headline", _OPINION)
def test_opinion_and_listicles_are_rejected(headline):
    assert is_low_signal_headline(headline)


@pytest.mark.parametrize("headline", _REAL)
def test_genuine_catalysts_survive_the_filter(headline):
    assert not is_low_signal_headline(headline)


def test_analyst_actions_are_kept_deliberately():
    """An upgrade is a real reason a stock moved today, unlike a lawsuit
    notice or a 13F filing which describe something already past."""
    assert not is_low_signal_headline("Ondas Upgraded To Overweight At Morgan Stanley")
    assert not is_low_signal_headline("Acme Price Target Raised To $50 From $35 At Jefferies")


def test_empty_headline_is_low_signal():
    assert is_low_signal_headline("")
    assert is_low_signal_headline(None)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _recent(hours_ago: float = 1) -> str:
    """FMP's naive "YYYY-MM-DD HH:MM:SS", which it publishes in UTC."""
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime("%Y-%m-%d %H:%M:%S")


@pytest.mark.asyncio
async def test_returns_the_most_recent_non_noise_headline_per_symbol():
    def handler(request):
        return httpx.Response(
            200,
            json=[
                # FMP returns newest first; the newest for AAA is noise, so
                # the next real one should win rather than AAA being skipped.
                {"symbol": "AAA", "title": "AAA Investor Alert: Law Offices of Someone", "publishedDate": _recent(1)},
                {"symbol": "AAA", "title": "AAA Announces FDA Clearance", "publishedDate": _recent(2)},
                {"symbol": "BBB", "title": "BBB Reports Record Quarterly Revenue", "publishedDate": _recent(3)},
            ],
        )

    async with _client(handler) as client:
        result = await fetch_fmp_headlines(client, "key", ["AAA", "BBB"])

    assert result == {
        "AAA": "AAA Announces FDA Clearance",
        "BBB": "BBB Reports Record Quarterly Revenue",
    }


@pytest.mark.asyncio
async def test_a_symbol_with_only_noise_is_absent_not_none():
    # Absent lets a caller tell "FMP had nothing usable" from "FMP wasn't
    # asked", which matters because the fallback only fills gaps.
    def handler(request):
        return httpx.Response(200, json=[{"symbol": "AAA", "title": "AAA Class Action Filed", "publishedDate": _recent()}])

    async with _client(handler) as client:
        assert await fetch_fmp_headlines(client, "key", ["AAA"]) == {}


@pytest.mark.asyncio
async def test_symbols_outside_the_requested_batch_are_ignored():
    # FMP has been observed mis-tagging stories; this at least rejects ones
    # attributed to a symbol nobody asked about.
    def handler(request):
        return httpx.Response(200, json=[{"symbol": "ZZZ", "title": "ZZZ Announces Merger", "publishedDate": _recent()}])

    async with _client(handler) as client:
        assert await fetch_fmp_headlines(client, "key", ["AAA"]) == {}


@pytest.mark.asyncio
async def test_a_failed_request_returns_nothing_rather_than_raising():
    """Best-effort by design: this is a fallback, so a failure must leave the
    Alpaca answer standing rather than propagate."""
    def handler(request):
        return httpx.Response(500)

    async with _client(handler) as client:
        assert await fetch_fmp_headlines(client, "key", ["AAA"]) == {}


@pytest.mark.asyncio
async def test_no_key_or_no_symbols_makes_no_request():
    called = False

    def handler(request):
        nonlocal called
        called = True
        return httpx.Response(200, json=[])

    async with _client(handler) as client:
        assert await fetch_fmp_headlines(client, "", ["AAA"]) == {}
        assert await fetch_fmp_headlines(client, "key", []) == {}
    assert not called


# --- NewsCache fallback policy ------------------------------------------------


class _Settings:
    scanner_news_refresh_interval = 0.0
    fmp_api_key = "key"

    @property
    def has_fmp_credentials(self) -> bool:
        return bool(self.fmp_api_key)


@pytest.mark.asyncio
async def test_alpaca_wins_where_both_feeds_have_a_story(monkeypatch):
    """Order matters: the catalyst boost's +9.1pp was calibrated on Alpaca
    headlines, so FMP must never overwrite one."""
    from app.market_data import news_cache as nc

    async def fake_alpaca(clients, symbols):
        return {"AAA": "AAA Reports Earnings Beat"}

    async def fake_fmp(client, key, symbols):
        # Would win if the fallback overrode rather than filled.
        return {sym: f"{sym} FMP story" for sym in symbols}

    monkeypatch.setattr(nc, "fetch_headlines", fake_alpaca)
    monkeypatch.setattr(nc, "fetch_fmp_headlines", fake_fmp)

    cache = nc.NewsCache(_Settings(), clients=None, http_client=object())
    await cache.ensure_fresh(["AAA", "BBB"])

    assert cache.get("AAA") == "AAA Reports Earnings Beat"
    assert cache.source("AAA") == nc.ALPACA
    # BBB had no Alpaca headline, so the gap is filled and marked.
    assert cache.get("BBB") == "BBB FMP story"
    assert cache.source("BBB") == nc.FMP


@pytest.mark.asyncio
async def test_no_fallback_without_an_http_client(monkeypatch):
    from app.market_data import news_cache as nc

    async def fake_alpaca(clients, symbols):
        return {}

    async def fake_fmp(client, key, symbols):
        raise AssertionError("should not be called without an http client")

    monkeypatch.setattr(nc, "fetch_headlines", fake_alpaca)
    monkeypatch.setattr(nc, "fetch_fmp_headlines", fake_fmp)

    cache = nc.NewsCache(_Settings(), clients=None, http_client=None)
    await cache.ensure_fresh(["AAA"])
    assert cache.get("AAA") is None
    assert cache.source("AAA") is None


# --- chart panel: merging both feeds ------------------------------------------


def _item(headline, source, minutes_ago, feed="alpaca"):
    from datetime import datetime, timedelta, timezone
    from app.news.client import NewsItem

    return NewsItem(
        headline=headline,
        summary="",
        source=source,
        url=None,
        published_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
        feed=feed,
    )


def test_chart_news_merges_both_feeds_newest_first():
    from app.symbols.info import merge_news

    merged = merge_news(
        [_item("Alpaca older story", "Benzinga", 120)],
        [_item("FMP newer story", "GlobeNewsWire", 10, feed="fmp")],
    )
    assert [i.headline for i in merged] == ["FMP newer story", "Alpaca older story"]
    assert [i.feed for i in merged] == ["fmp", "alpaca"]


def test_chart_news_dedupes_syndicated_releases():
    """A company press release reaches both feeds; showing it twice with
    different publisher labels would look like two separate events."""
    from app.symbols.info import merge_news

    merged = merge_news(
        [_item("Acme Reports Q2 Results", "Benzinga", 30)],
        [_item("acme   reports   q2   results", "GlobeNewsWire", 25, feed="fmp")],
    )
    assert len(merged) == 1


def test_chart_news_is_capped():
    from app.symbols.info import merge_news

    merged = merge_news(
        [_item(f"A{i}", "Benzinga", i) for i in range(10)],
        [_item(f"F{i}", "GlobeNewsWire", i, feed="fmp") for i in range(10)],
        limit=6,
    )
    assert len(merged) == 6


def test_stale_headlines_are_rejected():
    """FMP returns a symbol's whole history with no date bound. CPRT's newest
    item was five days old while the stock was up 7.55% today -- flagging that
    as the reason for the move would be wrong, and feeding it to the catalyst
    boost worse."""
    import asyncio

    def handler(request):
        return httpx.Response(
            200,
            json=[
                {"symbol": "AAA", "title": "AAA Announces Merger", "publishedDate": _recent(24 * 5)},
                {"symbol": "BBB", "title": "BBB Announces Merger", "publishedDate": _recent(6)},
            ],
        )

    async def run():
        async with _client(handler) as client:
            return await fetch_fmp_headlines(client, "key", ["AAA", "BBB"])

    result = asyncio.run(run())
    assert "AAA" not in result
    assert result["BBB"] == "BBB Announces Merger"


def test_undated_items_are_rejected_rather_than_assumed_fresh():
    import asyncio

    def handler(request):
        return httpx.Response(200, json=[{"symbol": "AAA", "title": "AAA Announces Merger"}])

    async def run():
        async with _client(handler) as client:
            return await fetch_fmp_headlines(client, "key", ["AAA"])

    assert asyncio.run(run()) == {}


def test_batches_stay_small_so_quiet_symbols_are_not_crowded_out():
    """limit is shared across every symbol in a request. At 25 per batch, a
    real response covered 20 symbols and gave CPRT zero of its 40 available
    articles because NVDA/GOOG/AAPL consumed the quota."""
    from app.market_data import fmp_news

    assert fmp_news._BATCH_SIZE <= 5


def test_cutoff_reaches_back_to_the_last_session_not_a_fixed_window():
    """A flat 48h from a Monday morning reaches only to Saturday, excluding
    the whole of Friday's session -- the exact session the scanner shows
    while the market is closed. It cut flagged rows from 46 to 7 in practice."""
    from datetime import datetime, timezone
    from app.market_data.fmp_news import recent_news_cutoff
    from app.services.market_clock import ET

    # Monday 2026-08-17, pre-market.
    monday = datetime(2026, 8, 17, 5, 0, tzinfo=timezone.utc)
    cutoff = recent_news_cutoff(monday)

    # Friday 2026-08-14 was the last session; the cutoff must precede its open.
    assert cutoff.astimezone(ET).date().isoformat() == "2026-08-13"
    # And a flat 48h would not have reached it.
    assert cutoff < monday - timedelta(hours=48)


def test_cutoff_uses_todays_open_once_the_session_has_begun():
    from datetime import datetime, timezone
    from app.market_data.fmp_news import recent_news_cutoff
    from app.services.market_clock import ET

    # Thursday 2026-08-13, mid-session (14:00 UTC = 10:00 ET).
    midday = datetime(2026, 8, 13, 14, 0, tzinfo=timezone.utc)
    cutoff = recent_news_cutoff(midday).astimezone(ET)
    # 09:30 open minus the 18h buffer lands on the previous evening.
    assert cutoff.date().isoformat() == "2026-08-12"


def test_fmp_timestamps_are_eastern_not_utc():
    """A Q2 release stamped 16:05 is five minutes after the ET close -- when
    earnings land. Read as UTC it becomes 12:05 ET, mid-session, and fell the
    wrong side of a session-anchored cutoff, discarding the actual catalyst
    behind a +44.98% move."""
    from app.market_data.fmp_news import parse_published
    from app.services.market_clock import ET

    parsed = parse_published("2026-08-13 16:05:00")
    assert parsed.astimezone(ET).strftime("%H:%M") == "16:05"


def test_an_after_close_release_survives_the_next_sessions_cutoff():
    from datetime import datetime, timezone
    from app.market_data.fmp_news import parse_published, recent_news_cutoff

    # Friday pre-market: the cutoff must still admit Thursday's 16:05 release.
    friday_premarket = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
    assert parse_published("2026-08-13 16:05:00") >= recent_news_cutoff(friday_premarket)
