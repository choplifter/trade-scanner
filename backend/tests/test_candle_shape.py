from app.market_data.candle_shape import is_marubozu


def test_is_marubozu_full_body_bullish():
    assert is_marubozu(open_=10.0, high=11.0, low=10.0, close=11.0) is True


def test_is_marubozu_full_body_bearish():
    assert is_marubozu(open_=11.0, high=11.0, low=10.0, close=10.0) is True


def test_is_marubozu_false_with_large_wicks():
    # Body is 10.4->10.6 (0.2), range is 10.0->11.0 (1.0) -- wick is 80% of range.
    assert is_marubozu(open_=10.4, high=11.0, low=10.0, close=10.6) is False


def test_is_marubozu_false_zero_range():
    assert is_marubozu(open_=10.0, high=10.0, low=10.0, close=10.0) is False


def test_is_marubozu_respects_custom_threshold():
    # Wick is exactly 20% of range -- passes a looser threshold, fails the default.
    assert is_marubozu(open_=10.2, high=11.0, low=10.0, close=11.0, max_wick_ratio=0.2) is True
    assert is_marubozu(open_=10.2, high=11.0, low=10.0, close=11.0) is False
