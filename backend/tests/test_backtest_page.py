"""Dash backtest page rendering. Everything under test here is a pure
function over a report dict, so none of it needs a browser, a running Dash
server or an Alpaca call.
"""

from app.dash_app.pages.backtest import (
    _BUCKET_COLUMNS,
    _bucket_rows,
    _daily_report_layout,
    _noise_flag,
    _stats_cell,
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
