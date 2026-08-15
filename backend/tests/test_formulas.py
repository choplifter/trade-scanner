from datetime import datetime, timedelta, timezone

from app.scanners.formulas import is_fade_risk, is_momentum_alert, is_stale, rank_score


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


def test_is_fade_risk_false_below_threshold():
    assert is_fade_risk(14.9) is False


def test_is_fade_risk_true_above_threshold():
    assert is_fade_risk(15.1) is True


def test_is_fade_risk_false_when_none():
    assert is_fade_risk(None) is False


def test_rank_score_no_headline_no_fade_risk_is_unchanged():
    assert rank_score(10.0, False, 5.0) == 10.0


def test_rank_score_headline_boosts():
    assert rank_score(10.0, True, 5.0) == 11.5


def test_rank_score_fade_risk_discounts():
    assert rank_score(10.0, False, 20.0) == 7.0


def test_rank_score_headline_and_fade_risk_both_apply():
    assert rank_score(10.0, True, 20.0) == 10.0 * 1.15 * 0.7


def test_rank_score_preserves_sign_for_losers():
    # Losers rank by negative pct_change -- a catalyst should push it
    # further negative (bigger drop, ranks higher), a fade-risk discount
    # should pull it back toward zero (ranks lower), same multipliers as
    # the positive/gainers case.
    boosted = rank_score(-10.0, True, 5.0)
    discounted = rank_score(-10.0, False, 20.0)
    assert boosted < -10.0
    assert -10.0 < discounted < 0.0


def test_is_momentum_alert_none_pct_change_is_false():
    assert is_momentum_alert(None, True, True, True, threshold=5.0) is False


def test_is_momentum_alert_below_threshold_is_false():
    assert is_momentum_alert(3.0, True, True, True, threshold=5.0) is False


def test_is_momentum_alert_requires_shaved_top():
    assert is_momentum_alert(8.0, False, True, True, threshold=5.0) is False
    assert is_momentum_alert(8.0, True, True, True, threshold=5.0) is True


def test_is_momentum_alert_requires_green_candle():
    assert is_momentum_alert(8.0, True, False, True, threshold=5.0) is False


def test_is_momentum_alert_requires_above_vwap():
    assert is_momentum_alert(8.0, True, True, False, threshold=5.0) is False


def test_is_momentum_alert_down_move_never_alerts():
    # Long side only -- a downward move never alerts regardless of
    # magnitude or how the other flags happen to be set.
    assert is_momentum_alert(-8.0, True, True, True, threshold=5.0) is False


def test_is_momentum_alert_exactly_at_threshold_is_true():
    assert is_momentum_alert(5.0, True, True, True, threshold=5.0) is True
