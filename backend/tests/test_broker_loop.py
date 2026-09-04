"""Per-user broker accounts downstream: the trade record scoped by user,
the services preferring an injected broker, and the trigger loop closing
each trigger on its owner's account."""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.core.config import Settings
from app.options.monitor import _service_for
from app.options.service import OptionsService
from app.trading.service import OrderService
from app.trading.trade_store import TradeStore
from app.trading.trades import Trade


def _settings(**overrides) -> Settings:
    kwargs = {"alpaca_api_key_id": "k", "alpaca_api_secret_key": "s", "trading_enabled": True}
    kwargs.update(overrides)
    return Settings(**kwargs)


def _trade(symbol: str, entry_order_id: str) -> Trade:
    return Trade(
        symbol=symbol, side="long",
        opened_at=datetime(2026, 9, 1, 14, 0, tzinfo=UTC), closed_at=datetime(2026, 9, 1, 15, 0, tzinfo=UTC),
        qty=10, entry_avg=10.0, exit_avg=11.0, pnl=10.0, pnl_pct=10.0, initial_stop=None, risk_per_share=None,
        r_multiple=None, entry_order_id=entry_order_id, exit_order_ids=["x1"], fill_count=2,
    )


def test_trade_store_scopes_trips_by_user_and_keeps_legacy_rows_for_the_admin(tmp_path):
    store = TradeStore(str(tmp_path / "db.sqlite3"))
    asyncio.run(store.init_schema())
    # Recorded before trips were per user: user_id 0.
    asyncio.run(store.upsert([_trade("AAA", "legacy")]))
    asyncio.run(store.upsert([_trade("BBB", "owner")], user_id=1))
    asyncio.run(store.upsert([_trade("CCC", "guest")], user_id=2))
    admin = {t["symbol"] for t in asyncio.run(store.all(user_id=1, include_legacy=True))}
    guest = {t["symbol"] for t in asyncio.run(store.all(user_id=2, include_legacy=False))}
    assert admin == {"AAA", "BBB"}
    assert guest == {"CCC"}
    # The default read (legacy callers) still sees the unscoped rows.
    assert {t["symbol"] for t in asyncio.run(store.all())} == {"AAA"}
    assert asyncio.run(store.all(user_id=2, include_legacy=False))[0]["user_id"] == 2


class _Broker:
    def __init__(self, name):
        self.name = name


def test_services_prefer_the_injected_broker():
    settings = _settings()
    mine = _Broker("mine")
    assert OrderService(None, settings, broker=mine)._trading is mine  # type: ignore[arg-type]
    assert OptionsService(None, settings, chain_cache=object(), broker=mine)._trading is mine  # type: ignore[arg-type]
    # Without one, the operator's clients as before (resolved lazily).
    class _Clients:
        trading = _Broker("env")

    assert OrderService(_Clients(), settings)._trading.name == "env"  # type: ignore[arg-type]
    assert OrderService(None, settings, live_available=True)._live_available is True  # type: ignore[arg-type]


@dataclass
class _Resolver:
    brokers: dict
    live: dict = field(default_factory=dict)

    async def client(self, user, account):
        return self.brokers.get((user["id"], account))

    async def availability(self, user):
        return {"paper": (user["id"], "paper") in self.brokers, "live": self.live.get(user["id"], False)}


def test_trigger_loop_builds_one_service_per_owner_and_account():
    settings = _settings()
    owner = {"id": 1, "username": "owner", "is_admin": True}
    guest = {"id": 2, "username": "guest", "is_admin": False}
    resolver = _Resolver({(1, "paper"): _Broker("owner-paper"), (2, "paper"): _Broker("guest-paper")}, live={2: True})
    owner_service = asyncio.run(_service_for(None, settings, None, object(), resolver, owner, "paper"))
    guest_service = asyncio.run(_service_for(None, settings, None, object(), resolver, guest, "paper"))
    assert owner_service._trading.name == "owner-paper"
    assert guest_service._trading.name == "guest-paper"
    assert guest_service._live_available is True and owner_service._live_available is False
    # No live pair for the owner -> no service, the trigger is parked.
    assert asyncio.run(_service_for(None, settings, None, object(), resolver, owner, "live")) is None
    # Unknown owner (deleted user) -> None too; without a resolver at all,
    # the pre-per-user behaviour (the operator's keys) still applies.
    assert asyncio.run(_service_for(None, settings, None, object(), resolver, None, "paper")) is None
    assert asyncio.run(_service_for(None, settings, None, object(), None, None, "paper")) is not None
    assert asyncio.run(_service_for(None, settings, None, object(), None, None, "live")) is None
