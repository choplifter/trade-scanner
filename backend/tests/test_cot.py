"""COT: the CFTC's rows -> weeks, the percentile that makes them readable,
and the cache's best-effort posture. No network: the rows are the shape the
public feed returned for WTI on 2026-09-22."""

import asyncio

import pytest

from app.market_data.cot import (
    CONTRACTS,
    CotCache,
    Week,
    parse_weeks,
    percentile_of,
    reading,
)

WTI = {
    "report_date_as_yyyy_mm_dd": "2026-09-22T00:00:00.000",
    "open_interest_all": "1841811",
    "m_money_positions_long_all": "223190",
    "m_money_positions_short_all": "121362",
    "prod_merc_positions_long": "607458",
    "prod_merc_positions_short": "304558",
}


def _row(day: str, long_: int, short: int, oi: int = 1_000_000) -> dict:
    return {
        "report_date_as_yyyy_mm_dd": f"{day}T00:00:00.000",
        "open_interest_all": str(oi),
        "m_money_positions_long_all": str(long_),
        "m_money_positions_short_all": str(short),
        "prod_merc_positions_long": "10",
        "prod_merc_positions_short": "20",
    }


def test_a_row_becomes_a_week_with_its_nets():
    (week,) = parse_weeks([WTI])
    assert week.report_date.isoformat() == "2026-09-22"
    assert week.money_net == 223190 - 121362
    assert week.commercial_net == 607458 - 304558
    assert week.money_net_pct_oi == pytest.approx(101828 / 1841811)


def test_unusable_rows_are_dropped_and_the_rest_come_back_oldest_first():
    weeks = parse_weeks([_row("2026-09-22", 5, 1), {"open_interest_all": "1"}, _row("2026-09-15", 4, 1)])
    assert [w.report_date.isoformat() for w in weeks] == ["2026-09-15", "2026-09-22"]


def test_a_week_without_open_interest_has_no_share():
    assert Week(parse_weeks([_row("2026-09-22", 5, 1, oi=0)])[0].report_date, 0, 5, 1, 0, 0).money_net_pct_oi is None


def test_the_percentile_needs_a_year_of_weeks():
    assert percentile_of(0.5, [0.1] * 51) is None, "a percentile of 51 readings reads as precision that is not there"
    assert percentile_of(0.5, [0.1] * 52) == 1.0


def test_the_percentile_is_a_mid_rank():
    history = [i / 100 for i in range(100)]
    assert percentile_of(0.5, history) == pytest.approx(0.505)
    assert percentile_of(-1.0, history) == 0.0
    # A flat stretch is not an extreme: a reading like all the others sits
    # in the middle, not at the top.
    assert percentile_of(0.1, [0.1] * 60) == 0.5


def test_the_reading_flags_only_the_extremes():
    # Three years of readings, the latest above every one of them.
    rows = [_row(f"2024-01-{d:02d}", 1000, 900) for d in range(1, 29)]
    rows += [_row(f"2025-{m:02d}-01", 1000, 900) for m in range(1, 13)]
    rows += [_row(f"2026-{m:02d}-0{d}", 1000, 900) for m in range(1, 9) for d in (1, 8)]
    # A spread-out history, so the flags are about where the latest sits.
    rows = [_row(r["report_date_as_yyyy_mm_dd"][:10], 1000 + i * 10, 900) for i, r in enumerate(rows)]
    rows.append(_row("2026-09-22", 9000, 100))
    out = reading(parse_weeks(rows), label="WTI crude", symbol="USO")
    # Just under 1.0: the latest week is part of its own history, and a
    # mid-rank gives its own tie half a place.
    assert out["money_net_percentile"] > 0.98
    assert "crowded side" in out["note"] and "signal" in out["note"]

    calm = reading(parse_weeks(rows[:-1] + [_row("2026-09-22", 1250, 900)]), label="WTI crude", symbol="USO")
    assert calm["note"] is None, "an ordinary reading says nothing"


def test_nothing_usable_is_no_reading():
    assert reading([], label="WTI crude", symbol="USO") is None


class _Client:
    """Counts calls and can be told to fail, like the feed on a bad day."""

    def __init__(self, rows, fail_after: int | None = None):
        self.rows = rows
        self.calls = 0
        self.fail_after = fail_after

    async def get(self, url, params=None, headers=None, timeout=None):
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise RuntimeError("feed down")
        return _Response(self.rows)


class _Response:
    def __init__(self, rows):
        self._rows = rows

    def raise_for_status(self):
        return None

    def json(self):
        return self._rows


def _cache(client, clock=None) -> CotCache:
    return CotCache(client, now=clock or (lambda: 0.0))


def test_a_symbol_the_report_does_not_cover_asks_nothing():
    client = _Client([WTI])
    cache = _cache(client)
    assert asyncio.run(cache.reading("AAPL")) is None
    assert client.calls == 0, "no contract, no request"
    assert cache.covers("uso") and not cache.covers("AAPL")


def test_the_reading_is_cached_until_the_ttl_and_shared_by_the_etfs_of_one_metal():
    client = _Client([WTI])
    cache = _cache(client)
    first = asyncio.run(cache.reading("USO"))
    again = asyncio.run(cache.reading("uso"))
    assert first == again and client.calls == 1
    assert first["contract"] == "WTI crude" and first["symbol"] == "USO"
    # GLD and IAU name the same contract but are cached per symbol.
    assert CONTRACTS["GLD"][0] == CONTRACTS["IAU"][0]


def test_a_failed_fetch_leaves_the_last_reading_standing():
    clock = iter([0.0, 0.0, 10**6, 10**6])
    client = _Client([WTI], fail_after=1)
    cache = CotCache(client, now=lambda: next(clock))
    good = asyncio.run(cache.reading("USO"))
    assert good is not None
    # TTL has passed, the refetch fails: the stale answer beats none.
    assert asyncio.run(cache.reading("USO")) == good


def test_a_first_fetch_that_fails_is_not_cached():
    client = _Client([WTI], fail_after=0)
    cache = _cache(client)
    assert asyncio.run(cache.reading("USO")) is None
    assert asyncio.run(cache.reading("USO")) is None
    assert client.calls == 2, "a failure is retried, not remembered"
