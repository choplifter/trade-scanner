"""Option round trips put back together into the spreads they were traded
as: legs opened together, how each ended, the spread's P/L and the day and
week subtotals."""

from datetime import datetime, timedelta, timezone

import pytest

from app.options.closed_spreads import group_spreads, in_range, leg_outcome, summarize_spreads

OPEN = datetime(2026, 10, 9, 14, 40, 12, tzinfo=timezone.utc)
EXPIRY_CLOSE = datetime(2026, 10, 9, 20, 0, tzinfo=timezone.utc)


def _row(symbol, side, qty, entry, exit_, pnl, opened=OPEN, closed=EXPIRY_CLOSE, exits=("OPEXP-1",), mult=100):
    return {
        "id": f"{symbol}:{side}:{opened.isoformat()}:0",
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "entry_avg": entry,
        "exit_avg": exit_,
        "pnl": pnl,
        "opened_at": opened.isoformat(),
        "closed_at": closed.isoformat(),
        "exit_order_ids": list(exits),
        "multiplier": mult,
    }


def test_a_bull_put_that_expired_worthless_is_one_spread_with_the_whole_credit():
    rows = [
        _row("SPY261009P00773000", "short", 12, 0.15, 0.0, 180.0, exits=("act-1",)),
        _row("SPY261009P00771000", "long", 12, 0.06, 0.0, -72.0, exits=("act-2",)),
    ]
    [spread] = group_spreads(rows, {"act-1": "OPEXP", "act-2": "OPEXP"})
    assert spread["underlying"] == "SPY" and spread["strategy"] == "bull_put"
    assert spread["qty"] == 12 and spread["outcome"] == "expired"
    assert spread["entry_net"] == pytest.approx(-0.09), "a 9-cent credit, received"
    assert spread["exit_net"] == 0 and spread["pnl"] == pytest.approx(108.0)
    assert [leg["strike"] for leg in spread["legs"]] == [771.0, 773.0]


def test_a_condor_closed_side_by_side_stays_one_spread_and_lists_both_endings():
    later = OPEN + timedelta(days=3)
    rows = [
        _row("QQQ261120P00670000", "long", 1, 2.96, 2.80, -16.0, closed=later, exits=("ord-9",)),
        _row("QQQ261120P00700000", "short", 1, 5.45, 5.20, 25.0, closed=later, exits=("ord-9",)),
        _row("QQQ261120C00790000", "short", 1, 7.31, 0.0, 731.0, exits=("expiry:x",)),
        _row("QQQ261120C00820000", "long", 1, 2.10, 0.0, -210.0, exits=("expiry:y",)),
    ]
    [spread] = group_spreads(rows)
    assert spread["strategy"] == "iron_condor"
    assert spread["outcome"] == "closed + expired"
    assert spread["closed_at"] == later.isoformat(), "the last leg's close"
    assert spread["pnl"] == pytest.approx(530.0)


def test_stock_trades_and_separate_openings_are_kept_apart():
    rows = [
        _row("SPY", "long", 100, 770.0, 775.0, 500.0, mult=1),
        _row("USO261009P00146000", "short", 20, 0.10, 0.0, 200.0),
        _row("USO261009P00145000", "long", 20, 0.03, 0.0, -60.0),
        _row("USO261009P00140000", "short", 1, 0.05, 0.0, 5.0, opened=OPEN + timedelta(hours=1)),
    ]
    spreads = group_spreads(rows)
    assert sorted(s["strategy"] for s in spreads) == ["bull_put", "short_put"]
    assert all(s["underlying"] != "SPY" for s in spreads)


def test_how_a_leg_ended():
    assert leg_outcome({"exit_order_ids": ["assign:SPY:1"], "exit_avg": 0}, {}) == "assigned"
    assert leg_outcome({"exit_order_ids": ["a1"], "exit_avg": 0}, {"a1": "OPASN"}) == "assigned"
    assert leg_outcome({"exit_order_ids": ["o1"], "exit_avg": 0.4}, {}) == "closed"
    assert leg_outcome({"exit_order_ids": ["o2"], "exit_avg": 0}, {}) == "expired"


def test_day_and_week_subtotals_and_the_range():
    friday, monday = OPEN, OPEN + timedelta(days=3)
    rows = [
        _row("SPY261009P00773000", "short", 1, 0.15, 0.0, 15.0),
        _row("SPY261009P00771000", "long", 1, 0.06, 0.0, -6.0),
        _row("IWM261012P00270000", "short", 1, 0.50, 2.50, -200.0, opened=monday, closed=monday + timedelta(hours=1), exits=("o",)),
    ]
    spreads = group_spreads(rows)
    totals = summarize_spreads(spreads)
    assert totals["summary"]["count"] == 2 and totals["summary"]["wins"] == 1
    assert totals["summary"]["pnl"] == pytest.approx(-191.0)
    assert [d["date"] for d in totals["days"]] == ["2026-10-12", "2026-10-09"]
    assert [w["week_start"] for w in totals["weeks"]] == ["2026-10-12", "2026-10-05"]
    assert len(in_range(spreads, monday.replace(hour=0))) == 1


def test_the_day_change_is_equity_against_yesterdays_close():
    from app.options.service import _day_change

    assert _day_change("99486.19", "99238.00") == pytest.approx(248.19)
    assert _day_change("1000", None) is None
    assert _day_change(None, "1000") is None
