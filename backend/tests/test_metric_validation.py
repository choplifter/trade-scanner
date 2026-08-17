from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from app.scanners.backtest import alpha_by_view, benchmark_returns_by_date, simulate_from_bars
from app.scanners.metric_validation import (
    base_rate,
    expectancy,
    implied_multiplier,
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


def _gap_pick(symbol: str, day: str, gap: float, won: bool, catalyst: bool, alpha: float = 0.0) -> dict:
    return {
        "symbol": symbol,
        "trading_date": day,
        "entry_pct_change": gap,
        "pct_change_since_entry": 1.0 if won else -1.0,
        "alpha_vs_benchmark": alpha,
        "has_catalyst": catalyst,
    }


def _calibrated_picks() -> list[dict]:
    """Non-catalyst win rate rises exactly 10pp per doubling of gap (40/50/60%
    at gaps of 1/2/4), and flagged picks win 55% against the 50% unflagged
    average -- a +5pp edge.

    So the answer is arithmetic, not approximate: half a doubling, hence
    sqrt(2). Any change that starts scaling the multiplier with the edge
    itself rather than with edge/slope breaks this.
    """
    picks = []
    for gap, win_rate in ((1.0, 0.40), (2.0, 0.50), (4.0, 0.60)):
        for i in range(200):
            picks.append(_gap_pick(f"P{gap}_{i}", "2026-01-05", gap, i < 200 * win_rate, False))
    for i in range(200):
        picks.append(_gap_pick(f"C{i}", "2026-01-05", 2.0, i < 110, True))
    return picks


def test_implied_multiplier_is_edge_over_slope_not_edge():
    result = implied_multiplier(_calibrated_picks(), bootstrap=0)
    assert result["win_rate_slope_pp_per_doubling"] == 10.0
    assert result["win_rate_edge_pp"] == 5.0
    # 2 ** (5/10), not something proportional to the 5pp edge on its own.
    assert result["win_rate_multiplier"] == round(2**0.5, 3)


def test_flat_gap_slope_yields_no_multiplier():
    """When gap predicts nothing, no multiplier expresses the edge -- dividing
    by a ~zero slope would manufacture an enormous one out of noise."""
    picks = []
    for gap in (1.0, 2.0, 4.0):
        for i in range(200):
            picks.append(_gap_pick(f"P{gap}_{i}", "2026-01-05", gap, i < 100, False))
    for i in range(200):
        picks.append(_gap_pick(f"C{i}", "2026-01-05", 2.0, i < 140, True))
    result = implied_multiplier(picks, bootstrap=0)
    assert result["win_rate_slope_pp_per_doubling"] == 0.0
    assert result["win_rate_multiplier"] is None


def test_picks_above_the_inversion_are_excluded_not_fitted():
    """Past GAP_INVERSION_PCT the gap/outcome relation flips sign. Those picks
    are extreme enough to set an unweighted fit's sign by themselves, so they
    must not reach the regression at all."""
    picks = _calibrated_picks()
    baseline = implied_multiplier(picks, bootstrap=0)
    for i in range(300):
        picks.append(_gap_pick(f"X{i}", "2026-01-06", 25.0, False, i % 2 == 0, alpha=-40.0))
    result = implied_multiplier(picks, bootstrap=0)
    assert result["picks_excluded_above_limit"] == 300
    assert result["win_rate_multiplier"] == baseline["win_rate_multiplier"]
    assert result["win_rate_slope_pp_per_doubling"] == baseline["win_rate_slope_pp_per_doubling"]


def test_bootstrap_resamples_symbol_days_so_replication_cannot_narrow_it():
    """The methodological guard. An intraday replay emits one pick per
    qualifying 5-minute bar, so a single symbol-day arrives ~78 times over
    with the same catalyst flag and near-identical outcome. Resampling picks
    would read that as 78 independent observations and shrink the interval by
    roughly sqrt(78); resampling symbol-days leaves it untouched.

    Replicating every pick within its own symbol-day is therefore a no-op
    here, exactly -- same bands, same weights, same draws.
    """
    picks = [_gap_pick(f"S{i}", f"2026-01-{5 + i % 20:02d}", gap, i < 200 * rate, flag)
             for gap, rate, flag in ((1.0, 0.40, False), (2.0, 0.50, False),
                                     (4.0, 0.60, False), (2.0, 0.55, True))
             for i in range(200)]
    once = implied_multiplier(picks, bootstrap=60)
    replicated = implied_multiplier([p for p in picks for _ in range(20)], bootstrap=60)

    assert replicated["picks_used"] == once["picks_used"] * 20
    assert replicated["symbol_days"] == once["symbol_days"]
    assert replicated["bootstrap"] == once["bootstrap"]
