"""Dash backtest page rendering. Everything under test here is a pure
function over a report dict, so none of it needs a browser, a running Dash
server or an Alpaca call.
"""

from app.dash_app.pages.backtest import (
    _BUCKET_COLUMNS,
    _SLICE_COLUMNS,
    _STRATEGY_COLUMNS,
    _bucket_rows,
    _daily_report_layout,
    _noise_flag,
    _stats_cell,
    _strategy_row,
)
from app.scanners import bucket_analysis

_FLOOR = bucket_analysis.MIN_SAMPLE_SIZE


def _bucket(sample_size: int, view: str = "most_active", label: str = "30-60%") -> dict:
    return {
        "view": view,
        "bucket": label,
        "sample_size": sample_size,
        "win_rate": 72.7,
        "avg_return": 1.17,
    }


def test_noise_flag_marks_only_under_the_floor():
    assert _noise_flag(_FLOOR - 1) == f"noisy (n<{_FLOOR})"
    assert _noise_flag(_FLOOR) == ""
    assert _noise_flag(_FLOOR + 1) == ""


def test_group_and_bucket_tables_use_identical_wording():
    # Two spellings of one warning would read as two different warnings.
    thin_stats = {"sample_size": 11, "win_rate": 72.7, "avg_return": 1.17}
    assert _stats_cell(thin_stats)["flag"] == _bucket_rows([_bucket(11)])[0]["flag"]


def test_bucket_rows_flag_a_thin_bucket():
    # The real row that prompted this: an 11-pick 30-60% gap bucket whose
    # 72.7% win rate looks like a finding next to the ~49% the big buckets
    # show, and is three picks away from 45%.
    row = _bucket_rows([_bucket(11)])[0]
    assert row["flag"] == f"noisy (n<{_FLOOR})"
    # The numbers themselves are still shown -- the floor is a reading aid,
    # not a filter, and hiding the row would hide that it's under-sampled.
    assert row["sample_size"] == 11
    assert row["win_rate"] == "72.7%"


def test_bucket_rows_leave_a_healthy_bucket_unflagged():
    assert _bucket_rows([_bucket(2049, view="gainers", label="<15%")])[0]["flag"] == ""


def test_bucket_table_declares_the_flag_column():
    # Without the column the row's flag value never reaches the page, which
    # is exactly the bug this covers.
    assert "flag" in {column["id"] for column in _BUCKET_COLUMNS}


def test_daily_report_layout_builds_with_flagged_buckets():
    report = {
        "sample_size": 12,
        "symbol_count": 2,
        "symbols_with_bars": 2,
        "lookback_days": 180,
        "horizon_days": 1,
        "gap_buckets": [_bucket(11), _bucket(2049, view="gainers", label="<15%")],
        "rvol_buckets": [_bucket(11, label="5-15x")],
        "fade_risk": {
            "threshold": 15.0,
            "views": [
                {
                    "view": view,
                    "rvol_above_threshold": {"sample_size": 0, "win_rate": None, "avg_return": None},
                    "rvol_at_or_below_threshold": {"sample_size": 11, "win_rate": 72.7, "avg_return": 1.17},
                    "sufficient_sample": False,
                }
                for view in bucket_analysis.VIEWS
            ],
        },
        "shaved_top": [
            {
                "view": view,
                "shaved_top": {"sample_size": 4, "win_rate": 50.0, "avg_return": 0.1},
                "not_shaved_top": {"sample_size": 8, "win_rate": 50.0, "avg_return": 0.1},
                "sufficient_sample": False,
            }
            for view in bucket_analysis.VIEWS
        ],
    }
    assert _daily_report_layout(report) is not None


# --- the strategy-rules section ---------------------------------------------


def _slice(trades: int = 300, expectancy: float | None = 0.21) -> dict:
    return {
        "trades": trades,
        "expectancy_r": expectancy,
        "win_rate": 44.0,
        "avg_win_r": 2.1,
        "avg_loss_r": -1.0,
        "ambiguous_pct": 6.5,
    }


def test_a_strategy_row_carries_both_the_text_and_the_number():
    """The colouring is a filter query over expectancy_r, and the formatted
    "+0.21" cannot be compared to zero -- so the row needs both."""
    row = _strategy_row(_slice(), "strategy", "orb_break")
    assert row["strategy"] == "orb_break"
    assert row["expectancy"] == "+0.21"
    assert row["expectancy_r"] == 0.21
    assert row["win_rate"] == "44.0%"
    assert row["avg_loss"] == "-1.00"


def test_a_bucket_with_no_signals_reads_as_nothing_not_as_break_even():
    row = _strategy_row(_slice(trades=0, expectancy=None), "bucket", "12:00-14:00 midday")
    assert row["expectancy"] == "—"
    assert row["trades"] == 0
    # Sorting and colouring need a number; it must not be mistaken for a
    # measured zero, which is why the displayed cell stays a dash.
    assert row["expectancy_r"] == 0


def test_a_thin_strategy_is_flagged_with_the_same_wording_as_every_other_table():
    assert _strategy_row(_slice(trades=_FLOOR - 1), "strategy", "retest")["flag"] == f"noisy (n<{_FLOOR})"
    assert _strategy_row(_slice(trades=_FLOOR), "strategy", "retest")["flag"] == ""


def test_both_strategy_tables_declare_every_column_the_rows_fill():
    for columns, label_id in ((_STRATEGY_COLUMNS, "strategy"), (_SLICE_COLUMNS, "bucket")):
        declared = {c["id"] for c in columns}
        row = _strategy_row(_slice(), label_id, "x")
        # expectancy_r is deliberately undeclared: it is the sort/colour key,
        # not a column the reader sees.
        assert declared <= set(row), declared - set(row)
        assert label_id in declared
