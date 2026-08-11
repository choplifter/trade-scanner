from datetime import datetime, timedelta, timezone

from app.scanners.formulas import is_stale


def test_is_stale_true_when_no_last_trade_at():
    assert is_stale(None, datetime.now(timezone.utc), 300.0) is True


def test_is_stale_false_within_threshold():
    now = datetime.now(timezone.utc)
    last_trade_at = now - timedelta(seconds=60)
    assert is_stale(last_trade_at, now, 300.0) is False


def test_is_stale_true_past_threshold():
    now = datetime.now(timezone.utc)
    last_trade_at = now - timedelta(seconds=600)
    assert is_stale(last_trade_at, now, 300.0) is True


def test_is_stale_exactly_at_threshold_is_not_stale():
    now = datetime.now(timezone.utc)
    last_trade_at = now - timedelta(seconds=300)
    assert is_stale(last_trade_at, now, 300.0) is False
