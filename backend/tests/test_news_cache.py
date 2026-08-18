"""The FMP news fallback's negative cache.

Alpaca is the primary feed and is re-checked on the normal cadence; FMP only
fills the gaps. Asking it repeatedly about symbols it has already said it has
nothing for was the largest single consumer of this app's FMP quota.
"""

from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_fmp_is_not_re_asked_about_a_symbol_it_had_nothing_for(monkeypatch):
    """The bulk of this app's FMP traffic was re-confirming, every fifteen
    minutes, that quiet stocks were still quiet.

    Alpaca is still re-checked on the normal cadence, so a story breaking in
    the gap is caught by the primary feed -- what this suppresses is only the
    fallback's repeat question.
    """
    from app.market_data import news_cache as module

    calls: list[list[str]] = []

    async def fake_fetch(client, key, symbols, cutoff=None):
        calls.append(sorted(symbols))
        return {}  # FMP has nothing for any of them

    monkeypatch.setattr(module, "fetch_fmp_headlines", fake_fetch)

    cache = module.NewsCache.__new__(module.NewsCache)
    cache.settings = SimpleNamespace(has_fmp_credentials=True, fmp_api_key="k")
    cache.http_client = object()
    cache._headlines = {}
    cache._sources = {}
    cache._fmp_miss_until = {}

    await cache._fill_gaps_from_fmp(["AAA", "BBB"])
    await cache._fill_gaps_from_fmp(["AAA", "BBB"])

    assert calls == [["AAA", "BBB"]], f"asked twice: {calls}"


@pytest.mark.asyncio
async def test_a_symbol_fmp_answers_for_is_asked_again_next_time(monkeypatch):
    """A miss is remembered; a hit is not. Otherwise a symbol that had news
    once would be skipped when its next story arrives."""
    from app.market_data import news_cache as module

    calls: list[list[str]] = []

    async def fake_fetch(client, key, symbols, cutoff=None):
        calls.append(sorted(symbols))
        return {"AAA": "Real news"}

    monkeypatch.setattr(module, "fetch_fmp_headlines", fake_fetch)

    cache = module.NewsCache.__new__(module.NewsCache)
    cache.settings = SimpleNamespace(has_fmp_credentials=True, fmp_api_key="k")
    cache.http_client = object()
    cache._headlines = {}
    cache._sources = {}
    cache._fmp_miss_until = {}

    await cache._fill_gaps_from_fmp(["AAA", "BBB"])
    await cache._fill_gaps_from_fmp(["AAA", "BBB"])

    # AAA answered, so it stays askable; BBB missed and is held back.
    assert calls == [["AAA", "BBB"], ["AAA"]]
