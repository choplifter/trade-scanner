from datetime import datetime, timezone
from types import SimpleNamespace

from app.dash_app.pages import screener as page
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
    def __init__(self, floats=None):
        self._floats = floats or {}

    def float_shares(self, symbol):
        return self._floats.get(symbol)


class _NewsCache:
    def __init__(self, headlines=None):
        self._headlines = headlines or {}

    def get(self, symbol):
        return self._headlines.get(symbol)


def _engine(rows, floats=None, headlines=None, latest=None, session="regular"):
    return SimpleNamespace(
        rows={r.symbol: r for r in rows},
        _latest_session_rows=latest,
        session=session,
        is_latest_session_fallback=not rows,
        fundamentals=_Fundamentals(floats),
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


# --- Dash page helpers --------------------------------------------------------


def test_page_coerces_text_input_per_operator():
    assert page._coerce("5", "gt") == 5.0
    assert page._coerce("NASDAQ", "eq") == "NASDAQ"
    assert page._coerce("NASDAQ, NYSE", "in") == ["NASDAQ", "NYSE"]
    assert page._coerce("", "gt") is None
    # Unparseable numerics pass through; the screener's comparison drops them
    # rather than this raising mid-callback.
    assert page._coerce("abc", "gt") == "abc"


def test_page_builds_one_row_per_filter_including_boolean_ones():
    rows = page.build_filter_rows(
        [{"field": "pct_change", "op": "gt", "value": 5}, {"field": "is_hod", "op": "is_true"}]
    )
    assert len(rows) == 2


def test_page_filter_rows_always_include_both_value_inputs():
    # Dash resolves pattern-matching inputs positionally, so a row that
    # sometimes omits an input would shift every later row's values by one.
    def count_inputs(component):
        found = 0
        children = getattr(component, "children", None) or []
        for child in children if isinstance(children, list) else [children]:
            if type(child).__name__ == "Input":
                found += 1
            found += count_inputs(child)
        return found

    boolean_row = page.build_filter_rows([{"field": "is_hod", "op": "is_true"}])[0]
    between_row = page.build_filter_rows([{"field": "last_price", "op": "between", "value": 1, "value2": 2}])[0]
    assert count_inputs(boolean_row) == count_inputs(between_row) == 2


def test_page_formats_by_registry_type():
    assert page._format(-3.2, "pct_change") == "-3.20%"
    assert page._format(1_234_567.0, "dollar_volume_today") == "$1.2M"
    assert page._format(True, "is_hod") == "Yes"
    assert page._format(None, "float_shares") == "—"


def test_page_screen_from_state_round_trips_into_a_valid_screen():
    screen = page._screen_from_state(
        [{"field": "rvol", "op": "gte", "value": "3"}], "pct_change", "asc", "25"
    )
    assert screen.filters[0].value == 3.0
    assert screen.descending is False
    assert screen.limit == 25
