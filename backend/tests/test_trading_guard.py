"""The paper guard. The most important test in this feature.

Two switches, both of which must be on before any write path runs. They are
deliberately independent: TRADING_ENABLED means merging this cannot change
anyone's behaviour until they opt in, and the paper check means opting in
still cannot reach real money.
"""

import pytest

from app.core.config import Settings
from app.trading.errors import LiveTradingRefused, TradingDisabled
from app.trading.service import OrderService, _plain


def _service(**overrides) -> OrderService:
    settings = Settings(
        alpaca_api_key_id="k", alpaca_api_secret_key="s", **overrides
    )
    return OrderService(clients=None, settings=settings)  # type: ignore[arg-type]


def test_write_paths_refuse_when_trading_is_disabled():
    """The default. Pulling this branch must not arm anything."""
    with pytest.raises(TradingDisabled):
        _service(trading_enabled=False, alpaca_paper=True)._assert_can_trade()


def test_write_paths_refuse_against_a_live_account():
    """Even fully switched on, a non-paper account is refused outright.

    This is the whole safety posture: ALPACA_PAPER is a one-character edit,
    so it must not be the only thing between this app and real money.
    """
    with pytest.raises(LiveTradingRefused):
        _service(trading_enabled=True, alpaca_paper=False)._assert_can_trade()


def test_disabled_takes_precedence_over_live():
    """Both wrong -- the message should be about the switch the user actually
    controls, not send them off to change ALPACA_PAPER."""
    with pytest.raises(TradingDisabled):
        _service(trading_enabled=False, alpaca_paper=False)._assert_can_trade()


def test_both_switches_on_and_paper_is_allowed():
    _service(trading_enabled=True, alpaca_paper=True)._assert_can_trade()


def test_refusals_carry_a_structured_detail_the_ui_can_branch_on():
    """Mirrors the screener's 422 refusal shape, which the frontend already
    knows how to read -- see BacktestRefusedError."""
    try:
        _service(trading_enabled=True, alpaca_paper=False)._assert_can_trade()
    except LiveTradingRefused as exc:
        detail = exc.to_detail()
    assert detail["code"] == "live_trading_refused"
    assert "paper" in detail["message"].lower()


def test_alpaca_decimal_strings_survive_serialisation():
    """Alpaca sends money as decimal strings to avoid float rounding. They
    must arrive intact rather than being coerced here -- the frontend parses
    at the point of display."""
    from decimal import Decimal

    assert _plain({"equity": Decimal("100000.00")}) == {"equity": "100000.00"}
    assert _plain(None) is None
    assert _plain([Decimal("1.5"), None]) == ["1.5", None]


# --- the live account -----------------------------------------------------
# A second gate on top of the two above: the live prefix needs its own keys,
# TRADING_ALLOW_LIVE, and the typed confirmation on every write.

from app.trading.errors import LiveConfirmationRequired  # noqa: E402
from app.trading.guards import limits_for  # noqa: E402


def _live_service(**overrides) -> OrderService:
    settings = Settings(
        alpaca_api_key_id="k",
        alpaca_api_secret_key="s",
        trading_enabled=True,
        alpaca_paper=True,
        **overrides,
    )
    return OrderService(clients=None, settings=settings, account="live")  # type: ignore[arg-type]


def test_live_refuses_without_live_keys():
    with pytest.raises(LiveTradingRefused):
        _live_service(trading_allow_live=True)._assert_can_trade("LIVE")


def test_live_refuses_without_the_allow_switch():
    with pytest.raises(LiveTradingRefused):
        _live_service(
            alpaca_live_api_key_id="lk", alpaca_live_api_secret_key="ls"
        )._assert_can_trade("LIVE")


def test_live_refuses_without_the_typed_confirmation():
    service = _live_service(
        alpaca_live_api_key_id="lk", alpaca_live_api_secret_key="ls", trading_allow_live=True
    )
    with pytest.raises(LiveConfirmationRequired):
        service._assert_can_trade(None)
    with pytest.raises(LiveConfirmationRequired):
        service._assert_can_trade("live")  # exact word, upper case


def test_live_passes_with_keys_switch_and_confirmation():
    _live_service(
        alpaca_live_api_key_id="lk", alpaca_live_api_secret_key="ls", trading_allow_live=True
    )._assert_can_trade("LIVE")


def test_paper_ignores_the_confirmation_entirely():
    _service(trading_enabled=True, alpaca_paper=True)._assert_can_trade(None)


def test_live_ceilings_are_the_smaller_ones():
    settings = Settings(alpaca_api_key_id="k", alpaca_api_secret_key="s")
    paper, live = limits_for(settings, "paper"), limits_for(settings, "live")
    assert live.max_order_notional < paper.max_order_notional
    assert live.max_order_qty < paper.max_order_qty
    assert live.max_option_contracts < paper.max_option_contracts
    assert live.account == "live" and paper.account == "paper"
