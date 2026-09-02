"""The options write paths start at the same gate as the equity ones, plus
the account's options level. Proven with clients=None, like
test_trading_guard: reaching the SDK would raise AttributeError instead."""

import asyncio
from datetime import date

import pytest

from app.core.config import Settings
from app.options.guards import assert_options_level
from app.options.models import CloseLeg, CloseSpreadRequest, SpreadTicket
from app.options.service import OptionsService
from app.trading.errors import LiveConfirmationRequired, LiveTradingRefused, OptionsNotApproved, TradingDisabled


def _ticket() -> SpreadTicket:
    return SpreadTicket(
        underlying="SPY", strategy="bull_put", expiry=date(2026, 9, 18), qty=1, long_strike=740, short_strike=745
    )


def _close() -> CloseSpreadRequest:
    return CloseSpreadRequest(
        legs=[CloseLeg(symbol="SPY260918P00740000", qty=1), CloseLeg(symbol="SPY260918P00745000", qty=-1)], qty=1
    )


def _service(account="paper", **overrides) -> OptionsService:
    settings = Settings(alpaca_api_key_id="k", alpaca_api_secret_key="s", **overrides)
    return OptionsService(clients=None, settings=settings, chain_cache=object(), account=account)  # type: ignore[arg-type]


def test_options_level_gate():
    assert_options_level(3, 3, "bull put spread")
    with pytest.raises(OptionsNotApproved) as exc:
        assert_options_level(2, 3, "bull put spread")
    assert exc.value.field == "strategy" and "level 2" in exc.value.message
    with pytest.raises(OptionsNotApproved):
        assert_options_level(None, 3, "iron condor")


def test_submit_refuses_before_touching_the_broker_when_disabled():
    with pytest.raises(TradingDisabled):
        asyncio.run(_service(trading_enabled=False).submit(_ticket()))


def test_close_refuses_before_touching_the_broker_when_disabled():
    with pytest.raises(TradingDisabled):
        asyncio.run(_service(trading_enabled=False).close_spread(_close()))


def test_live_submit_needs_switch_and_confirmation():
    with pytest.raises(LiveTradingRefused):
        asyncio.run(
            _service("live", trading_enabled=True, alpaca_live_api_key_id="lk", alpaca_live_api_secret_key="ls").submit(
                _ticket(), confirm="LIVE"
            )
        )
    with pytest.raises(LiveConfirmationRequired):
        asyncio.run(
            _service(
                "live",
                trading_enabled=True,
                trading_allow_live=True,
                alpaca_live_api_key_id="lk",
                alpaca_live_api_secret_key="ls",
            ).submit(_ticket())
        )
