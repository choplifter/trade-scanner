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


@pytest.mark.asyncio
async def test_returns_the_most_recent_non_noise_headline_per_symbol():
    def handler(request):
        return httpx.Response(
            200,
            json=[
                # FMP returns newest first; the newest for AAA is noise, so
                # the next real one should win rather than AAA being skipped.
                {"symbol": "AAA", "title": "AAA Investor Alert: Law Offices of Someone"},
                {"symbol": "AAA", "title": "AAA Announces FDA Clearance"},
                {"symbol": "BBB", "title": "BBB Reports Record Quarterly Revenue"},
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
        return httpx.Response(200, json=[{"symbol": "AAA", "title": "AAA Class Action Filed"}])

    async with _client(handler) as client:
        assert await fetch_fmp_headlines(client, "key", ["AAA"]) == {}


@pytest.mark.asyncio
async def test_symbols_outside_the_requested_batch_are_ignored():
    # FMP has been observed mis-tagging stories; this at least rejects ones
    # attributed to a symbol nobody asked about.
    def handler(request):
        return httpx.Response(200, json=[{"symbol": "ZZZ", "title": "ZZZ Announces Merger"}])

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
