"""The indicator VWAP against the hard-wired SessionVwapState.

Two implementations of one definition only stay one definition if something
checks. These replay identical bars through both -- the vectorised pandas
pass in app.indicators.vwap and the stateful accumulator in
app.market_data.vwap that /bars already returns -- and require they agree.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest

from app.indicators import vwap as indicator_vwap
from app.indicators.context import build_context
from app.market_data.vwap import SessionVwapState
from app.services.market_clock import ET


@dataclass
class _Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: float | None = None


def _session_bars(day: str, start_hour=9, start_minute=30, count=10, **kw) -> list[_Bar]:
    """`count` consecutive minute bars from a given ET wall-clock time."""
    base = datetime.fromisoformat(day).replace(
        hour=start_hour, minute=start_minute, tzinfo=ET
    )
    bars = []
    for i in range(count):
        price = 100.0 + i
        bars.append(
            _Bar(
                timestamp=(base + timedelta(minutes=i)).astimezone(ET),
                open=price,
                high=price + 0.5,
                low=price - 0.5,
                close=price,
                volume=1_000.0 + i * 10,
                **kw,
            )
        )
    return bars


def _hard_wired(bars: list[_Bar]) -> list[float | None]:
    state = SessionVwapState(symbol="TEST")
    return [
        state.update(
            timestamp=b.timestamp,
            high=b.high,
            low=b.low,
            close=b.close,
            volume=b.volume,
            bar_vwap=getattr(b, "vwap", None),
        )
        for b in bars
    ]


def _indicator(bars: list[_Bar]) -> list[float | None]:
    ctx = build_context("TEST", bars, [], [])
    return [p["value"] for p in indicator_vwap.compute(ctx)["VWAP"]]


def _assert_matches(bars: list[_Bar]) -> None:
    for i, (a, b) in enumerate(zip(_indicator(bars), _hard_wired(bars))):
        if a is None or b is None:
            assert a == b, f"bar {i}: indicator={a} hard-wired={b}"
        else:
            assert a == pytest.approx(b, rel=1e-12), f"bar {i}: {a} vs {b}"


def test_matches_hard_wired_on_a_plain_session():
    _assert_matches(_session_bars("2026-08-17"))


def test_matches_hard_wired_using_alpacas_per_bar_vwap():
    """The precedence that actually matters in production: real Alpaca bars
    carry a trade-weighted vwap, so a typical-price implementation would
    diverge from the line the chart already draws -- silently, and only on
    live data, where no fixture would catch it."""
    bars = _session_bars("2026-08-17")
    for i, b in enumerate(bars):
        b.vwap = b.close + (0.13 if i % 2 else -0.07)
    _assert_matches(bars)


def test_matches_hard_wired_when_the_feed_omits_vwap():
    bars = _session_bars("2026-08-17")
    for b in bars:
        b.vwap = None
    _assert_matches(bars)


def test_premarket_is_excluded_by_both():
    """Both must return None before 09:30, not a premarket-anchored value."""
    bars = _session_bars("2026-08-17", start_hour=7, count=5) + _session_bars("2026-08-17")
    indicator, hard = _indicator(bars), _hard_wired(bars)
    assert indicator[:5] == [None] * 5
    assert hard[:5] == [None] * 5
    _assert_matches(bars)


def test_each_session_restarts_rather_than_carrying_forward():
    """A multi-day window is the normal case -- /bars fetches several
    sessions. Getting this wrong would drag day two's line toward day one."""
    bars = _session_bars("2026-08-17") + _session_bars("2026-08-18")
    _assert_matches(bars)

    second_day = _indicator(bars)[10:]
    standalone = _indicator(_session_bars("2026-08-18"))
    assert second_day == pytest.approx(standalone, rel=1e-12)


def test_bands_widen_from_zero_as_prices_disperse():
    """The part the hard-wired line has no answer for.

    At the first print every share traded at one price, so the band width is
    zero -- and must be exactly zero rather than NaN, which is what an
    unclipped sqrt of a slightly-negative floating point variance would give.
    """
    result = indicator_vwap.compute(build_context("TEST", _session_bars("2026-08-17"), [], []))
    upper = [p["value"] for p in result["+1 SD"]]
    lower = [p["value"] for p in result["-1 SD"]]
    mid = [p["value"] for p in result["VWAP"]]

    assert upper[0] == pytest.approx(mid[0])
    assert lower[0] == pytest.approx(mid[0])
    widths = [u - l for u, l in zip(upper, lower)]
    assert all(w >= 0 for w in widths)
    assert widths[-1] > widths[1]


def test_empty_input_returns_empty_series_not_an_error():
    assert indicator_vwap.compute(build_context("TEST", [], [], [])) == {
        "VWAP": [],
        "+1 SD": [],
        "-1 SD": [],
    }
