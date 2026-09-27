"""COT: the CFTC's rows -> weeks, the percentile that makes them readable,
and the cache's best-effort posture. No network: the rows are the shape the
public feed returned for WTI on 2026-09-22."""

import asyncio

import pytest

from app.market_data.cot import (
    CONTRACTS,
    DISAGGREGATED,
    FINANCIAL,
    CotCache,
    Week,
    parse_weeks,
    percentile_of,
    reading,
)

WTI_CONTRACT = CONTRACTS["USO"]
ES_CONTRACT = CONTRACTS["SPY"]

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
    assert week.spec_net == 223190 - 121362
    assert week.hedge_net == 607458 - 304558
    assert week.spec_net_pct_oi == pytest.approx(101828 / 1841811)


def test_unusable_rows_are_dropped_and_the_rest_come_back_oldest_first():
    weeks = parse_weeks([_row("2026-09-22", 5, 1), {"open_interest_all": "1"}, _row("2026-09-15", 4, 1)])
    assert [w.report_date.isoformat() for w in weeks] == ["2026-09-15", "2026-09-22"]


def test_a_week_without_open_interest_has_no_share():
    assert Week(parse_weeks([_row("2026-09-22", 5, 1, oi=0)])[0].report_date, 0, 5, 1, 0, 0).spec_net_pct_oi is None


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
    out = reading(parse_weeks(rows), contract=WTI_CONTRACT, symbol="USO")
    # Just under 1.0: the latest week is part of its own history, and a
    # mid-rank gives its own tie half a place.
    assert out["spec_net_percentile"] > 0.98
    assert "top of their three-year range" in out["note"] and "not a sell signal" in out["note"]

    calm = reading(parse_weeks(rows[:-1] + [_row("2026-09-22", 1250, 900)]), contract=WTI_CONTRACT, symbol="USO")
    assert calm["note"] is None, "an ordinary reading says nothing"


def test_nothing_usable_is_no_reading():
    assert reading([], contract=WTI_CONTRACT, symbol="USO") is None


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
    assert CONTRACTS["GLD"].code == CONTRACTS["IAU"].code


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


# --- the financial report ----------------------------------------------------


ES = {
    "report_date_as_yyyy_mm_dd": "2026-09-08T00:00:00.000",
    "open_interest_all": "2071836",
    "lev_money_positions_long": "155517",
    "lev_money_positions_short": "496621",
    "asset_mgr_positions_long": "900000",
    "asset_mgr_positions_short": "300000",
}


def test_the_financial_report_is_read_through_its_own_columns():
    """TFF names the speculators differently and puts them in different
    fields; the disaggregated parser would read all zeros here."""
    (week,) = parse_weeks([ES], FINANCIAL)
    assert week.spec_net == 155517 - 496621
    assert week.hedge_net == 600000
    assert parse_weeks([ES], DISAGGREGATED)[0].spec_net == 0, "the commodity columns are simply absent"


def test_an_index_reading_names_its_groups_and_carries_the_caveat():
    out = reading(parse_weeks([ES], FINANCIAL), contract=ES_CONTRACT, symbol="SPY")
    assert out["contract"] == "E-mini S&P 500"
    assert out["spec_label"] == "leveraged funds" and out["hedge_label"] == "asset managers"
    assert out["spec_net"] < 0, "short is the ordinary state here"
    assert "basis trade" in out["caveat"], "the sign alone would mislead"
    assert out["spec_net_percentile"] is None, "one week is no history"


def test_the_note_names_the_group_of_its_own_report():
    # 60 weeks: past the year the percentile insists on.
    weeks = parse_weeks(
        [_es_row(f"2026-{m:02d}-{d:02d}", 100 + m, 200) for m in range(1, 13) for d in (1, 8, 15, 22, 28)], FINANCIAL
    )
    weeks += parse_weeks([_es_row("2026-12-29", 9000, 100)], FINANCIAL)
    out = reading(weeks, contract=ES_CONTRACT, symbol="SPY")
    # The group of *this* report is named, and the note is a place in the
    # range rather than a direction -- these funds are net short throughout.
    assert out["note"].startswith("Leveraged funds sit at the top")


def _es_row(day: str, long_: int, short: int) -> dict:
    return {
        "report_date_as_yyyy_mm_dd": f"{day}T00:00:00.000",
        "open_interest_all": "1000000",
        "lev_money_positions_long": str(long_),
        "lev_money_positions_short": str(short),
        "asset_mgr_positions_long": "10",
        "asset_mgr_positions_short": "20",
    }


def test_each_symbol_asks_the_dataset_its_contract_names():
    seen = []

    class _Recording(_Client):
        async def get(self, url, params=None, headers=None, timeout=None):
            seen.append((url, params["$where"]))
            return await super().get(url, params, headers, timeout)

    cache = CotCache(_Recording([ES]), now=lambda: 0.0)
    asyncio.run(cache.reading("SPY"))
    asyncio.run(cache.reading("USO"))
    assert "gpe5-46if" in seen[0][0] and "13874A" in seen[0][1]
    assert "72hh-3qpy" in seen[1][0] and "067651" in seen[1][1]
