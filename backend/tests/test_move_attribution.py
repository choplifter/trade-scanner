"""Move Attribution (app.attribution): sizing moves against the recent past,
and lining each up with what else happened."""

from datetime import date, datetime, timedelta, timezone

import pytest

from app.attribution.report import (
    Candle,
    attribute,
    daily_rows,
    factor_stats,
    intraday_rows,
    yield_opposite,
    yield_threshold_bp,
)
from app.attribution.sources import Release, merge_releases, opex_label, parse_fred_release_dates

UTC = timezone.utc


def _days(closes, start=datetime(2026, 3, 2, 5, tzinfo=UTC)):
    # Weekdays only, stamped at New York midnight like Alpaca's daily bars.
    out, day = [], start
    for c in closes:
        while day.weekday() >= 5:
            day += timedelta(days=1)
        out.append(Candle(at=day, open=c, close=c))
        day += timedelta(days=1)
    return out


def _wiggle(n, base=100.0, step=0.3):
    return [base + (step if i % 2 else -step) for i in range(n)]


# --- sizing ---------------------------------------------------------------


def test_a_day_far_outside_the_recent_spread_is_big():
    closes = _wiggle(30) + [95.0]
    rows = daily_rows(_days(closes), k=2.5)
    assert rows[-1].big and rows[-1].z < -2.5
    assert not any(r.big for r in rows[:-1])


def test_daily_rows_split_the_move_into_gap_and_session():
    bars = [Candle(datetime(2026, 3, 2, 5, tzinfo=UTC), 100, 100), Candle(datetime(2026, 3, 3, 5, tzinfo=UTC), 102, 101)]
    [row] = daily_rows(bars, k=2.5)
    assert row.gap_pct == pytest.approx(2.0)
    assert row.session_pct == pytest.approx(-0.980, abs=1e-3)
    assert row.ret_pct == pytest.approx(1.0)


def _intraday(start, closes, minutes=15):
    return [Candle(start + timedelta(minutes=minutes * i), c, c) for i, c in enumerate(closes)]


def test_a_premarket_candle_is_judged_against_the_premarket():
    # Small by regular-hours standards, big for premarket. Twenty sessions of quiet premarket, 04:00-09:15 New York (08:00-13:15
    # UTC), then a day whose 08:30 candle jumps 0.4 %.
    open_utc = datetime(2026, 3, 10, 8, 0, tzinfo=UTC)
    quiet = [100 + (0.01 if i % 2 else -0.01) for i in range(22)]
    bars = []
    for day in range(20):
        bars += _intraday(open_utc + timedelta(days=day), quiet)
    jump_day = open_utc + timedelta(days=20)
    bars += _intraday(jump_day, quiet[:18] + [100.4])  # 04:00 .. 08:15, then 08:30
    rows = intraday_rows(bars, 15, k=2.5)
    assert rows[-1].session == "pre" and rows[-1].big


def test_a_candle_after_a_gap_is_not_sized():
    start = datetime(2026, 3, 10, 14, 0, tzinfo=UTC)
    bars = _intraday(start, [100, 100.1]) + _intraday(start + timedelta(hours=6), [110])
    rows = intraday_rows(bars, 15, k=2.5)
    assert all(r.at != start + timedelta(hours=6) for r in rows)


# --- attribution ------------------------------------------------------------


def _rel(label, at):
    return Release(label, at, exact=True)


def test_intraday_the_release_lands_on_its_candle_and_the_rest_of_the_day_is_context():
    start = datetime(2026, 3, 11, 12, 15, tzinfo=UTC)  # 08:15 New York
    rows = intraday_rows(_intraday(start, [100, 100, 100, 100]), 15, k=2.5)
    cpi = _rel("CPI", datetime(2026, 3, 11, 12, 30, tzinfo=UTC))
    attribute(rows, mode="intraday", candle_minutes=15, releases=[cpi], earnings={})
    at_release = [r for r in rows if r.at == cpi.at][0]
    later = [r for r in rows if r.at > cpi.at]
    assert at_release.releases == ["CPI"] and "CPI" in at_release.factors
    assert all(r.releases == [] and "CPI day" in r.context for r in later)


def test_earnings_count_on_the_report_day_and_the_next():
    rows = daily_rows(_days(_wiggle(5)), k=2.5)
    report_day = rows[1].at.astimezone(timezone(timedelta(hours=-5))).date()
    attribute(rows, mode="daily", candle_minutes=390, releases=[], earnings={report_day: ["NVDA"]})
    flagged = [i for i, r in enumerate(rows) if "Mega-cap earnings" in r.factors]
    assert flagged == [1, 2]
    assert rows[2].context == ["Earnings: NVDA"]


def test_daily_yield_and_vix_moves_come_from_their_closes():
    rows = daily_rows(_days([100, 101, 99]), k=2.5)
    d = [r.at.astimezone(timezone(timedelta(hours=-5))).date() for r in rows]
    prev = date(2026, 3, 2)
    ten_year = {prev: 4.90, d[0]: 5.02, d[1]: 5.00}
    vix = {prev: 15.0, d[0]: 17.0, d[1]: 16.0}
    attribute(rows, mode="daily", candle_minutes=390, releases=[], earnings={}, ten_year=ten_year, vix=vix)
    assert rows[0].yield_bp == pytest.approx(12.0)
    assert "10Y ±10bp+" in rows[0].factors
    assert rows[0].vix_pct == pytest.approx(13.33, abs=0.01) and "VIX +10%" in rows[0].factors
    assert rows[1].yield_bp == pytest.approx(-2.0) and "10Y ±10bp+" not in rows[1].factors


def test_intraday_yield_is_estimated_from_ief_opposite_to_its_price():
    start = datetime(2026, 3, 11, 14, 0, tzinfo=UTC)
    rows = intraday_rows(_intraday(start, [100, 100, 100]), 15, k=2.5)
    ief = {start: 95.0, start + timedelta(minutes=15): 95.0, start + timedelta(minutes=30): 94.81}
    attribute(rows, mode="intraday", candle_minutes=15, releases=[], earnings={}, ief=ief)
    last = rows[-1]
    # IEF -0.2 % over a ~7y duration: yields up about 2.9 bp.
    assert last.yield_estimated and last.yield_bp == pytest.approx(2.857, abs=0.01)


def test_the_lift_compares_big_moves_with_all_of_them():
    rows = daily_rows(_days(_wiggle(40)), k=2.5)
    for r in rows[:10]:
        r.factors.add("CPI")
    for r in rows[:4]:
        r.big = True
    rows[20].big = True
    [stat] = factor_stats(rows)
    # 4 of 5 big moves had it, against 10 of 39 candles overall.
    assert (stat.big_with, stat.big_total, stat.all_with, stat.all_total) == (4, 5, 10, 39)
    assert stat.lift == pytest.approx((4 / 5) / (10 / 39))


def test_yield_opposite_counts_price_against_yields():
    rows = daily_rows(_days(_wiggle(4)), k=2.5)
    rows[0].big, rows[0].ret_pct, rows[0].yield_bp = True, -1.5, 12.0
    rows[1].big, rows[1].ret_pct, rows[1].yield_bp = True, 1.2, 11.0
    rows[2].big, rows[2].ret_pct, rows[2].yield_bp = True, 1.0, 3.0  # not sharp
    assert yield_opposite(rows, 10) == (1, 2)


# --- sources -----------------------------------------------------------------


def test_fred_dates_get_the_releases_scheduled_new_york_time():
    [cpi] = parse_fred_release_dates({"release_dates": [{"release_id": 10, "date": "2026-01-13"}]}, 10)
    [fomc] = parse_fred_release_dates({"release_dates": [{"release_id": 101, "date": "2026-07-29"}]}, 101)
    assert cpi.at == datetime(2026, 1, 13, 13, 30, tzinfo=UTC)  # 08:30 EST
    assert fomc.at == datetime(2026, 7, 29, 18, 0, tzinfo=UTC)  # 14:00 EDT
    assert not cpi.exact


def test_fmp_supplies_the_exact_minute_for_a_release_fred_also_lists():
    fred = [Release("PCE", datetime(2025, 12, 23, 13, 30, tzinfo=UTC), exact=False)]
    fmp = [Release("PCE", datetime(2025, 12, 23, 15, 0, tzinfo=UTC), exact=True)]
    assert merge_releases(fred, fmp) == fmp


@pytest.mark.parametrize(
    "day, label",
    [(date(2026, 9, 18), "Quad witching"), (date(2026, 10, 16), "OPEX"), (date(2026, 10, 9), None), (date(2026, 10, 15), None)],
)
def test_options_expiry_is_the_third_friday(day, label):
    assert opex_label(day) == label


def test_the_yield_threshold_matches_the_charts():
    assert yield_threshold_bp(15) == pytest.approx(2.06, abs=0.01)
    assert yield_threshold_bp(390) == pytest.approx(10.5)


def test_release_factors_are_measured_only_where_releases_are_known():
    rows = daily_rows(_days(_wiggle(41)), k=2.5)
    known_from = rows[20].at
    cpi_day = rows[30].at
    attribute(
        rows,
        mode="daily",
        candle_minutes=390,
        releases=[_rel("CPI", cpi_day + timedelta(hours=8))],
        earnings={},
        releases_known_from=known_from,
    )
    rows[30].big = rows[5].big = True
    cpi = next(s for s in factor_stats(rows) if s.name == "CPI")
    # Twenty known days, one CPI among them, and one of the big moves in
    # that stretch -- the unknown first twenty days count for nothing.
    assert (cpi.big_with, cpi.big_total, cpi.all_with, cpi.all_total) == (1, 1, 1, 20)
    assert not rows[5].releases_known and rows[30].releases_known


@pytest.mark.parametrize(
    "headline, macro",
    [
        ("Powell Says Fed Can Be Patient On Rate Cuts", True),
        ("Trump Announces 25% Tariffs On Imports From Mexico", True),
        ("Oil Jumps 6% After Strikes On Iran", True),
        ("Treasury Yields Surge After Hot CPI Print", True),
        ("Bristol Myers Squibb Presents New Clinical Trial Data", False),
        ("Quarterhill Q4 EPS $(0.29), Sales Miss Estimate", False),
    ],
)
def test_market_wide_headlines_are_told_from_company_news(headline, macro):
    from app.attribution.build import is_macro_headline

    assert is_macro_headline(headline) is macro
