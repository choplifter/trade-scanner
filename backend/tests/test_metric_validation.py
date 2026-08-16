from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from app.scanners.backtest import alpha_by_view, benchmark_returns_by_date, simulate_from_bars
from app.scanners.metric_validation import (
    base_rate,
    expectancy,
    overfitting_gauge,
    search_conditions,
    split_by_date,
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
        if self.open is None:
            self.open = self.close
        if self.high is None:
            self.high = self.close
        if self.low is None:
            self.low = self.close


def _daily_bars(start: date, closes: list[float], volume: float = 100_000.0) -> list[_Bar]:
    return [
        _Bar(
            close=c,
            volume=volume,
            timestamp=datetime.combine(start + timedelta(days=i), datetime.min.time(), tzinfo=timezone.utc),
        )
        for i, c in enumerate(closes)
    ]


def _pick(view="gainers", trading_date="2026-01-01", ret=1.0, gap=10.0, rvol=1.0,
          dollars=1e7, shaved=False, alpha=None):
    return {
        "view": view,
        "trading_date": trading_date,
        "pct_change_since_entry": ret,
        "entry_pct_change": gap,
        "entry_rvol": rvol,
        "entry_dollar_volume": dollars,
        "is_shaved_top": shaved,
        "alpha_vs_benchmark": alpha,
    }


# --- benchmark / alpha --------------------------------------------------------


def test_benchmark_returns_measured_over_the_same_horizon():
    bars = _daily_bars(date(2026, 1, 1), [100.0, 110.0, 121.0])
    returns = benchmark_returns_by_date(bars, horizon_days=1)

    assert round(returns[date(2026, 1, 1)], 2) == 10.0
    assert round(returns[date(2026, 1, 2)], 2) == 10.0
    # The final bar has no forward window, so it isn't a usable entry date.
    assert date(2026, 1, 3) not in returns


def test_alpha_is_the_pick_minus_the_benchmark():
    closes = [100.0] * 20 + [110.0, 115.0]
    bars = _daily_bars(date(2026, 1, 1), closes)
    entry_date = date(2026, 1, 21)
    # Benchmark rose 2% over the same window.
    picks = simulate_from_bars({"AAA": bars}, 0.0, 1, {entry_date: 2.0})

    gainer = next(p for p in picks if p["view"] == "gainers")
    expected = (115.0 - 110.0) / 110.0 * 100
    assert round(gainer["pct_change_since_entry"], 2) == round(expected, 2)
    assert gainer["benchmark_pct_change_since_entry"] == 2.0
    assert round(gainer["alpha_vs_benchmark"], 2) == round(expected - 2.0, 2)


def test_a_pick_can_be_a_raw_win_and_an_alpha_loss():
    # Up 4.8% while the benchmark was up 10% -- positive, and still worse
    # than doing nothing. The raw win rate cannot see this.
    closes = [100.0] * 20 + [110.0, 115.0]
    bars = _daily_bars(date(2026, 1, 1), closes)
    picks = simulate_from_bars({"AAA": bars}, 0.0, 1, {date(2026, 1, 21): 10.0})

    gainer = next(p for p in picks if p["view"] == "gainers")
    assert gainer["pct_change_since_entry"] > 0
    assert gainer["alpha_vs_benchmark"] < 0


def test_alpha_is_none_when_the_benchmark_lacks_that_date():
    closes = [100.0] * 20 + [110.0, 115.0]
    bars = _daily_bars(date(2026, 1, 1), closes)

    for benchmark in ({}, None):
        picks = simulate_from_bars({"AAA": bars}, 0.0, 1, benchmark)
        gainer = next(p for p in picks if p["view"] == "gainers")
        # None, not 0.0 -- "unmeasurable" must stay distinct from "exactly
        # matched the market".
        assert gainer["alpha_vs_benchmark"] is None
        assert gainer["benchmark_pct_change_since_entry"] is None


def test_alpha_by_view_separates_raw_wins_from_benchmark_wins():
    picks = [
        _pick(ret=1.0, alpha=-1.0),  # up, but behind the market
        _pick(ret=2.0, alpha=1.0),
        _pick(view="losers", ret=-1.0, alpha=-2.0),
    ]
    rows = {r["view"]: r for r in alpha_by_view(picks)}

    assert rows["gainers"]["win_rate"] == 100.0
    assert rows["gainers"]["alpha_win_rate"] == 50.0
    assert rows["losers"]["win_rate"] == 0.0


def test_alpha_by_view_ignores_picks_without_a_benchmark():
    picks = [_pick(ret=1.0, alpha=1.0), _pick(ret=1.0, alpha=None)]
    row = alpha_by_view(picks)[0]

    assert row["sample_size"] == 2
    assert row["sample_size_with_benchmark"] == 1
    assert row["alpha_win_rate"] == 100.0


# --- expectancy ---------------------------------------------------------------


def test_expectancy_reports_payoff_alongside_win_rate():
    # 75% win rate that loses money: wins of 1, a loss of 9.
    picks = [_pick(ret=1.0), _pick(ret=1.0), _pick(ret=1.0), _pick(ret=-9.0)]
    stats = expectancy(picks)

    assert stats["win_rate"] == 75.0
    assert stats["avg_win"] == 1.0
    assert stats["avg_loss"] == -9.0
    # Below 1.0 -- the number a win rate alone would have hidden.
    assert stats["payoff_ratio"] < 1.0
    assert stats["mean"] < 0


def test_expectancy_skips_none_values():
    assert expectancy([_pick(alpha=1.0), _pick(alpha=None)], key="alpha_vs_benchmark")["sample_size"] == 1


def test_expectancy_is_none_without_data():
    assert expectancy([]) is None


def test_base_rate_covers_every_symbol_day_not_just_ranked_ones():
    # Three bars -> two measurable forward returns per symbol, regardless of
    # whether anything would have ranked.
    bars = {"AAA": _daily_bars(date(2026, 1, 1), [100.0, 110.0, 99.0])}
    stats = base_rate(bars)

    assert stats["sample_size"] == 2
    assert stats["win_rate"] == 50.0


def test_base_rate_applies_the_same_tradability_filter_as_ranking():
    # $1M/day of dollar volume vs a $100M floor: the scanner could never have
    # ranked these days, so they must not sit in the population it's compared
    # against either.
    bars = {"AAA": _daily_bars(date(2026, 1, 1), [100.0, 110.0, 99.0], volume=10_000.0)}

    assert base_rate(bars, 1, min_dollar_volume=0.0)["sample_size"] == 2
    assert base_rate(bars, 1, min_dollar_volume=100_000_000.0) is None


# --- split / search -----------------------------------------------------------


def test_split_is_chronological_and_shares_no_date():
    picks = [_pick(trading_date=f"2026-01-{day:02d}") for day in range(1, 11)]
    in_sample, out_of_sample = split_by_date(picks, oos_fraction=0.3)

    assert in_sample and out_of_sample
    assert not ({p["trading_date"] for p in in_sample} & {p["trading_date"] for p in out_of_sample})
    # Every held-out date is strictly later -- a random split would let one
    # day's correlated picks sit on both sides.
    assert max(p["trading_date"] for p in in_sample) < min(p["trading_date"] for p in out_of_sample)


def test_search_respects_the_sample_floor():
    picks = [_pick(trading_date="2026-01-01") for _ in range(10)]
    assert search_conditions(picks, [], min_sample=100) == []
    assert search_conditions(picks, [], min_sample=5) != []


def test_search_reports_out_of_sample_for_in_sample_rules():
    in_sample = [_pick(trading_date="2026-01-01", ret=1.0) for _ in range(120)]
    out_of_sample = [_pick(trading_date="2026-02-01", ret=-1.0) for _ in range(60)]
    results = search_conditions(in_sample, out_of_sample, min_sample=100)

    top = results[0]
    # A rule that looks perfect in-sample and fails completely out -- exactly
    # the case the split exists to expose.
    assert top["in_sample"]["win_rate"] == 100.0
    assert top["out_of_sample"]["win_rate"] == 0.0


def test_overfitting_gauge_measures_the_in_to_out_drop():
    results = [
        {
            "label": "x",
            "in_sample": {"win_rate": 70.0},
            "out_of_sample": {"win_rate": 50.0, "sample_size": 100},
        },
        {
            "label": "y",
            "in_sample": {"win_rate": 60.0},
            "out_of_sample": {"win_rate": 60.0, "sample_size": 100},
        },
    ]
    gauge = overfitting_gauge(results)

    assert gauge["rules_compared"] == 2
    assert gauge["mean_win_rate_drop_pp"] == 10.0
    assert gauge["rules_that_held_up"] == 1


def test_overfitting_gauge_ignores_thin_out_of_sample_rules():
    results = [
        {"label": "x", "in_sample": {"win_rate": 90.0}, "out_of_sample": {"win_rate": 10.0, "sample_size": 3}}
    ]
    assert overfitting_gauge(results) is None
