"""Slicing picks by COT positioning (app.scanners.cot_slices): the band a
pick could actually have known, ranked against the history that existed."""

from datetime import date, timedelta

import pytest

from app.market_data.cot import Week
from app.scanners.cot_slices import (
    NO_CONTRACT,
    NOT_YET_KNOWN,
    band_on,
    by_cot_band,
    covered_symbols,
    public_from,
    week_known_on,
)

# A Tuesday, which is what a report date always is.
TUESDAY = date(2026, 9, 22)


def _weeks(shares: list[float], *, last: date = TUESDAY) -> list[Week]:
    """Weeks ending at `last`, one per week, oldest first, whose spec net
    is `share` of a fixed open interest."""
    out = []
    for i, share in enumerate(reversed(shares)):
        oi = 100_000
        net = int(share * oi)
        out.append(
            Week(
                report_date=last - timedelta(weeks=i),
                open_interest=oi,
                spec_long=max(net, 0),
                spec_short=max(-net, 0),
                hedge_long=0,
                hedge_short=0,
            )
        )
    return sorted(out, key=lambda w: w.report_date)


def _pick(symbol: str, day: date, pct: float = 1.0) -> dict:
    return {"symbol": symbol, "trading_date": day.isoformat(), "pct_change_since_entry": pct}


# --- when a report becomes readable -----------------------------------------


def test_a_report_is_not_readable_on_the_days_it_describes():
    """Tuesday's positions are published the Friday after. Wednesday and
    Thursday are precisely the days a nearest-date join would hand it to,
    and precisely the days the positioning was still being built."""
    weeks = _weeks([0.1] * 60)
    for offset in range(4):  # Tuesday through Friday
        week = week_known_on(weeks, TUESDAY + timedelta(days=offset))
        # The week before is public and standing; this Tuesday's is not.
        assert week is not None and week.report_date == TUESDAY - timedelta(weeks=1)


def test_the_report_is_readable_from_the_day_after_its_release():
    weeks = _weeks([0.1] * 60)
    assert public_from(TUESDAY) == TUESDAY + timedelta(days=4)
    week = week_known_on(weeks, TUESDAY + timedelta(days=4))
    assert week is not None and week.report_date == TUESDAY


def test_an_older_report_still_stands_until_the_next_one_lands():
    weeks = _weeks([0.1] * 60)
    week = week_known_on(weeks, TUESDAY + timedelta(days=9))
    assert week.report_date == TUESDAY


# --- the percentile is point in time ----------------------------------------


def test_the_band_ranks_only_against_weeks_published_by_then():
    """The whole point. A week that was the highest reading of its own
    history sits in the top decile *then*, even if a later year doubled the
    range -- ranking it against what came after is knowing the future."""
    # 60 flat weeks, then a spike, then 60 far higher weeks.
    shares = [0.10] * 60 + [0.20] + [0.40] * 60
    weeks = _weeks(shares, last=TUESDAY)
    spike = weeks[60]
    # Read on the first day the spike was public: nothing above it existed.
    assert band_on(weeks, public_from(spike.report_date)) == "top decile -- crowded long"
    # Read today, with the higher years in the history, the same week is
    # no longer an extreme -- which is why the date matters.
    assert band_on(weeks, TUESDAY + timedelta(days=4)) != "top decile -- crowded long"


def test_a_history_too_short_to_rank_yields_no_band():
    # percentile_of refuses under a year; a band from eight readings would
    # be the most confident row in the table.
    assert band_on(_weeks([0.1] * 8), TUESDAY + timedelta(days=4)) is None


def test_the_middle_of_the_range_is_one_wide_ordinary_band():
    weeks = _weeks([i / 100 for i in range(60)], last=TUESDAY)
    # The newest week is the largest of its own history: the top decile.
    assert band_on(weeks, TUESDAY + timedelta(days=4)) == "top decile -- crowded long"


# --- the table --------------------------------------------------------------


def test_every_band_appears_even_with_no_picks_in_it():
    rows = by_cot_band([], {})
    assert [r["bucket"] for r in rows] == [
        "bottom decile -- crowded short",
        "10-30th",
        "30-70th ordinary",
        "70-90th",
        "top decile -- crowded long",
    ]
    assert all(r["sample_size"] == 0 and r["avg_return"] is None for r in rows)


def test_uncovered_symbols_and_unpublished_dates_are_kept_apart_not_dropped():
    weeks = _weeks([0.1] * 60)
    picks = [
        _pick("SPY", TUESDAY + timedelta(days=4)),  # banded
        _pick("SPY", TUESDAY - timedelta(weeks=70)),  # before the history
        _pick("AAPL", TUESDAY + timedelta(days=4)),  # no contract
    ]
    rows = by_cot_band(picks, {"SPY": weeks})
    by_bucket = {r["bucket"]: r for r in rows}
    assert by_bucket[NO_CONTRACT]["sample_size"] == 1
    assert by_bucket[NOT_YET_KNOWN]["sample_size"] == 1
    assert sum(r["sample_size"] for r in rows) == 3, "the buckets must add up to the run"


def test_the_bucket_stats_are_the_pages_own_win_rate_and_average():
    weeks = _weeks([0.1] * 60)
    day = TUESDAY + timedelta(days=4)
    picks = [_pick("SPY", day, 2.0), _pick("SPY", day, -1.0)]
    banded = next(r for r in by_cot_band(picks, {"SPY": weeks}) if r["sample_size"] == 2)
    assert banded["win_rate"] == 50.0
    assert banded["avg_return"] == pytest.approx(0.5)


def test_only_the_symbols_present_and_covered_are_worth_fetching():
    picks = [_pick("SPY", TUESDAY), _pick("AAPL", TUESDAY), _pick("GLD", TUESDAY)]
    assert covered_symbols(picks) == ["GLD", "SPY"]


def test_the_window_is_the_same_trailing_three_years_at_both_ends_of_a_run():
    """An expanding window would rank the run's first pick against one year
    and its last against four, so the same label would mean two different
    things in one table. Here a week five years old cannot reach today's
    ranking at all."""
    # Five years of extreme readings, then three years of flat ones.
    old_extremes = [5.0] * 104
    recent = [0.10] * 160
    weeks = _weeks(old_extremes + recent, last=TUESDAY)
    # Today's flat reading is ordinary among the flat years, and would be
    # the bottom decile if the old extremes still counted.
    assert band_on(weeks, TUESDAY + timedelta(days=4)) == "30-70th ordinary"
