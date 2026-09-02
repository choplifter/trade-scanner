from datetime import date

import pytest

from app.options.occ import format_occ, parse_occ, try_parse_occ


def test_round_trip_call():
    parsed = parse_occ("SPY251219C00650000")
    assert parsed.root == "SPY"
    assert parsed.underlying == "SPY"
    assert parsed.expiry == date(2025, 12, 19)
    assert parsed.kind == "call"
    assert parsed.strike == 650.0
    assert format_occ("SPY", date(2025, 12, 19), "call", 650) == "SPY251219C00650000"


def test_fractional_strike_and_put():
    parsed = parse_occ("QQQ260918P00652500")
    assert parsed.kind == "put"
    assert parsed.strike == 652.5
    assert format_occ("QQQ", date(2026, 9, 18), "put", 652.5) == "QQQ260918P00652500"


def test_adjusted_root_keeps_root_but_strips_underlying():
    parsed = parse_occ("SPY1251219C00650000")
    assert parsed.root == "SPY1"
    assert parsed.underlying == "SPY"


def test_lower_case_and_whitespace_are_tolerated():
    assert parse_occ(" spy251219c00650000 ").symbol == "SPY251219C00650000"


@pytest.mark.parametrize(
    "bad", ["SPY", "SPY251219X00650000", "SPY25121C00650000", "SPY251299C00650000", ""]
)
def test_malformed_symbols_raise(bad):
    with pytest.raises(ValueError):
        parse_occ(bad)
    assert try_parse_occ(bad) is None


def test_try_parse_none():
    assert try_parse_occ(None) is None
