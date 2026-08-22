from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.core.config import Settings
from app.market_data.bars import MOMENTUM_BAR_MINUTES
from app.market_data.momentum import MOMENTUM_WINDOW
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


# Monday 2026-08-03, 14:30 UTC = 10:30 ET -- deliberately a real NYSE
# trading day, mid-regular-session, since momentum only evaluates
# regular-session bars now (see momentum.is_regular_session_bar). Fixtures
# stay well inside 09:30-16:00 ET so no bar drifts past the close.
_BASE = datetime(2026, 8, 3, 14, 30, tzinfo=timezone.utc)
_THRESHOLD = Settings().alarm_momentum_pct_threshold

# How many 5-minute bars make up one MOMENTUM_WINDOW -- the flat lead-in a
# trigger needs behind it, and therefore the index the trigger bar sits at.
_LEAD_IN = int(MOMENTUM_WINDOW.total_seconds() // 60) // MOMENTUM_BAR_MINUTES


def _bars(closes: list[float], start: datetime = _BASE) -> list[_Bar]:
    """One flat (zero-wick) 5-minute bar per close -- fine for tests that
    only care about the momentum magnitude, not candle shape.

    Most fixtures below lead in with _LEAD_IN flat bars before the trigger
    bar, which is exactly MOMENTUM_WINDOW's worth: the trigger needs a
    reference price a full window back, and without the lead-in there is
    nothing to measure against. Derived from the constant rather than
    written out, because these fixtures encoded "3 bars = 15 minutes" and
    every one of them broke silently the day the window changed.
    """
    return [_Bar(close=c, volume=100_000.0, timestamp=start + timedelta(minutes=5 * i)) for i, c in enumerate(closes)]


def test_simulate_momentum_alerts_no_picks_when_flat():
    bars = {"AAA": _bars([100.0] * 40)}
    picks = simulate_momentum_alerts(bars, threshold=_THRESHOLD, horizon_minutes=15)
    assert picks == []


def test_simulate_momentum_alerts_threshold_only_without_shape_confirmation():
    # One window of flat lead-in, then a +10% jump that closes
    # mid-range (not shaved) -- crosses the threshold but the entry candle
    # doesn't confirm.
    closes = [100.0] * _LEAD_IN + [110.0] * 10
    bars = _bars(closes)
    # Override the trigger bar (index 3) with a wide range closing in the middle.
    bars[_LEAD_IN] = _Bar(close=110.0, volume=100_000.0, timestamp=bars[_LEAD_IN].timestamp, open=105.0, high=115.0, low=100.0)

    picks = simulate_momentum_alerts({"AAA": bars}, threshold=_THRESHOLD, horizon_minutes=5)

    kinds = {p["kind"] for p in picks}
    assert "threshold_only" in kinds
    assert "full_alert" not in kinds


def test_simulate_momentum_alerts_full_alert_when_shape_confirms():
    closes = [100.0] * _LEAD_IN + [110.0] * 10
    bars = _bars(closes)
    # Shaved top (upper wick ~0), closes green (close > open), and the
    # short flat-100 run beforehand keeps the running VWAP low enough that
    # closing at 110 lands well above it -- all three confirmation gates
    # satisfied. Lower wick (low=95) is deliberately wide -- the bottom
    # side isn't evaluated at all, long-only, but keeps the fixture honest
    # about not needing a shaved bottom too. At this resolution the bar
    # itself *is* the confirmation candle -- no aggregation to fake data for.
    bars[_LEAD_IN] = _Bar(close=110.0, volume=100_000.0, timestamp=bars[_LEAD_IN].timestamp, open=100.0, high=110.0, low=95.0)

    picks = simulate_momentum_alerts({"AAA": bars}, threshold=_THRESHOLD, horizon_minutes=5)

    full_alert_picks = [p for p in picks if p["kind"] == "full_alert"]
    assert len(full_alert_picks) == 1
    assert full_alert_picks[0]["symbol"] == "AAA"
    assert "view" not in full_alert_picks[0]


def test_simulate_momentum_alerts_requires_green_candle():
    # Shaved top (upper wick ~0.04) but closes red (close < open) --
    # confirmation requires a green candle, not just wick shape.
    closes = [100.0] * _LEAD_IN + [110.0] * 10
    bars = _bars(closes)
    bars[_LEAD_IN] = _Bar(close=110.0, volume=100_000.0, timestamp=bars[_LEAD_IN].timestamp, open=110.5, high=110.5, low=99.0)

    picks = simulate_momentum_alerts({"AAA": bars}, threshold=_THRESHOLD, horizon_minutes=5)

    kinds = {p["kind"] for p in picks}
    assert "threshold_only" in kinds
    assert "full_alert" not in kinds


def test_simulate_momentum_alerts_requires_above_vwap():
    # A higher base (200) before a deeper drop to 90 drags the running
    # session VWAP well above a move that's only recovering part of that
    # drop (90 -> 100, +11%) -- shaved top and green both confirm, but the
    # close is still well below the running VWAP, so this shouldn't fire
    # full_alert even though it clears the momentum threshold.
    closes = [200.0] * _LEAD_IN + [90.0] * 7 + [100.0] * 5
    bars = _bars(closes)
    recovery = _LEAD_IN + 7
    bars[recovery] = _Bar(
        close=100.0, volume=100_000.0, timestamp=bars[recovery].timestamp,
        open=95.0, high=100.0, low=90.0,
    )

    picks = simulate_momentum_alerts({"AAA": bars}, threshold=_THRESHOLD, horizon_minutes=5)

    kinds_at_recovery = {
        p["kind"] for p in picks if p["timestamp"] == bars[recovery].timestamp.isoformat()
    }
    assert "threshold_only" in kinds_at_recovery
    assert "full_alert" not in kinds_at_recovery


def test_simulate_momentum_alerts_ignores_downward_moves_entirely():
    # A sharp, shaved-bottom -10% move -- would satisfy the live alarm's
    # short-side check, but this backtest is long-only, so it should
    # produce nothing at all, not even a threshold_only pick.
    closes = [100.0] * _LEAD_IN + [90.0] * 10
    bars = _bars(closes)
    bars[_LEAD_IN] = _Bar(close=90.0, volume=100_000.0, timestamp=bars[_LEAD_IN].timestamp, open=100.0, high=105.0, low=90.0)

    picks = simulate_momentum_alerts({"AAA": bars}, threshold=_THRESHOLD, horizon_minutes=5)

    assert picks == []


def test_simulate_momentum_alerts_rising_edge_dedup():
    # The +10% move happens at index 3 and then simply *stays* at that
    # level for several more bars -- momentum relative to one window back
    # stays >= threshold across several consecutive bars, all with the
    # same shaved-top entry candle shape, but only the FIRST one should
    # count as a pick, not one per bar.
    closes = [100.0] * _LEAD_IN + [110.0] * 10
    bars = _bars(closes)
    for i in range(3, 8):
        bars[i] = _Bar(close=110.0, volume=100_000.0, timestamp=bars[i].timestamp, open=100.0, high=110.0, low=99.0)

    picks = simulate_momentum_alerts({"AAA": bars}, threshold=_THRESHOLD, horizon_minutes=5)

    full_alert_picks = [p for p in picks if p["kind"] == "full_alert"]
    assert len(full_alert_picks) == 1


def test_simulate_momentum_alerts_outcome_uses_horizon_minutes():
    # horizon_minutes=10 converts to a 2-bar offset (MOMENTUM_BAR_MINUTES=5)
    # -- entry at index 3 (close 110), 2 bars later is index 5 (close 120),
    # not index 4 or 6 (both a different value), so an off-by-one in the
    # minutes->bars conversion would be caught.
    closes = [100.0] * _LEAD_IN + [110.0, 110.0, 120.0, 110.0, 110.0]
    bars = _bars(closes)
    bars[_LEAD_IN] = _Bar(close=110.0, volume=100_000.0, timestamp=bars[_LEAD_IN].timestamp, open=100.0, high=110.0, low=99.0)

    picks = simulate_momentum_alerts({"AAA": bars}, threshold=_THRESHOLD, horizon_minutes=10)

    full_alert_picks = [p for p in picks if p["kind"] == "full_alert"]
    assert len(full_alert_picks) == 1
    assert round(full_alert_picks[0]["pct_change_since_entry"], 2) == round((120.0 - 110.0) / 110.0 * 100, 2)


def test_simulate_momentum_alerts_multiple_symbols_are_independent():
    up_bars = _bars([100.0] * _LEAD_IN + [110.0] * 5)
    up_bars[_LEAD_IN] = _Bar(close=110.0, volume=100_000.0, timestamp=up_bars[_LEAD_IN].timestamp, open=100.0, high=110.0, low=99.0)
    flat_bars = _bars([50.0] * 10)

    picks = simulate_momentum_alerts(
        {"AAA": up_bars, "BBB": flat_bars}, threshold=_THRESHOLD, horizon_minutes=5
    )

    symbols_with_full_alert = {p["symbol"] for p in picks if p["kind"] == "full_alert"}
    assert symbols_with_full_alert == {"AAA"}


def test_sweep_momentum_params_returns_one_row_per_combination():
    bars = {"AAA": _bars([100.0] * 40)}
    results = sweep_momentum_params(bars, thresholds=[1.0, 5.0], horizons_minutes=[5, 15, 30])

    assert len(results) == 6  # 2 thresholds x 3 horizons
    combos = {(r["threshold"], r["horizon_minutes"]) for r in results}
    assert combos == {(1.0, 5), (1.0, 15), (1.0, 30), (5.0, 5), (5.0, 15), (5.0, 30)}


def test_sweep_momentum_params_lower_threshold_finds_more_picks():
    # A modest, sustained +3% move: too small for the default 5%
    # threshold, but should trigger at a looser 1% one.
    closes = [100.0] * _LEAD_IN + [103.0] * 10
    bars = {"AAA": _bars(closes)}
    bars["AAA"][_LEAD_IN] = _Bar(close=103.0, volume=100_000.0, timestamp=bars["AAA"][_LEAD_IN].timestamp, open=100.0, high=103.0, low=99.0)

    results = sweep_momentum_params(bars, thresholds=[1.0, 5.0], horizons_minutes=[5])

    loose = next(r for r in results if r["threshold"] == 1.0)
    strict = next(r for r in results if r["threshold"] == 5.0)
    assert loose["full_alert"]["sample_size"] == 1
    assert strict["full_alert"]["sample_size"] == 0


def test_sweep_momentum_params_reuses_same_bars_across_combinations():
    # A sanity check that the sweep doesn't mutate/consume its input --
    # the same bars dict backs every combination in the grid.
    bars = {"AAA": _bars([100.0] * _LEAD_IN + [110.0] * 10)}
    bars["AAA"][_LEAD_IN] = _Bar(close=110.0, volume=100_000.0, timestamp=bars["AAA"][_LEAD_IN].timestamp, open=100.0, high=110.0, low=99.0)

    results = sweep_momentum_params(bars, thresholds=[5.0], horizons_minutes=[5, 5])
    assert results[0]["full_alert"] == results[1]["full_alert"]


# --- Regular-session gating -------------------------------------------------
#
# Regression coverage for a real backtest artifact: an APLD pick on
# 2026-07-27 measured "+5.34% in 15 minutes" purely by comparing a
# 300-share after-hours print at 16:10 ET against the last regular-session
# close at 15:55 ET, i.e. straight across the market close. Momentum is
# regular-session-only now (see momentum.is_regular_session_bar).

# Same trading day as _BASE. ET is UTC-4 in August, so 19:40 UTC = 15:40 ET.
_LATE_SESSION = datetime(2026, 8, 3, 19, 40, tzinfo=timezone.utc)


def test_no_picks_from_after_hours_bars_across_the_close():
    # Four calm regular-session bars into the close, then a big jump on an
    # after-hours bar at 16:10 ET -- the exact APLD shape. The jump clears
    # the 5% threshold against 15:55's close, but must not produce a pick.
    bars = [
        _Bar(close=26.285, volume=15_661.0, timestamp=_LATE_SESSION),                        # 15:40 ET
        _Bar(close=26.305, volume=17_959.0, timestamp=_LATE_SESSION + timedelta(minutes=5)),  # 15:45 ET
        _Bar(close=26.345, volume=39_141.0, timestamp=_LATE_SESSION + timedelta(minutes=10)), # 15:50 ET
        _Bar(close=26.38, volume=45_978.0, timestamp=_LATE_SESSION + timedelta(minutes=15)),  # 15:55 ET
        # 16:10 ET, after the close: +5.34% vs 15:55, on 300 shares.
        _Bar(close=27.79, volume=300.0, timestamp=_LATE_SESSION + timedelta(minutes=30),
             open=27.78, high=27.79, low=27.78),
        _Bar(close=27.75, volume=200.0, timestamp=_LATE_SESSION + timedelta(minutes=35)),
    ]

    picks = simulate_momentum_alerts({"APLD": bars}, threshold=_THRESHOLD, horizon_minutes=5)

    assert picks == []


def test_opening_range_can_trigger_using_a_premarket_reference():
    # The mirror of the after-hours case, and deliberately NOT symmetric
    # with it: a big jump on the 09:35 ET bar, whose 15-minutes-back
    # reference price sits in premarket. The trigger bar is a real, liquid
    # regular-session bar, so this SHOULD fire -- excluding premarket from
    # the lookback entirely would blank out the opening range, which was
    # most of the historical picks (see momentum.same_trading_day).
    # Anchored on the trigger and built backwards, so the reference bar lands
    # one MOMENTUM_WINDOW earlier wherever that falls -- at a 30-minute window
    # that is 09:05 ET, still comfortably premarket.
    trigger = datetime(2026, 8, 3, 13, 35, tzinfo=timezone.utc)  # 09:35 ET
    bars = [
        _Bar(close=100.0, volume=1_000.0,
             timestamp=trigger - MOMENTUM_WINDOW + timedelta(minutes=MOMENTUM_BAR_MINUTES * i))
        for i in range(_LEAD_IN)
    ]
    bars += [
        _Bar(close=110.0, volume=50_000.0, timestamp=trigger, open=100.0, high=110.0, low=99.0),
        _Bar(close=111.0, volume=50_000.0, timestamp=trigger + timedelta(minutes=5)),
    ]

    picks = simulate_momentum_alerts({"AAA": bars}, threshold=_THRESHOLD, horizon_minutes=5)

    # +10% vs the premarket close one window back, on a shaved-top green bar
    # above VWAP.
    assert {p["kind"] for p in picks} == {"threshold_only", "full_alert"}
    assert all(p["timestamp"] == trigger.isoformat() for p in picks)


def test_premarket_bar_itself_never_triggers():
    # The other half: premarket bars may serve as a *reference* price but
    # must never be the trigger bar themselves, even on a big clean move.
    premarket_start = datetime(2026, 8, 3, 11, 0, tzinfo=timezone.utc)  # 07:00 ET
    bars = [
        _Bar(close=100.0, volume=1_000.0, timestamp=premarket_start),                          # 07:00 ET
        _Bar(close=100.0, volume=1_000.0, timestamp=premarket_start + timedelta(minutes=5)),   # 07:05 ET
        _Bar(close=100.0, volume=1_000.0, timestamp=premarket_start + timedelta(minutes=10)),  # 07:10 ET
        _Bar(close=110.0, volume=5_000.0, timestamp=premarket_start + timedelta(minutes=15),   # 07:15 ET
             open=100.0, high=110.0, low=99.0),
        _Bar(close=111.0, volume=5_000.0, timestamp=premarket_start + timedelta(minutes=20)),  # 07:20 ET
    ]

    picks = simulate_momentum_alerts({"AAA": bars}, threshold=_THRESHOLD, horizon_minutes=5)

    assert picks == []


def test_pick_dropped_when_forward_horizon_leaves_the_session():
    # A clean, qualifying entry at 15:50 ET, but the forward
    # window lands after the close -- scoring it against a thin
    # after-hours print would be worse than not scoring it at all.
    # The qualifying entry sits at 15:50 ET with a full window of flat bars
    # behind it, so it triggers whatever the window length is.
    entry = datetime(2026, 8, 3, 19, 50, tzinfo=timezone.utc)  # 15:50 ET
    bars = [
        _Bar(close=100.0, volume=50_000.0,
             timestamp=entry - MOMENTUM_WINDOW + timedelta(minutes=MOMENTUM_BAR_MINUTES * i))
        for i in range(_LEAD_IN)
    ]
    bars += [
        _Bar(close=110.0, volume=50_000.0, timestamp=entry, open=100.0, high=110.0, low=99.0),
        _Bar(close=111.0, volume=50_000.0, timestamp=entry + timedelta(minutes=5)),   # 15:55 ET
        # Enough trailing after-hours bars that the walk actually *reaches*
        # the entry at a 3-bar horizon -- the loop stops horizon_bars short
        # of the end, so a shorter fixture would skip the entry entirely and
        # pass for the wrong reason.
        _Bar(close=112.0, volume=300.0, timestamp=entry + timedelta(minutes=25)),     # 16:15 ET (after hours)
        _Bar(close=113.0, volume=200.0, timestamp=entry + timedelta(minutes=30)),     # 16:20 ET (after hours)
        _Bar(close=114.0, volume=200.0, timestamp=entry + timedelta(minutes=35)),     # 16:25 ET (after hours)
    ]

    # horizon 15 min = 3 bars ahead of 15:50 ET -> lands outside the session.
    picks = simulate_momentum_alerts({"AAA": bars}, threshold=_THRESHOLD, horizon_minutes=15)
    assert picks == []

    # The same entry with a 5-minute horizon stays inside the session and
    # is scored normally -- proving it's the horizon, not the entry, being
    # rejected above.
    picks = simulate_momentum_alerts({"AAA": bars}, threshold=_THRESHOLD, horizon_minutes=5)
    assert {p["kind"] for p in picks} == {"threshold_only", "full_alert"}
