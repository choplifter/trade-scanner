"""The sector-attribution page's rendering. Everything under test here is a
pure function over row dicts, so none of it needs a browser, a running Dash
server or an Alpaca call.
"""

from app.dash_app.pages.sector_attribution import (
    _BANNERS,
    _SECTOR_COLUMNS,
    _SECTOR_SORT_KEYS,
    _SYMBOL_COLUMNS,
    _SYMBOL_SORT_KEYS,
    _figure,
    _sector_rows,
    _sorted_rows,
    _symbol_rows,
    layout,
)
from app.scanners import sector_attribution as sa


def _attributed(symbol="AAA", sector="Healthcare", pct=10.0, specific=9.6):
    return {
        "symbol": symbol,
        "company_name": "A Corp",
        "sector": sector,
        "etf": "XLV" if sector else None,
        "pct_change": pct,
        "market_pct": 0.5 if sector else None,
        "sector_pct": 0.4 if sector else None,
        "sector_excess_pct": -0.1 if sector else None,
        "stock_specific_pct": specific if sector else None,
        "sector_share": 0.04 if sector else None,
        "verdict": sa.VERDICT_INDEPENDENT if sector else sa.VERDICT_UNKNOWN,
        "recent_headline": None,
    }


def _rolled(sector="Healthcare", count=2, concentration=2.1):
    return {
        "sector": sector,
        "etf": "XLV",
        "sector_pct": 0.4,
        "count": count,
        "share": 0.3,
        "concentration": concentration,
        "avg_pct": 12.0,
        "avg_stock_specific_pct": 11.6,
        "verdicts": {sa.VERDICT_INDEPENDENT: count},
    }


# --- columns match the rows ---------------------------------------------


def test_every_declared_sector_column_is_produced():
    """A column declared but never written renders as a silently blank
    column rather than failing."""
    row = _sector_rows([_rolled()])[0]

    assert [c["id"] for c in _SECTOR_COLUMNS if c["id"] not in row] == []


def test_every_declared_symbol_column_is_produced():
    row = _symbol_rows([_attributed()])[0]

    assert [c["id"] for c in _SYMBOL_COLUMNS if c["id"] not in row] == []


def test_every_sort_key_reads_a_field_that_exists():
    """Sorting is custom, so a key naming a field that was never emitted
    would raise on the first header click rather than at import."""
    sector_row = _sector_rows([_rolled()])[0]
    symbol_row = _symbol_rows([_attributed()])[0]

    for key in _SECTOR_SORT_KEYS.values():
        key(sector_row)
    for key in _SYMBOL_SORT_KEYS.values():
        key(symbol_row)


# --- formatting ----------------------------------------------------------


def test_missing_attribution_renders_as_a_dash_not_a_zero():
    row = _symbol_rows([_attributed(sector=None)])[0]

    assert row["stock_specific_pct"] == "—"
    assert row["sector"] == sa.UNKNOWN_SECTOR
    assert row["stock_specific_num"] is None


def test_the_verdict_carries_a_numeric_rank_for_colouring():
    """Colour and sort come off the number: filter_query compares more
    reliably against numbers than against strings containing spaces."""
    row = _symbol_rows([_attributed()])[0]

    assert row["verdict_rank"] == sa.VERDICT_RANK[sa.VERDICT_INDEPENDENT]


def test_percentages_keep_a_parallel_numeric_field():
    row = _symbol_rows([_attributed(pct=-3.25)])[0]

    assert row["pct_change"] == "-3.25%"
    assert row["pct_change_num"] == -3.25


# --- sorting -------------------------------------------------------------


def test_sorting_is_numeric_not_lexicographic():
    """The trap this helper exists for: as strings, "-8.66%" sorts after
    "-22.00%"."""
    rows = _symbol_rows([_attributed("AAA", pct=-8.66), _attributed("BBB", pct=-22.0)])

    ordered = _sorted_rows(rows, [{"column_id": "pct_change", "direction": "asc"}], _SYMBOL_SORT_KEYS)

    assert [r["symbol_plain"] for r in ordered] == ["BBB", "AAA"]


def test_rows_without_a_value_sort_to_the_end():
    rows = _symbol_rows([_attributed("AAA"), _attributed("ZZZ", sector=None)])

    ordered = _sorted_rows(
        rows, [{"column_id": "stock_specific_pct", "direction": "desc"}], _SYMBOL_SORT_KEYS
    )

    assert ordered[-1]["symbol_plain"] == "ZZZ"


# --- figure --------------------------------------------------------------


def test_the_figure_stacks_relative_so_mixed_signs_land_correctly():
    """barmode="stack" would draw a negative sector contribution on the
    positive side of zero."""
    figure = _figure([_attributed()])

    assert figure.layout.barmode == "relative"
    assert len(figure.data) == 3


def test_the_figure_skips_rows_with_nothing_to_attribute():
    figure = _figure([_attributed(sector=None)])

    assert len(figure.data) == 0


def test_an_empty_figure_still_renders():
    assert _figure([]).layout.height == 320


# --- layout and empty states ---------------------------------------------


def test_layout_builds_without_a_bound_backend():
    """layout() runs on every page visit, including before the FastAPI
    lifespan has bound backend_state."""
    assert layout() is not None


def test_every_empty_state_says_what_is_wrong():
    """Five different reasons for an empty page; one blank table for all of
    them would be indistinguishable from a broken backend."""
    assert set(_BANNERS) == {"no_engine", "no_rows", "fallback", "no_etf"}
    for message in _BANNERS.values():
        assert message and message[-1] == "."


def test_the_closed_market_banner_explains_the_mismatch():
    """The one empty state that is a correctness guard rather than cosmetics:
    last session's stock moves against today's ETF prices."""
    assert "different days" in _BANNERS["fallback"]
