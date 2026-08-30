from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from app.replay.engine import ReplayEngine
from app.services.market_clock import ET

_DAY = date(2026, 1, 5)  # a real NYSE session


@dataclass
class _Bar:
    volume: float
    timestamp: datetime
    close: float = 10.0
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


def _session(closes: list[float], volume: float = 100_000.0, start: time = time(9, 30), day: date = _DAY):
    first = datetime.combine(day, start, tzinfo=ET)
    return [
        _Bar(volume=volume, close=c, open=c - 1, timestamp=first + timedelta(minutes=5 * i))
        for i, c in enumerate(closes)
    ]


def _daily(closes: list[float], volume: float = 1_000_000.0, end: date = _DAY):
    return [
        _Bar(
            volume=volume,
            close=c,
            timestamp=datetime.combine(end - timedelta(days=len(closes) - 1 - i), time(0, 0), tzinfo=ET),
        )
        for i, c in enumerate(closes)
    ]


_FLAT_CURVE = [(time(0, 0), 0.1), (time(23, 0), 1.0)]


def _daily_series(prev_close: float, days: int = 25) -> list[_Bar]:
    """`days` daily bars ending at _DAY (inclusive) -- past
    rvol_backtest._WARMUP_TRADING_DAYS (20), so _DAY's trailing-volume
    baseline is actually computed rather than skipped as unwarmed. The bar
    dated the day *before* _DAY carries `prev_close`: previous_closes()
    reads that bar's close as _DAY's gap denominator.
    """
    closes = [100.0] * (days - 2) + [prev_close, 100.0]
    return _daily(closes, end=_DAY)


def _engine(bars_by_symbol: dict[str, list], prev_close: float = 10.0) -> ReplayEngine:
    daily_by_symbol = {symbol: _daily_series(prev_close) for symbol in bars_by_symbol}
    return ReplayEngine(bars_by_symbol, daily_by_symbol, _FLAT_CURVE)


def test_gainers_and_losers_rank_the_cohort_at_a_given_timestamp():
    # AAA gaps up 20%, BBB gaps down 20% -- same magnitude, opposite sign, so
    # each should lead its own view and appear in neither of the other's.
    bars = {"AAA": _session([12.0]), "BBB": _session([8.0])}
    engine = _engine(bars)
    as_of = engine.timestamps[0]

    views = engine.snapshot_views(as_of, min_dollar_volume=0.0)
    assert [r.symbol for r in views["gainers"]][:1] == ["AAA"]
    assert [r.symbol for r in views["losers"]][:1] == ["BBB"]
    assert "BBB" not in [r.symbol for r in views["gainers"]]
    assert "AAA" not in [r.symbol for r in views["losers"]]


def test_snapshot_views_uses_the_nearest_bar_at_or_before_as_of():
    bars = {"AAA": _session([11.0, 22.0])}
    engine = _engine(bars)
    first, second = engine.timestamps

    # Between the two bars -- must read as the first bar's state, not the
    # second's (no look-ahead) and not empty (a real bar did happen).
    between = first + (second - first) / 2
    views = engine.snapshot_views(between, min_dollar_volume=0.0)
    row = next(r for r in views["most_active"] if r.symbol == "AAA")
    assert round(row.pct_change, 2) == 10.0  # 11.0 vs prev close 10.0, not 22.0's 120%


def test_snapshot_views_empty_before_the_first_timestamp():
    bars = {"AAA": _session([11.0])}
    engine = _engine(bars)
    before = engine.timestamps[0] - timedelta(days=1)
    views = engine.snapshot_views(before, min_dollar_volume=0.0)
    assert views == {"gainers": [], "losers": [], "most_active": []}


def test_reference_price_and_day_high_match_the_ranked_row():
    bars = {"AAA": _session([11.0, 22.0])}
    engine = _engine(bars)
    ts = engine.timestamps[1]
    assert engine.reference_price("AAA", ts) == 22.0
    assert engine.day_high("AAA", ts) == 22.0
    assert engine.reference_price("ZZZ", ts) is None


def test_first_at_or_after_lands_on_the_next_available_bar():
    bars = {"AAA": _session([11.0, 22.0])}
    engine = _engine(bars)
    first, second = engine.timestamps
    assert engine.first_at_or_after(first) == first
    assert engine.first_at_or_after(first + timedelta(minutes=1)) == second
    assert engine.first_at_or_after(second + timedelta(days=1)) is None


def test_start_and_end_span_the_available_range():
    bars = {"AAA": _session([11.0, 22.0, 33.0])}
    engine = _engine(bars)
    assert engine.start == engine.timestamps[0]
    assert engine.end == engine.timestamps[-1]


def test_empty_bars_yield_no_timestamps_and_empty_views():
    engine = _engine({})
    assert engine.timestamps == []
    assert engine.start is None and engine.end is None
    views = engine.snapshot_views(datetime.combine(_DAY, time(9, 30), tzinfo=ET), min_dollar_volume=0.0)
    assert views == {"gainers": [], "losers": [], "most_active": []}
