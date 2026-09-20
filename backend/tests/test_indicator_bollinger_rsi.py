"""The two overlays added for reading a chart rather than trading it:
Bollinger bands in price units, RSI in its own pane between 0 and 100.

Checked against arithmetic anyone can redo by hand -- a flat series, a
rising one, a series with exactly one bar too few -- rather than against
pandas_ta's own output, which would only prove that pandas_ta equals
pandas_ta.
"""

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from app.indicators import bollinger, rsi
from app.indicators.context import IndicatorContext

START = datetime(2026, 9, 18, 13, 30, tzinfo=timezone.utc)


def _ctx(closes: list[float]) -> IndicatorContext:
    frame = pd.DataFrame(
        {
            "timestamp": [START + timedelta(minutes=i) for i in range(len(closes))],
            "open": closes,
            "high": [c + 0.1 for c in closes],
            "low": [c - 0.1 for c in closes],
            "close": closes,
            "volume": [1_000] * len(closes),
        }
    )
    empty = pd.DataFrame(columns=frame.columns)
    return IndicatorContext(
        symbol="TEST", minute_bars=frame, weekly_bars=empty, monthly_bars=empty, hourly_bars=empty, timeframe="1Min"
    )


def _values(points: list[dict]) -> list[float | None]:
    return [p["value"] for p in points]


# --- Bollinger ----------------------------------------------------------


def test_a_flat_series_has_no_width_and_sits_on_its_mean():
    """Twenty identical closes: standard deviation zero, so all three lines
    are the price itself -- the pinched band at its limit."""
    out = bollinger.compute(_ctx([100.0] * 25))
    for name in ("Bollinger upper", "Bollinger middle", "Bollinger lower"):
        assert _values(out[name])[-1] == pytest.approx(100.0)


def test_the_bands_are_two_deviations_around_the_mean():
    closes = [100.0] * 19 + [110.0]
    out = bollinger.compute(_ctx(closes))
    window = pd.Series(closes[-20:])
    mean = window.mean()
    # pandas_ta uses the sample deviation (ddof=1), the same one pandas
    # defaults to.
    deviation = window.std()

    assert _values(out["Bollinger middle"])[-1] == pytest.approx(mean, abs=1e-6)
    assert _values(out["Bollinger upper"])[-1] == pytest.approx(mean + 2 * deviation, abs=1e-6)
    assert _values(out["Bollinger lower"])[-1] == pytest.approx(mean - 2 * deviation, abs=1e-6)
    # Upper above middle above lower, always.
    assert _values(out["Bollinger upper"])[-1] > _values(out["Bollinger middle"])[-1] > _values(out["Bollinger lower"])[-1]


def test_the_first_bars_have_no_band_yet():
    """Nineteen closes cannot fill a twenty-bar window: those points carry
    None rather than a number invented from a shorter window."""
    out = bollinger.compute(_ctx([100.0 + i for i in range(25)]))
    values = _values(out["Bollinger middle"])
    assert values[:19] == [None] * 19
    assert values[19] is not None


def test_too_few_bars_draw_nothing_at_all():
    out = bollinger.compute(_ctx([100.0] * 5))
    assert out == {"Bollinger upper": [], "Bollinger middle": [], "Bollinger lower": []}
    assert bollinger.compute(_ctx([]))["Bollinger middle"] == []


# --- RSI ----------------------------------------------------------------


def test_an_unbroken_rise_pins_rsi_at_a_hundred():
    """No down bar means no average loss, and Wilder's ratio has nothing to
    divide by: the definition's own ceiling."""
    out = rsi.compute(_ctx([100.0 + i for i in range(30)]))
    assert _values(out["RSI 14"])[-1] == pytest.approx(100.0)


def test_an_unbroken_fall_pins_it_at_zero():
    out = rsi.compute(_ctx([100.0 - i for i in range(30)]))
    assert _values(out["RSI 14"])[-1] == pytest.approx(0.0)


def test_alternating_bars_sit_near_the_middle():
    closes = [100.0 + (1.0 if i % 2 else 0.0) for i in range(40)]
    value = _values(rsi.compute(_ctx(closes))["RSI 14"])[-1]
    assert 40.0 < value < 60.0


def test_every_value_stays_inside_the_pane():
    closes = [100.0, 101.5, 101.0, 103.0, 102.0, 99.0, 98.5, 101.0] * 6
    for value in _values(rsi.compute(_ctx(closes))["RSI 14"]):
        assert value is None or 0.0 <= value <= 100.0


def test_too_few_bars_draw_nothing():
    assert rsi.compute(_ctx([100.0] * 14))["RSI 14"] == []
    assert rsi.compute(_ctx([]))["RSI 14"] == []


def test_the_pane_declares_a_fixed_scale_and_its_guides():
    """Without a declared range the pane would autoscale to the session's
    own high and low, which makes a quiet 45-55 day look violent."""
    assert rsi.KIND == "oscillator"
    assert rsi.RANGE == {"min": 0.0, "max": 100.0}
    assert [g["value"] for g in rsi.GUIDES] == [70.0, 50.0, 30.0]
