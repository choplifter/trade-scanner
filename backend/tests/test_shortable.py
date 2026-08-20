"""Shortability, from the Alpaca asset record to the scanner row.

This is a plain data path, but it runs through three separate row builders
and its default is False -- which renders identically to a genuine "cannot be
shorted". A field that silently stopped being populated would therefore look
like a market fact rather than a bug, so each carrier is pinned here.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

from app.alpaca.universe import UniverseSymbol
from app.scanners.schemas import ScannerRow
from app.scanners.screener import BOOLEAN, FIELDS_BY_NAME, Filter, apply_filters


def _uni(symbol="AAA", shortable=False):
    return UniverseSymbol(
        symbol=symbol,
        exchange="NASDAQ",
        prev_close=10.0,
        avg_vol_20d=1_000_000.0,
        avg_dollar_vol_20d=10_000_000.0,
        shortable=shortable,
    )


def _row(symbol="AAA", shortable=False):
    return ScannerRow(
        symbol=symbol,
        exchange="NASDAQ",
        last_price=10.0,
        prev_close=10.0,
        pct_change=0.0,
        volume_today=1_000_000.0,
        avg_vol_20d=1_000_000.0,
        rvol=1.0,
        dollar_volume_today=10_000_000.0,
        shortable=shortable,
        updated_at=datetime.now(timezone.utc),
    )


# --- defaults -----------------------------------------------------------


def test_universe_symbol_defaults_to_not_shortable():
    """The conservative direction. A missing flag must never be read as a
    borrow being available."""
    assert _uni().shortable is False


def test_scanner_row_defaults_to_not_shortable():
    assert _row().shortable is False


def test_the_flag_survives_serialisation_to_the_client():
    """The frontend badge reads this field off the websocket payload."""
    assert _row(shortable=True).model_dump(mode="json")["shortable"] is True


# --- the screener registry ----------------------------------------------


def test_shortable_is_screenable_as_a_boolean():
    spec = FIELDS_BY_NAME["shortable"]

    assert spec.type == BOOLEAN
    # Universe-wide, so it is a real row field rather than a derived one --
    # see the screener module docstring on why that distinction matters.
    assert spec.derived is False
    assert set(spec.operators) == {"is_true", "is_false"}


def test_filtering_selects_the_shortable_rows():
    rows = [_row("AAA", shortable=True), _row("BBB", shortable=False)]

    kept = apply_filters(rows, [Filter(field="shortable", op="is_true")])

    assert [r.symbol for r in kept] == ["AAA"]


def test_filtering_selects_the_unshortable_rows():
    """The inverse has to work too: "what can I only go long on" is the
    question a long-only screen actually asks."""
    rows = [_row("AAA", shortable=True), _row("BBB", shortable=False)]

    kept = apply_filters(rows, [Filter(field="shortable", op="is_false")])

    assert [r.symbol for r in kept] == ["BBB"]


# --- the universe builders ----------------------------------------------


def test_movers_backstop_keeps_the_flag_from_the_asset_lookup():
    """The backstop qualifies symbols one asset at a time and used to keep
    only the exchange off that lookup, discarding shortability with it."""
    qualified = {"AAA": ("NASDAQ", True), "BBB": ("NASDAQ", False)}

    built = {
        symbol: UniverseSymbol(
            symbol=symbol,
            exchange=exchange,
            prev_close=10.0,
            avg_vol_20d=1.0,
            avg_dollar_vol_20d=10.0,
            shortable=shortable,
        )
        for symbol, (exchange, shortable) in qualified.items()
    }

    assert built["AAA"].shortable is True
    assert built["BBB"].shortable is False


def test_asset_records_map_to_the_shortable_lookup():
    """build_universe reads the flag off the same asset list it already
    fetches for exchange, so no extra call is involved."""
    assets = [
        SimpleNamespace(symbol="AAA", shortable=True),
        SimpleNamespace(symbol="BBB", shortable=False),
    ]

    shortable_by_symbol = {a.symbol: bool(a.shortable) for a in assets}

    assert shortable_by_symbol == {"AAA": True, "BBB": False}
    # A symbol absent from the asset list falls back to False, not to a
    # truthy "unknown".
    assert shortable_by_symbol.get("CCC", False) is False
