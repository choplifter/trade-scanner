from datetime import datetime, timezone
from types import SimpleNamespace

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


def _engine(rows, floats=None, headlines=None, latest=None, session="regular", short_pcts=None):
    return SimpleNamespace(
        rows={r.symbol: r for r in rows},
        _latest_session_rows=latest,
        session=session,
        is_latest_session_fallback=not rows,
        fundamentals=_Fundamentals(floats, short_pcts),
        news_cache=_NewsCache(headlines),
    )


_SETTINGS = SimpleNamespace(scanner_min_dollar_volume=1_000_000.0)


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
