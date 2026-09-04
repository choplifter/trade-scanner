"""The live sim fill loop leaves replaying users to the replay loop."""

from app.trading.sim.loop import symbols_to_price


def test_replaying_users_are_not_priced_live():
    by_user = {
        1: {"AAPL": [{"id": "a"}], "SPY": [{"id": "b"}]},
        2: {"SPY": [{"id": "c"}], "TSLA": [{"id": "d"}]},
        3: {"NVDA": [{"id": "e"}]},
    }
    assert symbols_to_price(by_user, set()) == ["AAPL", "NVDA", "SPY", "TSLA"]
    assert symbols_to_price(by_user, {2}) == ["AAPL", "NVDA", "SPY"]
    assert symbols_to_price(by_user, {1, 2}) == ["NVDA"]
    assert symbols_to_price({}, {1}) == []
