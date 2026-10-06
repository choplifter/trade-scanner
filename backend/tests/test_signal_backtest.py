"""The daily-signal options backtest: signals, the regime's pick of
structure, sizing and the risk caps, and a walk on a made-up tape."""

import math
from datetime import date, timedelta

import pytest

from app.options.payoff import bs_greeks
from app.options.signal_backtest import (
    MAX_TOTAL_RISK,
    RISK_PER_TRADE,
    build_spread,
    group_of,
    regime,
    signal,
    signal_exit,
    summarize,
    walk,
)

TODAY = date(2026, 6, 1)


def _uptrend(n: int = 260, start: float = 100.0, drift: float = 0.001) -> list[float]:
    return [start * math.exp(drift * i + 0.004 * math.sin(i)) for i in range(n)]


def test_a_breakout_above_the_20_day_high_in_an_uptrend_is_bullish():
    closes = _uptrend()
    closes.append(max(closes[-20:]) * 1.01)
    assert signal("trend", closes) == "bull"
    # The same breakout below a falling 200-day average is no trade.
    falling = list(reversed(_uptrend()))
    falling.append(max(falling[-20:]) * 1.01)
    assert signal("trend", falling) is None


def test_a_dip_under_the_lower_band_in_an_uptrend_is_bullish_and_ends_at_the_middle():
    closes = _uptrend()
    closes.append(closes[-1] * 0.95)
    assert signal("band", closes) == "bull"
    assert signal_exit("band", "bull", closes) is False
    closes.append(sum(closes[-20:]) / 20 * 1.01)
    assert signal_exit("band", "bull", closes) is True


def test_the_regime_needs_one_side_and_not_the_other():
    assert regime(0.30, 0.20, []) == "rich"  # IV/RV 1.5
    assert regime(0.18, 0.20, []) == "cheap"  # 0.9
    assert regime(0.21, 0.20, []) is None  # 1.05: neither
    history = [0.10 + 0.001 * i for i in range(100)]
    assert regime(0.21, 0.20, history) == "rich", "IV/RV says nothing, a 100 % rank does"


def test_a_credit_spread_sells_the_30_delta_and_risks_one_percent():
    spread = build_spread("SPY", "bull", "credit", 500.0, 0.20, TODAY, 100_000.0, "trend")
    assert spread.option == "put" and spread.short_strike < 500 and spread.long_strike < spread.short_strike
    t = (spread.expiry - TODAY).days / 365
    delta, _, _ = bs_greeks("put", 500.0, spread.short_strike, t, 0.20)
    assert abs(delta) == pytest.approx(0.30, abs=0.04)
    assert spread.max_loss <= RISK_PER_TRADE * 100_000.0
    # A debit spread buys at the money and risks what it pays.
    debit = build_spread("SPY", "bear", "debit", 500.0, 0.20, TODAY, 100_000.0, "band")
    assert debit.option == "put" and debit.long_strike == 500.0 and debit.short_strike < 500
    assert debit.max_loss == pytest.approx(debit.entry * 100 * debit.contracts, abs=0.01)


def test_correlated_names_share_a_group():
    assert group_of("SPY") == group_of("QQQ") == group_of("IWM")
    assert group_of("TLT") != group_of("SPY") and group_of("AAPL") == "AAPL"


def _tape(symbols, days=260 + 120, iv=0.30):
    start = TODAY - timedelta(days=days)
    closes, ivs = {}, {}
    for j, s in enumerate(symbols):
        prices = _uptrend(days, start=100.0 + 10 * j, drift=0.002)
        closes[s] = [(start + timedelta(days=i), p) for i, p in enumerate(prices)]
        ivs[s] = {start + timedelta(days=i): iv for i in range(260, days)}
    return closes, ivs


def test_a_walk_on_a_rising_tape_keeps_inside_its_risk_caps():
    closes, ivs = _tape(["SPY", "QQQ", "TLT", "GLD", "USO", "AAPL", "MSFT"])
    result = walk("trend", closes, ivs)
    summary = summarize(result, 100_000.0)
    assert summary["trades"] > 0
    # IV 30 % against a ~6 % realised tape: rich, so every entry sells premium.
    assert set(summary["by_kind"]) == {"credit"}
    # Never two open in one group (SPY and QQQ), and the cap skipped some.
    opened = sorted((t["opened"], t["closed"], group_of(t["symbol"])) for t in result.trades)
    for i, (o1, c1, g1) in enumerate(opened):
        for o2, c2, g2 in opened[i + 1 :]:
            assert not (g1 == g2 and o2 < c1), "two positions open in one correlation group"
    assert all(t["max_loss"] <= RISK_PER_TRADE * 110_000 for t in result.trades)
    assert summary["max_drawdown_pct"] < 100 * MAX_TOTAL_RISK + 1


def test_the_control_sells_rich_premium_without_a_signal_and_only_inside_its_dates():
    closes, ivs = _tape(["SPY", "TLT", "GLD"])
    days = sorted(ivs["SPY"])
    start, end = days[20], days[80]
    result = walk("vol", closes, ivs, start=start, end=end)
    assert result.trades or result.curve
    assert all(t["kind"] == "credit" and t["option"] == "put" for t in result.trades)
    assert all(start.isoformat() <= d <= end.isoformat() for d, _ in result.curve)
    assert not any(t["reason"] == "signal" for t in result.trades), "the control has no signal to exit on"
    assert signal("vol", [100.0] * 30) == "bull" and signal_exit("vol", "bull", [100.0] * 30) is False


def test_the_market_filter_reads_spy_trend_and_volatility_spikes():
    from app.options.signal_backtest import risk_off

    up = _uptrend(260)
    assert risk_off(up, [0.15] * 30) is None
    assert "200-day" in risk_off(list(reversed(up)), [0.15] * 30)
    assert "spiking" in risk_off(up, [0.15] * 25 + [0.20])


def test_a_filtered_walk_sells_nothing_while_the_market_is_risk_off():
    closes, ivs = _tape(["SPY", "TLT", "GLD"])
    falling = list(reversed([c for _, c in closes["SPY"]]))
    closes["SPY"] = [(d, c) for (d, _), c in zip(closes["SPY"], falling)]
    filtered = walk("vol", closes, ivs, market_filter=True)
    assert not filtered.trades and filtered.skipped.get("market below its 200-day average")
    assert walk("vol", closes, ivs).trades, "unfiltered, the same tape trades"


def test_a_wider_minimum_bid_ask_costs_a_cheap_spread_more_of_its_credit():
    """HYG-like: cheap legs, where the floor -- not the 3 % -- sets the cost."""
    tight = build_spread("HYG", "bull", "credit", 80.0, 0.07, TODAY, 100_000.0, "vol", min_spread=0.02)
    real = build_spread("HYG", "bull", "credit", 80.0, 0.07, TODAY, 100_000.0, "vol", min_spread=0.05)
    assert real.entry == pytest.approx(tight.entry - 0.03, abs=0.002), "two legs, 0.015 more half-spread each"
