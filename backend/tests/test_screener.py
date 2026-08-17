from datetime import datetime, timezone

from app.scanners.schemas import ScannerRow
from app.scanners.screener import (
    FIELDS,
    FIELDS_BY_NAME,
    OPERATORS_BY_TYPE,
    PRESETS,
    Filter,
    Screen,
    apply_filters,
    fields_payload,
    preset_list,
    run_screen,
    sort_rows,
)


def _row(symbol="AAA", price=10.0, pct=5.0, rvol=2.0, volume=1_000_000.0,
         exchange="NASDAQ", hod=False, stale=False, spread=None):
    return ScannerRow(
        symbol=symbol,
        exchange=exchange,
        last_price=price,
        prev_close=price / (1 + pct / 100) if pct != -100 else 1.0,
        pct_change=pct,
        volume_today=volume,
        avg_vol_20d=volume / max(rvol, 0.01),
        rvol=rvol,
        dollar_volume_today=volume * price,
        is_hod=hod,
        is_stale=stale,
        spread_pct=spread,
        updated_at=datetime.now(timezone.utc),
    )


# --- field registry -----------------------------------------------------------


def test_every_field_offers_operators_valid_for_its_type():
    for spec in FIELDS:
        if spec.sortable_only:
            assert spec.operators == ()
        else:
            assert spec.operators == OPERATORS_BY_TYPE[spec.type]


def test_fields_payload_is_serialisable_and_complete():
    payload = fields_payload()
    assert len(payload) == len(FIELDS)
    for entry in payload:
        # Exactly what a UI needs to render a filter row without knowing
        # anything about the backend.
        assert set(entry) == {"name", "label", "type", "operators", "derived"}
        assert entry["label"]


def test_boolean_fields_never_offer_numeric_operators():
    booleans = [f for f in FIELDS if f.type == "boolean"]
    assert booleans
    for spec in booleans:
        assert "gt" not in spec.operators
        assert set(spec.operators) == {"is_true", "is_false"}


# --- filtering ----------------------------------------------------------------


def test_filters_are_anded():
    rows = [_row("AAA", pct=10.0, rvol=5.0), _row("BBB", pct=10.0, rvol=1.0), _row("CCC", pct=1.0, rvol=5.0)]
    matched = apply_filters(
        rows, [Filter(field="pct_change", op="gt", value=5), Filter(field="rvol", op="gt", value=3)]
    )
    assert [r.symbol for r in matched] == ["AAA"]


def test_between_is_order_insensitive():
    rows = [_row("AAA", price=10.0), _row("BBB", price=100.0)]
    for low, high in ((5, 50), (50, 5)):
        matched = apply_filters(rows, [Filter(field="last_price", op="between", value=low, value2=high)])
        assert [r.symbol for r in matched] == ["AAA"]


def test_boolean_operators():
    rows = [_row("AAA", hod=True), _row("BBB", hod=False)]
    assert [r.symbol for r in apply_filters(rows, [Filter(field="is_hod", op="is_true")])] == ["AAA"]
    assert [r.symbol for r in apply_filters(rows, [Filter(field="is_lod", op="is_false")])] == ["AAA", "BBB"]


def test_text_operators_are_case_insensitive():
    rows = [_row("AAA", exchange="NASDAQ"), _row("BBB", exchange="NYSE")]
    assert [r.symbol for r in apply_filters(rows, [Filter(field="exchange", op="eq", value="nasdaq")])] == ["AAA"]
    assert [r.symbol for r in apply_filters(rows, [Filter(field="symbol", op="contains", value="bb")])] == ["BBB"]
    assert len(apply_filters(rows, [Filter(field="exchange", op="in", value=["NYSE", "AMEX"])])) == 1


def test_a_missing_value_never_matches_a_numeric_filter():
    # spread_pct is None here. "spread under 1%" must not sweep in rows whose
    # spread was never measured -- that would fill a screen with unchecked rows.
    rows = [_row("AAA", spread=None), _row("BBB", spread=0.5)]
    assert [r.symbol for r in apply_filters(rows, [Filter(field="spread_pct", op="lt", value=1.0)])] == ["BBB"]


def test_unknown_fields_are_ignored_not_fatal():
    # A saved screen naming a field that has since been removed still runs.
    rows = [_row("AAA")]
    assert apply_filters(rows, [Filter(field="no_such_field", op="gt", value=1)]) == rows


def test_derived_fields_filter_from_the_supplied_map():
    rows = [_row("AAA"), _row("BBB")]
    derived = {"float_shares": {"AAA": 5_000_000.0, "BBB": 80_000_000.0}}
    matched = apply_filters(rows, [Filter(field="float_shares", op="lt", value=20_000_000)], derived)
    assert [r.symbol for r in matched] == ["AAA"]


def test_derived_field_missing_for_a_symbol_does_not_match():
    rows = [_row("AAA"), _row("BBB")]
    derived = {"float_shares": {"AAA": 5_000_000.0}}
    matched = apply_filters(rows, [Filter(field="float_shares", op="lt", value=20_000_000)], derived)
    assert [r.symbol for r in matched] == ["AAA"]


# --- sorting ------------------------------------------------------------------


def test_sort_descending_and_ascending():
    rows = [_row("AAA", pct=1.0), _row("BBB", pct=9.0), _row("CCC", pct=5.0)]
    assert [r.symbol for r in sort_rows(rows, "pct_change", True)] == ["BBB", "CCC", "AAA"]
    assert [r.symbol for r in sort_rows(rows, "pct_change", False)] == ["AAA", "CCC", "BBB"]


def test_rows_without_a_sort_value_sink_in_both_directions():
    rows = [_row("AAA", spread=None), _row("BBB", spread=2.0), _row("CCC", spread=1.0)]
    assert [r.symbol for r in sort_rows(rows, "spread_pct", True)] == ["BBB", "CCC", "AAA"]
    # Still last ascending -- a None must never win a sort by being unorderable.
    assert [r.symbol for r in sort_rows(rows, "spread_pct", False)] == ["CCC", "BBB", "AAA"]


def test_text_sort_orders_as_text():
    rows = [_row("CCC"), _row("AAA"), _row("BBB")]
    assert [r.symbol for r in sort_rows(rows, "symbol", False)] == ["AAA", "BBB", "CCC"]


def test_unknown_sort_field_falls_back_instead_of_raising():
    rows = [_row("AAA", pct=1.0), _row("BBB", pct=9.0)]
    assert [r.symbol for r in sort_rows(rows, "nonsense", True)] == ["BBB", "AAA"]


# --- run_screen ---------------------------------------------------------------


def test_run_screen_reports_total_before_the_limit():
    rows = [_row(f"S{i}", pct=float(i)) for i in range(10)]
    result = run_screen(rows, Screen(filters=[Filter(field="pct_change", op="gt", value=0)], limit=3))

    assert len(result.rows) == 3
    # Without this a full page of results is indistinguishable from
    # exactly-a-full-page of results.
    assert result.total_matched == 9


def test_run_screen_trims_derived_values_to_returned_rows():
    rows = [_row("AAA"), _row("BBB"), _row("CCC")]
    derived = {"float_shares": {"AAA": 1.0, "BBB": 2.0, "CCC": 3.0}}
    result = run_screen(rows, Screen(sort_by="symbol", descending=False, limit=1), derived)

    assert [r.symbol for r in result.rows] == ["AAA"]
    # Shipping the whole universe's float map would dwarf the rows.
    assert result.values["float_shares"] == {"AAA": 1.0}


def test_run_screen_limit_is_never_zero():
    rows = [_row("AAA")]
    assert len(run_screen(rows, Screen(limit=0)).rows) == 1


# --- presets ------------------------------------------------------------------


def test_presets_reproduce_the_old_fixed_views():
    rows = [_row("UP", pct=8.0), _row("DOWN", pct=-8.0), _row("FLAT", pct=0.0, volume=9e6)]
    derived = {"rank_score": {"UP": 8.0, "DOWN": -8.0, "FLAT": 0.0}}

    gainers = run_screen(rows, PRESETS["gainers"]["screen"], derived)
    losers = run_screen(rows, PRESETS["losers"]["screen"], derived)
    active = run_screen(rows, PRESETS["most_active"]["screen"], derived)

    assert [r.symbol for r in gainers.rows] == ["UP"]
    assert [r.symbol for r in losers.rows] == ["DOWN"]
    # Direction-agnostic and ordered by dollar volume, so the flat but
    # heavily-traded name leads.
    assert active.rows[0].symbol == "FLAT"


def test_every_preset_references_only_real_fields():
    for name, preset in PRESETS.items():
        screen = preset["screen"]
        assert screen.sort_by in FIELDS_BY_NAME, name
        for f in screen.filters:
            assert f.field in FIELDS_BY_NAME, f"{name} -> {f.field}"
            spec = FIELDS_BY_NAME[f.field]
            assert f.op in spec.operators, f"{name} -> {f.field} {f.op}"


def test_moderate_movers_is_bounded_at_both_ends():
    """The upper bound carries the finding and is the part most likely to get
    "fixed" later: measured intraday, 3-8% won 58.7% out-of-sample while 8-13%
    won 34.3%. Dropping the ceiling to catch bigger movers inverts the screen
    rather than strengthening it, so both edges are pinned here.

    Ordered by dollar volume on purpose -- nothing ranked outcomes *within*
    the band out-of-sample, so the sort buys fill quality rather than making a
    prediction the data doesn't support.
    """
    rows = [
        _row("QUIET", pct=1.5, volume=9e6),
        _row("BAND_BIG", pct=4.0, volume=5e6),
        _row("BAND_SMALL", pct=7.5, volume=1e6),
        _row("TOOFAR", pct=11.0, volume=9e6),
        _row("BLOWOFF", pct=45.0, volume=9e6),
    ]
    result = run_screen(rows, PRESETS["moderate_movers"]["screen"])
    assert [r.symbol for r in result.rows] == ["BAND_BIG", "BAND_SMALL"]


def test_preset_list_exposes_editable_screens():
    entries = {p["name"]: p for p in preset_list()}
    # Presets travel as full filter specs so a UI can load one and let the
    # user edit it, rather than treating it as an opaque name.
    assert entries["gainers"]["screen"]["filters"][0]["field"] == "pct_change"
    assert entries["low_float_runners"]["label"]
