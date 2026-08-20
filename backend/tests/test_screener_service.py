from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

from app.services.market_clock import ET

from app.scanners import screener
from app.scanners.schemas import ScannerRow
from app.scanners.screener_service import derived_values, screen_live_rows


def _row(symbol="AAA", price=10.0, pct=5.0, rvol=2.0, volume=1_000_000.0):
    return ScannerRow(
        symbol=symbol,
        exchange="NASDAQ",
        last_price=price,
        prev_close=price,
        pct_change=pct,
        volume_today=volume,
        avg_vol_20d=volume / max(rvol, 0.01),
        rvol=rvol,
        dollar_volume_today=volume * price,
        updated_at=datetime.now(timezone.utc),
    )


class _Fundamentals:
    def __init__(self, floats=None, short_pcts=None):
        self._floats = floats or {}
        self._short_pcts = short_pcts or {}

    def float_shares(self, symbol):
        return self._floats.get(symbol)

    def short_interest_pct(self, symbol):
        return self._short_pcts.get(symbol)


class _NewsCache:
    def __init__(self, headlines=None):
        self._headlines = headlines or {}

    def get(self, symbol):
        return self._headlines.get(symbol)


class _MomentumCache:
    """Only the two things derived_values asks of it: the cached 5-minute
    bars behind rvol_window, and the latest candle's direction.
    """

    def __init__(self, bars=None, greens=None):
        self._bars = bars or {}
        self._greens = greens or {}

    def bars(self, symbol):
        return self._bars.get(symbol) or []

    def is_green(self, symbol):
        return self._greens.get(symbol, False)


def _engine(
    rows,
    floats=None,
    headlines=None,
    latest=None,
    session="regular",
    short_pcts=None,
    bars=None,
    greens=None,
    curve=None,
):
    return SimpleNamespace(
        rows={r.symbol: r for r in rows},
        _latest_session_rows=latest,
        session=session,
        is_latest_session_fallback=not rows,
        fundamentals=_Fundamentals(floats, short_pcts),
        news_cache=_NewsCache(headlines),
        momentum_cache=_MomentumCache(bars, greens),
        volume_profile=SimpleNamespace(curve=curve or []),
        settings=_SETTINGS,
    )


_SETTINGS = SimpleNamespace(
    scanner_min_dollar_volume=1_000_000.0, scanner_volume_surge_window_minutes=60
)


# --- derived values -----------------------------------------------------------


def test_float_comes_from_the_universe_wide_cache_not_the_row():
    rows = [_row("AAA")]
    # row.float_shares is None (only ranked rows get it attached), but the
    # bulk cache knows it -- which is what makes float screenable universe-wide.
    assert rows[0].float_shares is None
    derived = derived_values(_engine(rows, floats={"AAA": 12_000_000.0}), rows)
    assert derived["float_shares"]["AAA"] == 12_000_000.0


def test_rank_score_applies_the_catalyst_boost_only_to_gainers():
    up, down = _row("UP", pct=10.0), _row("DOWN", pct=-10.0)
    engine = _engine([up, down], headlines={"UP": "Earnings beat", "DOWN": "Earnings miss"})
    derived = derived_values(engine, [up, down])

    # Boosted above its raw magnitude on the up side...
    assert derived["rank_score"]["UP"] > 10.0
    # ...and untouched on the down side, matching engine._rank_losers.
    assert derived["rank_score"]["DOWN"] == -10.0


def test_rank_score_applies_the_fade_risk_discount():
    hot = _row("HOT", pct=10.0, rvol=50.0)
    derived = derived_values(_engine([hot]), [hot])
    assert derived["rank_score"]["HOT"] < 10.0


# --- screen_live_rows ---------------------------------------------------------


def test_tradability_floor_is_applied_before_user_filters():
    thin = _row("THIN", price=1.0, volume=1_000.0)  # $1k dollar volume
    liquid = _row("LIQ", price=10.0, volume=1_000_000.0)
    payload = screen_live_rows(_engine([thin, liquid]), _SETTINGS, screener.Screen())

    # A screen must not surface rows the scanner itself would never show.
    assert [r["symbol"] for r in payload["rows"]] == ["LIQ"]
    assert payload["tradable_size"] == 1
    assert payload["universe_size"] == 2


def test_falls_back_to_the_last_session_when_nothing_is_live():
    stored = _row("OLD")
    engine = _engine([], latest={"OLD": stored}, session="closed")
    payload = screen_live_rows(engine, _SETTINGS, screener.Screen())

    # An empty screener while the market is closed would look broken.
    assert [r["symbol"] for r in payload["rows"]] == ["OLD"]
    assert payload["is_latest_session"] is True


def test_screen_can_filter_on_universe_wide_float():
    small, large = _row("SMALL"), _row("LARGE")
    engine = _engine([small, large], floats={"SMALL": 5_000_000.0, "LARGE": 90_000_000.0})
    screen = screener.Screen(filters=[screener.Filter(field="float_shares", op="lt", value=20_000_000)])

    payload = screen_live_rows(engine, _SETTINGS, screen)
    assert [r["symbol"] for r in payload["rows"]] == ["SMALL"]
    assert payload["derived"]["float_shares"] == {"SMALL": 5_000_000.0}


def test_presets_run_against_live_rows():
    engine = _engine([_row("UP", pct=8.0), _row("DOWN", pct=-8.0)])
    payload = screen_live_rows(engine, _SETTINGS, screener.PRESETS["losers"]["screen"])
    assert [r["symbol"] for r in payload["rows"]] == ["DOWN"]


def test_short_interest_is_screenable_across_the_whole_universe():
    """Both inputs are already universe-wide bulk files (FINRA short interest,
    FMP float), so this needs no per-symbol fetch -- which is what lets it be
    filtered on before a symbol has ever been ranked."""
    heavy, light = _row("HEAVY"), _row("LIGHT")
    engine = _engine([heavy, light], short_pcts={"HEAVY": 32.5, "LIGHT": 1.2})
    screen = screener.Screen(
        filters=[screener.Filter(field="short_interest_pct", op="gt", value=20)]
    )

    payload = screen_live_rows(engine, _SETTINGS, screen)
    assert [r["symbol"] for r in payload["rows"]] == ["HEAVY"]
    assert payload["derived"]["short_interest_pct"] == {"HEAVY": 32.5}


# --- windowed rvol, volume concentration, green candle ------------------------

# A real NYSE session, so is_regular_session_bar finds trading hours for it.
_DAY = date(2026, 1, 5)
# Back-loaded like a real session, matching test_volume_surge's fixture.
_CURVE = [
    (time(9, 30), 0.02),
    (time(10, 30), 0.06),
    (time(11, 0), 0.10),
    (time(14, 0), 0.25),
    (time(15, 0), 0.45),
    (time(16, 0), 1.00),
]


@dataclass
class _Bar:
    volume: float
    timestamp: datetime
    close: float = 10.0
    open: float = 9.0
    high: float = 10.0
    low: float = 9.0


def _session_bars(count=36, volume=100.0, start=time(13, 0)):
    """Deliberately in the *second* half of the session: _CURVE has no point
    between 11:00 and 14:00, so a window landing there has an expected share
    of zero and every reading comes back None for a fixture reason rather
    than a real one.
    """
    first = datetime.combine(_DAY, start, tzinfo=ET)
    return [_Bar(volume=volume, timestamp=first + timedelta(minutes=5 * i)) for i in range(count)]


def test_rvol_window_follows_the_screens_window_not_the_global_default():
    """The whole point of Screen.window_minutes: same bars, same row, two
    different readings depending on what the screen asked for.
    """
    rows = [_row("AAA")]
    engine = _engine(rows, bars={"AAA": _session_bars()}, curve=_CURVE)

    hour = derived_values(engine, rows, 60)["rvol_window"]["AAA"]
    half = derived_values(engine, rows, 30)["rvol_window"]["AAA"]

    assert hour is not None and half is not None
    assert hour != half
    # None falls back to the global setting rather than skipping the field.
    assert derived_values(engine, rows, None)["rvol_window"]["AAA"] == hour


def test_volume_concentration_is_the_window_over_the_day():
    rows = [_row("AAA", rvol=2.0)]
    engine = _engine(rows, bars={"AAA": _session_bars()}, curve=_CURVE)

    values = derived_values(engine, rows, 60)
    assert values["volume_concentration"]["AAA"] == values["rvol_window"]["AAA"] / 2.0


def test_volume_concentration_is_none_rather_than_dividing_by_a_zero_rvol():
    """r.rvol is 0.0, not None, when the engine could not compute it -- so
    the guard has to be on falsiness or this raises.
    """
    rows = [_row("AAA", rvol=0.0)]
    engine = _engine(rows, bars={"AAA": _session_bars()}, curve=_CURVE)
    assert derived_values(engine, rows, 60)["volume_concentration"]["AAA"] is None


def test_a_red_candle_still_gets_a_reading_but_can_be_filtered_out():
    """The long bias moved from the volume maths to the filter layer: the
    number exists either way, is_green_candle is what excludes it.
    """
    rows = [_row("RED"), _row("GRN")]
    engine = _engine(
        rows,
        bars={"RED": _session_bars(), "GRN": _session_bars()},
        greens={"GRN": True, "RED": False},
        curve=_CURVE,
    )
    values = derived_values(engine, rows, 60)
    assert values["rvol_window"]["RED"] is not None
    assert values["is_green_candle"] == {"RED": False, "GRN": True}

    screen = screener.Screen(filters=[screener.Filter(field="is_green_candle", op="is_true")])
    result = screener.run_screen(rows, screen, values)
    assert [r.symbol for r in result.rows] == ["GRN"]


def test_meta_echoes_the_resolved_window_never_none():
    """The client labels its column from this, and "RVol (null m)" is not a
    header.
    """
    rows = [_row("AAA")]
    engine = _engine(rows, bars={"AAA": _session_bars()}, curve=_CURVE)

    payload = screen_live_rows(engine, _SETTINGS, screener.Screen())
    assert payload["window_minutes"] == 60

    payload = screen_live_rows(engine, _SETTINGS, screener.Screen(window_minutes=30))
    assert payload["window_minutes"] == 30


def test_a_half_typed_window_is_floored_rather_than_rejected():
    """The filter bar sends window_minutes on every keystroke, so "3" on the
    way to "30" must still return a screen. Clamping it client-side was the
    original approach and made 30 untypeable -- the 3 became a 5 and the 0
    landed after it.
    """
    rows = [_row("AAA")]
    engine = _engine(rows, bars={"AAA": _session_bars()}, curve=_CURVE)

    assert screen_live_rows(engine, _SETTINGS, screener.Screen(window_minutes=3))[
        "window_minutes"
    ] == 5
    # 0 is indistinguishable from "cleared" and falls back to the default.
    assert screen_live_rows(engine, _SETTINGS, screener.Screen(window_minutes=0))[
        "window_minutes"
    ] == 60
