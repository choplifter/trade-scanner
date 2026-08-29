from datetime import datetime, timedelta, timezone

from app.scanners.formulas import is_fade_risk, is_momentum_alert, is_stale, rank_score, rvol


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


def test_rank_score_no_headline_applies_no_catalyst_discount():
    # No headline is no longer a no-op: _NO_CATALYST_DISCOUNT (0.1) applies
    # whenever catalyst_boost is on, same as the boost applies when there is
    # one -- see formulas._NO_CATALYST_DISCOUNT for why.
    assert rank_score(10.0, False, 5.0) == 1.0


def test_rank_score_headline_boosts():
    assert rank_score(10.0, True, 5.0) == 11.5


def test_rank_score_fade_risk_discounts():
    assert rank_score(10.0, False, 20.0) == 10.0 * 0.1 * 0.7


def test_rank_score_headline_and_fade_risk_both_apply():
    assert rank_score(10.0, True, 20.0) == 10.0 * 1.15 * 0.7


def test_rank_score_preserves_sign_for_negative_magnitudes():
    # Losers rank by negative pct_change, so the multipliers have to keep
    # working on a negative magnitude: the boost pushes it further negative
    # (bigger drop, ranks higher), the fade-risk *and* no-catalyst discounts
    # both pull it back toward zero (ranks lower), same as the positive/
    # gainers case. The losers view does pass catalyst_boost now (see the
    # engine ranking tests), so this is exactly the arithmetic it exercises.
    boosted = rank_score(-10.0, True, 5.0)
    discounted = rank_score(-10.0, False, 20.0)
    assert boosted < -10.0
    assert -10.0 < discounted < 0.0


def test_rank_score_catalyst_boost_disabled_ignores_a_headline():
    # Same inputs as test_rank_score_headline_boosts, boost suppressed.
    assert rank_score(10.0, True, 5.0, catalyst_boost=False) == 10.0


def test_rank_score_catalyst_boost_disabled_still_applies_fade_risk():
    # The fade-risk discount is direction-agnostic, so it stays on in every
    # view even where the catalyst boost is switched off.
    assert rank_score(10.0, True, 20.0, catalyst_boost=False) == 7.0
    assert rank_score(-10.0, True, 20.0, catalyst_boost=False) == -7.0


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


def test_rvol_without_a_session_fraction_is_the_full_day_ratio():
    # Unchanged behaviour: the pre-normalization semantics.
    assert rvol(500_000.0, 1_000_000.0) == 0.5


def test_rvol_session_fraction_scales_the_denominator_to_time_of_day():
    # 20k traded when only 2% of a typical 1M-share day is normally done means
    # today is running at ~1x pace, not the 0.02x a full-day comparison shows.
    assert rvol(20_000.0, 1_000_000.0, 0.02) == 1.0
    assert rvol(20_000.0, 1_000_000.0) == 0.02


def test_rvol_session_fraction_of_one_matches_the_unnormalized_value():
    assert rvol(500_000.0, 1_000_000.0, 1.0) == rvol(500_000.0, 1_000_000.0)


def test_rvol_zero_session_fraction_falls_back_to_full_day():
    # Guards against a malformed profile dividing by ~nothing and reporting a
    # five-figure RVOL.
    assert rvol(500_000.0, 1_000_000.0, 0.0) == 0.5


def test_rvol_zero_avg_volume_is_none():
    assert rvol(500_000.0, 0.0, 0.5) is None
