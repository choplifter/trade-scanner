from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.core.config import Settings
from app.scanners.momentum_backtest import simulate_momentum_alerts, sweep_momentum_params


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


_BASE = datetime(2026, 8, 1, 14, 30, tzinfo=timezone.utc)
_THRESHOLD = Settings().alarm_momentum_pct_threshold  # 5.0 by default


def _minute_bars(closes: list[float], start: datetime = _BASE) -> list[_Bar]:
    """One flat (zero-wick) bar per minute -- fine for tests that only
    care about the 15m% magnitude, not candle shape.
    """
    return [_Bar(close=c, volume=100_000.0, timestamp=start + timedelta(minutes=i)) for i, c in enumerate(closes)]


def test_simulate_momentum_alerts_no_picks_when_flat():
    bars = {"AAA": _minute_bars([100.0] * 40)}
    picks = simulate_momentum_alerts(bars, threshold=_THRESHOLD, horizon_minutes=15)
    assert picks == []


def test_simulate_momentum_alerts_threshold_only_without_shape_confirmation():
    # 15 flat bars, then a +10% jump that closes mid-range (not shaved) --
    # crosses the threshold but the entry candle doesn't confirm.
    closes = [100.0] * 15 + [110.0] * 20
    bars = _minute_bars(closes)
    # Override the trigger bar (index 15) with a wide range closing in the middle.
    bars[15] = _Bar(close=110.0, volume=100_000.0, timestamp=bars[15].timestamp, open=105.0, high=115.0, low=100.0)

    picks = simulate_momentum_alerts({"AAA": bars}, threshold=_THRESHOLD, horizon_minutes=5)

    kinds = {p["kind"] for p in picks}
    assert "threshold_only" in kinds
    assert "full_alert" not in kinds


def test_simulate_momentum_alerts_full_alert_when_shape_confirms():
    closes = [100.0] * 15 + [110.0] * 20
    bars = _minute_bars(closes)
    # Shaved top (upper wick ~0), closes green (close > open), and the
    # sustained flat-100 run beforehand keeps the running VWAP low enough
    # that closing at 110 lands well above it -- all three confirmation
    # gates satisfied. Lower wick (low=95) is deliberately wide -- the
    # bottom side isn't evaluated at all, long-only, but keeps the fixture
    # honest about not needing a shaved bottom too.
    bars[15] = _Bar(close=110.0, volume=100_000.0, timestamp=bars[15].timestamp, open=100.0, high=110.0, low=95.0)

    picks = simulate_momentum_alerts({"AAA": bars}, threshold=_THRESHOLD, horizon_minutes=5)

    full_alert_picks = [p for p in picks if p["kind"] == "full_alert"]
    assert len(full_alert_picks) == 1
    assert full_alert_picks[0]["symbol"] == "AAA"
    assert "view" not in full_alert_picks[0]


def test_simulate_momentum_alerts_requires_green_candle():
    # Shaved top (upper wick ~0.04) but closes red (close < open) --
    # confirmation requires a green candle, not just wick shape.
    closes = [100.0] * 15 + [110.0] * 20
    bars = _minute_bars(closes)
    bars[15] = _Bar(close=110.0, volume=100_000.0, timestamp=bars[15].timestamp, open=110.5, high=110.5, low=99.0)

    picks = simulate_momentum_alerts({"AAA": bars}, threshold=_THRESHOLD, horizon_minutes=5)

    kinds = {p["kind"] for p in picks}
    assert "threshold_only" in kinds
    assert "full_alert" not in kinds


def test_simulate_momentum_alerts_requires_above_vwap():
    # A sustained higher base (200) before a deeper drop to 90 drags the
    # running session VWAP well above a move that's only recovering part
    # of that drop (90 -> 100, +11%) -- shaved top and green both confirm,
    # but the close is still well below the running VWAP, so this
    # shouldn't fire full_alert even though it clears the 15m% threshold.
    closes = [200.0] * 15 + [90.0] * 20 + [100.0] * 10
    bars = _minute_bars(closes)
    bars[35] = _Bar(close=100.0, volume=100_000.0, timestamp=bars[35].timestamp, open=95.0, high=100.0, low=90.0)

    picks = simulate_momentum_alerts({"AAA": bars}, threshold=_THRESHOLD, horizon_minutes=5)

    kinds_at_35 = {p["kind"] for p in picks if p["timestamp"] == bars[35].timestamp.isoformat()}
    assert "threshold_only" in kinds_at_35
    assert "full_alert" not in kinds_at_35


def test_simulate_momentum_alerts_ignores_downward_moves_entirely():
    # A sharp, shaved-bottom -10% move -- would satisfy the live alarm's
    # short-side check, but this backtest is long-only, so it should
    # produce nothing at all, not even a threshold_only pick.
    closes = [100.0] * 15 + [90.0] * 20
    bars = _minute_bars(closes)
    bars[15] = _Bar(close=90.0, volume=100_000.0, timestamp=bars[15].timestamp, open=100.0, high=105.0, low=90.0)

    picks = simulate_momentum_alerts({"AAA": bars}, threshold=_THRESHOLD, horizon_minutes=5)

    assert picks == []


def test_simulate_momentum_alerts_rising_edge_dedup():
    # The +10% move happens at index 15 and then simply *stays* at that
    # level for the next 10 minutes -- 15m% relative to 15 minutes back
    # stays >= threshold across several consecutive bars, all with the
    # same shaved-top entry candle shape, but only the FIRST one should
    # count as a pick, not one per bar.
    closes = [100.0] * 15 + [110.0] * 25
    bars = _minute_bars(closes)
    for i in range(15, 25):
        bars[i] = _Bar(close=110.0, volume=100_000.0, timestamp=bars[i].timestamp, open=100.0, high=110.0, low=99.0)

    picks = simulate_momentum_alerts({"AAA": bars}, threshold=_THRESHOLD, horizon_minutes=5)

    full_alert_picks = [p for p in picks if p["kind"] == "full_alert"]
    assert len(full_alert_picks) == 1


def test_simulate_momentum_alerts_outcome_uses_horizon_minutes():
    closes = [100.0] * 15 + [110.0, 110.0, 110.0, 110.0, 120.0]
    bars = _minute_bars(closes)
    bars[15] = _Bar(close=110.0, volume=100_000.0, timestamp=bars[15].timestamp, open=100.0, high=110.0, low=99.0)

    picks = simulate_momentum_alerts({"AAA": bars}, threshold=_THRESHOLD, horizon_minutes=4)

    full_alert_picks = [p for p in picks if p["kind"] == "full_alert"]
    assert len(full_alert_picks) == 1
    # Entry at index 15 (close 110.0), horizon 4 bars later is index 19 (close 120.0).
    assert round(full_alert_picks[0]["pct_change_since_entry"], 2) == round((120.0 - 110.0) / 110.0 * 100, 2)


def test_simulate_momentum_alerts_multiple_symbols_are_independent():
    up_bars = _minute_bars([100.0] * 15 + [110.0] * 10)
    up_bars[15] = _Bar(close=110.0, volume=100_000.0, timestamp=up_bars[15].timestamp, open=100.0, high=110.0, low=99.0)
    flat_bars = _minute_bars([50.0] * 25)

    picks = simulate_momentum_alerts(
        {"AAA": up_bars, "BBB": flat_bars}, threshold=_THRESHOLD, horizon_minutes=5
    )

    symbols_with_full_alert = {p["symbol"] for p in picks if p["kind"] == "full_alert"}
    assert symbols_with_full_alert == {"AAA"}


def test_sweep_momentum_params_returns_one_row_per_combination():
    bars = {"AAA": _minute_bars([100.0] * 40)}
    results = sweep_momentum_params(bars, thresholds=[1.0, 5.0], horizons_minutes=[5, 15, 30])

    assert len(results) == 6  # 2 thresholds x 3 horizons
    combos = {(r["threshold"], r["horizon_minutes"]) for r in results}
    assert combos == {(1.0, 5), (1.0, 15), (1.0, 30), (5.0, 5), (5.0, 15), (5.0, 30)}


def test_sweep_momentum_params_lower_threshold_finds_more_picks():
    # A modest, sustained +3% move: too small for the default 5%
    # threshold, but should trigger at a looser 1% one.
    closes = [100.0] * 15 + [103.0] * 20
    bars = {"AAA": _minute_bars(closes)}
    bars["AAA"][15] = _Bar(close=103.0, volume=100_000.0, timestamp=bars["AAA"][15].timestamp, open=100.0, high=103.0, low=99.0)

    results = sweep_momentum_params(bars, thresholds=[1.0, 5.0], horizons_minutes=[5])

    loose = next(r for r in results if r["threshold"] == 1.0)
    strict = next(r for r in results if r["threshold"] == 5.0)
    assert loose["full_alert"]["sample_size"] == 1
    assert strict["full_alert"]["sample_size"] == 0


def test_sweep_momentum_params_reuses_same_bars_across_combinations():
    # A sanity check that the sweep doesn't mutate/consume its input --
    # the same bars dict backs every combination in the grid.
    bars = {"AAA": _minute_bars([100.0] * 15 + [110.0] * 20)}
    bars["AAA"][15] = _Bar(close=110.0, volume=100_000.0, timestamp=bars["AAA"][15].timestamp, open=100.0, high=110.0, low=99.0)

    results = sweep_momentum_params(bars, thresholds=[5.0], horizons_minutes=[5, 5])
    assert results[0]["full_alert"] == results[1]["full_alert"]
