from app.market_data.candle_shape import is_shaved_bottom, is_shaved_top


def test_is_shaved_top_true_when_close_at_high():
    assert is_shaved_top(open_=10.0, high=11.0, low=10.0, close=11.0) is True


def test_is_shaved_top_ignores_a_large_lower_wick():
    # Gapped up, sold off hard intraday (low=8.0), then rallied back to
    # close right at the high -- still a shaved top since only the upper
    # wick matters here.
    assert is_shaved_top(open_=10.0, high=11.0, low=8.0, close=11.0) is True


def test_is_shaved_top_false_with_large_upper_wick():
    # Closed well below the high -- upper wick is 40% of range.
    assert is_shaved_top(open_=10.4, high=11.0, low=10.0, close=10.6) is False


def test_is_shaved_top_false_zero_range():
    assert is_shaved_top(open_=10.0, high=10.0, low=10.0, close=10.0) is False


def test_is_shaved_top_respects_custom_threshold():
    assert is_shaved_top(open_=10.0, high=11.0, low=10.0, close=10.8, max_wick_ratio=0.25) is True
    assert is_shaved_top(open_=10.0, high=11.0, low=10.0, close=10.8) is False


def test_is_shaved_bottom_true_when_close_at_low():
    assert is_shaved_bottom(open_=11.0, high=11.0, low=10.0, close=10.0) is True


def test_is_shaved_bottom_ignores_a_large_upper_wick():
    # Gapped down, rallied hard intraday (high=13.0), then sold back off to
    # close right at the low -- still a shaved bottom since only the lower
    # wick matters here.
    assert is_shaved_bottom(open_=11.0, high=13.0, low=10.0, close=10.0) is True


def test_is_shaved_bottom_false_with_large_lower_wick():
    # Closed well above the low -- lower wick is 40% of range.
    assert is_shaved_bottom(open_=10.6, high=11.0, low=10.0, close=10.4) is False


def test_is_shaved_bottom_false_zero_range():
    assert is_shaved_bottom(open_=10.0, high=10.0, low=10.0, close=10.0) is False


def test_is_shaved_bottom_respects_custom_threshold():
    assert is_shaved_bottom(open_=11.0, high=11.0, low=10.0, close=10.2, max_wick_ratio=0.25) is True
    assert is_shaved_bottom(open_=11.0, high=11.0, low=10.0, close=10.2) is False
