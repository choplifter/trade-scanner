from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from app.scanners.backtest import (
    active_views,
    look_ahead_filters,
    fade_risk_by_view,
    simulate_from_bars,
    unsupported_filters,
)


@dataclass
class _Bar:
    close: float
    volume: float
    timestamp: datetime
    open: float = None  # type: ignore[assignment]
    high: float = None  # type: ignore[assignment]
    low: float = None  # type: ignore[assignment]

    def __post_init__(self):
        # Defaults to a zero-range bar (open=high=low=close) for tests that
        # don't care about wick shape, so existing fixtures don't need to
        # specify OHLC explicitly.
        if self.open is None:
            self.open = self.close
        if self.high is None:
            self.high = self.close
        if self.low is None:
            self.low = self.close


def _daily_bars(start: date, closes: list[float], volume: float = 100_000.0) -> list[_Bar]:
    """One bar per calendar day starting at `start` (no weekend-skipping --
    the date-keyed grouping under test doesn't care about real trading
    calendars, just that each bar carries a distinct date). Zero-range
    (open=high=low=close) -- fine for tests that don't care about wick
    shape; see _shaved_top_bar/_non_shaved_top_bar for ones that do.
    """
    return [
        _Bar(close=c, volume=volume, timestamp=datetime.combine(start + timedelta(days=i), datetime.min.time(), tzinfo=timezone.utc))
        for i, c in enumerate(closes)
    ]


def test_simulate_from_bars_empty_for_short_history():
    # Needs _WARMUP_TRADING_DAYS (20) + horizon_days (1) + 1 bars minimum.
    bars = _daily_bars(date(2026, 1, 1), [100.0] * 10)
    picks = simulate_from_bars({"AAA": bars}, min_dollar_volume=0.0, horizon_days=1)
    assert picks == []


def test_simulate_from_bars_basic_gainer_win():
    # 20 flat warmup days, then a +10% gap day, then a further gain the
    # next day (a "win" by the pct_change_since_entry > 0 convention).
    closes = [100.0] * 20 + [110.0, 115.0]
    bars = _daily_bars(date(2026, 1, 1), closes)
    picks = simulate_from_bars({"AAA": bars}, min_dollar_volume=0.0, horizon_days=1)

    # Also picked up by most_active (direction-agnostic) -- see
    # test_simulate_from_bars_can_rank_one_symbol_in_two_views_on_one_date.
    gainers = [p for p in picks if p["view"] == "gainers"]
    assert len(gainers) == 1
    pick = gainers[0]
    assert pick["symbol"] == "AAA"
    assert round(pick["entry_pct_change"], 2) == 10.0
    assert round(pick["pct_change_since_entry"], 2) == round((115.0 - 110.0) / 110.0 * 100, 2)


def test_simulate_from_bars_basic_loser():
    closes = [100.0] * 20 + [90.0, 85.0]
    bars = _daily_bars(date(2026, 1, 1), closes)
    picks = simulate_from_bars({"AAA": bars}, min_dollar_volume=0.0, horizon_days=1)

    losers = [p for p in picks if p["view"] == "losers"]
    assert len(losers) == 1
    assert round(losers[0]["entry_pct_change"], 2) == -10.0


def test_simulate_from_bars_min_dollar_volume_filters_out_thin_symbols():
    closes = [100.0] * 20 + [110.0, 115.0]
    bars = _daily_bars(date(2026, 1, 1), closes, volume=10.0)  # $1,100 dollar volume on the entry day
    picks = simulate_from_bars({"AAA": bars}, min_dollar_volume=1_000_000.0, horizon_days=1)
    assert picks == []


def test_simulate_from_bars_groups_by_calendar_date_not_list_index():
    # AAA has a normal, unbroken run of daily bars. BBB is "new" and only
    # starts partway through AAA's window -- so BBB's list index 0 lines up
    # with a much later calendar date than AAA's index 0. If rows were
    # grouped by list index instead of date, BBB's early entries would get
    # cross-ranked against AAA's much-earlier days instead of never
    # existing for those dates.
    start = date(2026, 1, 1)
    aaa_closes = [100.0] * 20 + [110.0, 111.0]  # entry on day 20 (2026-01-21)
    aaa = _daily_bars(start, aaa_closes)

    # BBB's series starts 20 calendar days later, so its list index 20
    # (its own entry day) actually falls on a *different* calendar date
    # than AAA's index 20.
    bbb_start = start + timedelta(days=20)
    bbb_closes = [50.0] * 20 + [55.0, 56.0]
    bbb = _daily_bars(bbb_start, bbb_closes)

    picks = simulate_from_bars({"AAA": aaa, "BBB": bbb}, min_dollar_volume=0.0, horizon_days=1)

    aaa_pick = next(p for p in picks if p["symbol"] == "AAA" and p["view"] == "gainers")
    bbb_pick = next(p for p in picks if p["symbol"] == "BBB" and p["view"] == "gainers")
    # Each symbol's pick is dated to its own actual entry bar, not smeared
    # onto a shared index-based date.
    assert aaa_pick["trading_date"] == (start + timedelta(days=20)).isoformat()
    assert bbb_pick["trading_date"] == (bbb_start + timedelta(days=20)).isoformat()
    assert aaa_pick["trading_date"] != bbb_pick["trading_date"]


def test_simulate_from_bars_flags_shaved_top_entry_candle():
    closes = [100.0] * 20 + [110.0, 111.0]
    bars = _daily_bars(date(2026, 1, 1), closes)
    # Override the entry day (index 20) with a real candle shape: gapped up
    # to 105, dipped to 95 intraday, then rallied to close right at the
    # day's high -- a shaved top (upper wick ~0) despite a big lower wick.
    bars[20] = _Bar(
        close=110.0, volume=100_000.0, timestamp=bars[20].timestamp, open=105.0, high=110.0, low=95.0
    )
    picks = simulate_from_bars({"AAA": bars}, min_dollar_volume=0.0, horizon_days=1)

    assert picks
    assert all(p["is_shaved_top"] is True for p in picks)


def test_simulate_from_bars_flags_non_shaved_top_entry_candle():
    closes = [100.0] * 20 + [110.0, 111.0]
    bars = _daily_bars(date(2026, 1, 1), closes)
    # Closed well off the day's high -- not a shaved top.
    bars[20] = _Bar(
        close=110.0, volume=100_000.0, timestamp=bars[20].timestamp, open=100.0, high=115.0, low=99.0
    )
    picks = simulate_from_bars({"AAA": bars}, min_dollar_volume=0.0, horizon_days=1)

    assert picks
    assert all(p["is_shaved_top"] is False for p in picks)


def test_simulate_from_bars_replays_most_active():
    # Dead flat the whole way -- never a gainer (needs pct > 0) and never a
    # loser (needs pct < 0), so the only view that can pick this up is
    # most_active, which is direction-agnostic.
    closes = [100.0] * 22
    bars = _daily_bars(date(2026, 1, 1), closes)
    picks = simulate_from_bars({"AAA": bars}, min_dollar_volume=0.0, horizon_days=1)

    assert [p["view"] for p in picks] == ["most_active"]
    assert picks[0]["entry_pct_change"] == 0.0


def test_simulate_from_bars_ranks_most_active_by_dollar_volume_not_share_volume():
    # Backtest-side twin of test_engine_ranking.py's
    # test_most_active_ranks_by_dollar_volume_not_share_volume: PENNY trades
    # 20x the shares but a 100th of the price, so ranking on raw share
    # volume would put it first and ranking on dollar volume puts it last.
    start = date(2026, 1, 1)
    penny = _daily_bars(start, [1.0] * 22, volume=1_000_000.0)  # $1M
    pricey = _daily_bars(start, [100.0] * 22, volume=50_000.0)  # $5M

    picks = simulate_from_bars(
        {"PENNY": penny, "PRICEY": pricey}, min_dollar_volume=0.0, horizon_days=1
    )

    # Picks are appended in ranked order within a (date, view) cohort.
    assert [p["symbol"] for p in picks if p["view"] == "most_active"] == ["PRICEY", "PENNY"]


def test_simulate_from_bars_records_entry_dollar_volume():
    closes = [100.0] * 20 + [110.0, 115.0]
    bars = _daily_bars(date(2026, 1, 1), closes, volume=2_000.0)
    picks = simulate_from_bars({"AAA": bars}, min_dollar_volume=0.0, horizon_days=1)

    gainer = next(p for p in picks if p["view"] == "gainers")
    # The entry day's own close (110.0), not the warmup price.
    assert gainer["entry_dollar_volume"] == 2_000.0 * 110.0


def test_simulate_from_bars_can_rank_one_symbol_in_two_views_on_one_date():
    # A big gapper is also, on this one-symbol universe, the most active
    # name -- so it legitimately appears twice for the same date under
    # different views, exactly as the live history store records it.
    closes = [100.0] * 20 + [110.0, 115.0]
    bars = _daily_bars(date(2026, 1, 1), closes)
    picks = simulate_from_bars({"AAA": bars}, min_dollar_volume=0.0, horizon_days=1)

    assert sorted(p["view"] for p in picks) == ["gainers", "most_active"]
    assert len({p["trading_date"] for p in picks}) == 1


def test_fade_risk_is_broken_down_per_view_never_pooled():
    # One high-RVOL winner in gainers and one high-RVOL loser in losers.
    # Pooled, they cancel to a 50% win rate; per view each is 100% and 0%.
    picks = [
        {"view": "gainers", "entry_rvol": 20.0, "pct_change_since_entry": 5.0},
        {"view": "losers", "entry_rvol": 20.0, "pct_change_since_entry": -5.0},
        {"view": "gainers", "entry_rvol": 1.0, "pct_change_since_entry": 1.0},
    ]
    rows = fade_risk_by_view(picks)

    by_view = {r["view"]: r for r in rows}
    assert list(by_view) == ["gainers", "losers", "most_active"]
    assert by_view["gainers"]["rvol_above_threshold"]["win_rate"] == 100.0
    assert by_view["losers"]["rvol_above_threshold"]["win_rate"] == 0.0
    assert by_view["gainers"]["rvol_at_or_below_threshold"]["sample_size"] == 1
    # Present but empty rather than missing, so a consumer can render every
    # view's row without special-casing.
    assert by_view["most_active"]["rvol_above_threshold"]["sample_size"] == 0
    # Two picks above the threshold is nowhere near the noise floor.
    assert by_view["gainers"]["sufficient_sample"] is False


def test_simulate_from_bars_respects_horizon_days():
    closes = [100.0] * 20 + [110.0, 111.0, 112.0, 120.0]
    bars = _daily_bars(date(2026, 1, 1), closes)
    picks = simulate_from_bars({"AAA": bars}, min_dollar_volume=0.0, horizon_days=3)

    assert picks
    # Entry day 20 (close 110.0), horizon 3 trading days later is index 23
    # (close 120.0) -- the forward horizon is per symbol, so every view's
    # pick for that day measures the same outcome.
    expected = round((120.0 - 110.0) / 110.0 * 100, 2)
    assert all(round(p["pct_change_since_entry"], 2) == expected for p in picks)


# --- screen backtesting -------------------------------------------------------


def _screen(**kwargs):
    from app.scanners import screener

    return screener.Screen(**kwargs)


def test_unsupported_filters_names_what_daily_bars_cannot_reconstruct():
    from app.scanners import screener

    screen = _screen(
        filters=[
            screener.Filter(field="pct_change", op="gt", value=5),
            screener.Filter(field="float_shares", op="lt", value=20_000_000),
            screener.Filter(field="rvol_1h", op="gt", value=2),
        ],
        sort_by="pct_change",
    )
    # float_shares is now supported but look-ahead (see LOOK_AHEAD_FIELDS);
    # only the intraday-only field is genuinely unreplayable on daily bars.
    assert unsupported_filters(screen) == ["rvol_1h"]
    assert look_ahead_filters(screen) == ["float_shares"]


def test_unsupported_filters_checks_the_sort_key_too():
    assert unsupported_filters(_screen(sort_by="spread_pct")) == ["spread_pct"]
    assert unsupported_filters(_screen(sort_by="rank_score")) == []


def test_a_fully_supported_screen_has_nothing_unsupported():
    from app.scanners import screener

    screen = _screen(
        filters=[
            screener.Filter(field="rvol", op="gt", value=2),
            screener.Filter(field="is_hod", op="is_true"),
            screener.Filter(field="last_price", op="between", value=1, value2=50),
        ],
        sort_by="rank_score",
    )
    assert unsupported_filters(screen) == []


def test_screen_replaces_the_three_views_rather_than_joining_them():
    from app.scanners import screener

    closes = [100.0] * 20 + [110.0, 115.0]
    bars = _daily_bars(date(2026, 1, 1), closes)
    picks = simulate_from_bars(
        {"AAA": bars}, 0.0, 1, None,
        _screen(filters=[screener.Filter(field="pct_change", op="gt", value=5)]),
    )

    # One "screen" view -- mixing it with gainers/most_active would put
    # unrelated picks in the same buckets.
    assert [p["view"] for p in picks] == ["screen"]


def test_screen_filters_actually_exclude_rows():
    from app.scanners import screener

    up = _daily_bars(date(2026, 1, 1), [100.0] * 20 + [110.0, 115.0])
    flat = _daily_bars(date(2026, 1, 1), [50.0] * 22)

    picks = simulate_from_bars(
        {"UP": up, "FLAT": flat}, 0.0, 1, None,
        _screen(filters=[screener.Filter(field="pct_change", op="gt", value=5)]),
    )
    assert {p["symbol"] for p in picks} == {"UP"}


def test_screen_can_filter_on_fields_only_daily_bars_provide():
    from app.scanners import screener

    # Closes at its high on the entry day -> is_hod true.
    bars = _daily_bars(date(2026, 1, 1), [100.0] * 20 + [110.0, 115.0])
    bars[20] = _Bar(close=110.0, volume=100_000.0, timestamp=bars[20].timestamp,
                    open=105.0, high=110.0, low=95.0)

    hod = simulate_from_bars({"AAA": bars}, 0.0, 1, None,
                             _screen(filters=[screener.Filter(field="is_hod", op="is_true")]))
    not_hod = simulate_from_bars({"AAA": bars}, 0.0, 1, None,
                                 _screen(filters=[screener.Filter(field="is_lod", op="is_true")]))
    assert len(hod) == 1
    assert not_hod == []


def test_active_views_switches_with_the_screen():
    assert active_views([], None) == ("gainers", "losers", "most_active")
    assert active_views([], _screen()) == ("screen",)
