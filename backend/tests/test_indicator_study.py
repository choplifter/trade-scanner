"""STUDY -- the formula an indicator declares so the intraday chart can
recompute it from its own candles (see app.indicators.loader) -- has to be
the formula compute() actually runs, or the 1m line (backend) and the 15m
line (browser) would quietly disagree. This recomputes each declared study
with pandas_ta and checks it against the indicator's own output; the
browser's side is pinned against pandas_ta in chartStudies.test.ts."""

import math
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pandas_ta as ta
import pytest

from app.indicators import bollinger, ema, rsi
from app.indicators.context import build_context
from app.indicators.loader import run_indicators


def _bars(n=60):
    start = datetime(2026, 9, 18, 13, 30, tzinfo=timezone.utc)
    closes = [100 + 3 * math.sin(i / 4) + (i % 7) * 0.1 for i in range(n)]
    return [
        SimpleNamespace(timestamp=start + timedelta(minutes=i), open=c, high=c + 0.5, low=c - 0.5,
                        close=c, volume=1_000, vwap=c)
        for i, c in enumerate(closes)
    ], pd.Series(closes)


def _expected(study, closes):
    kind, n = study["type"], study["length"]
    if kind == "ema":
        return ta.ema(closes, length=n)
    if kind == "rsi":
        return ta.rsi(closes, length=n)
    bands = ta.bbands(closes, length=n, std=study.get("stdev", 2.0))
    return {"bb_lower": bands.iloc[:, 0], "sma": bands.iloc[:, 1], "bb_upper": bands.iloc[:, 2]}[kind]


@pytest.mark.parametrize("module", [ema, bollinger, rsi])
def test_the_declared_study_is_what_compute_draws(module):
    bars, closes = _bars()
    series = module.compute(build_context("TEST", bars, [], []))
    assert set(module.STUDY) == set(series), "every sub-series declares its formula"
    for sub, study in module.STUDY.items():
        expected = _expected(study, closes)
        for point, want in zip(series[sub], expected):
            if want != want:  # NaN
                assert point["value"] is None
            else:
                assert point["value"] == pytest.approx(float(want))


def test_the_loader_sends_the_study_to_the_chart():
    bars, _ = _bars()
    by_name = {ind["name"]: ind for ind in run_indicators(build_context("TEST", bars, [], []))}
    assert by_name["RSI"]["study"] == {"RSI 14": {"type": "rsi", "length": 14}}
    assert "study" not in by_name["Daily Range"]
