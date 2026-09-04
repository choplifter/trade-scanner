"""Per-user broker credentials: secrets at rest, the store, which account
the resolver hands out, and the guards' per-user live check."""

import asyncio
import sqlite3

import pytest

from app.broker.crypto import SecretBox, SecretUnreadable, secret_box_from_settings
from app.broker.resolver import BrokerResolver, account_summary
from app.broker.store import BrokerStore, key_hint
from app.core.config import Settings
from app.trading.errors import LiveTradingRefused, OrderRejected
from app.trading.guards import assert_can_trade, can_submit


def _settings(**overrides) -> Settings:
    kwargs = {
        "alpaca_api_key_id": "PKOPERATOR0001",
        "alpaca_api_secret_key": "operator-secret",
        "session_secret_key": "session-secret",
        "trading_enabled": True,
    }
    kwargs.update(overrides)
    return Settings(**kwargs)


ADMIN = {"id": 1, "username": "owner", "display_name": "Owner", "is_admin": True}
GUEST = {"id": 2, "username": "guest", "display_name": "Guest", "is_admin": False}


class _FakeClient:
    def __init__(self, key_id, secret, paper, *, fail=False):
        self.key_id, self.secret, self.paper, self.fail = key_id, secret, paper, fail

    def get_account(self):
        if self.fail:
            raise _ApiError(401)
        return {
            "account_number": f"ACCT-{self.key_id[-4:]}",
            "status": "ACTIVE",
            "options_trading_level": 3,
            "buying_power": "1000.5",
            "equity": "2000",
        }


class _ApiError(Exception):
    def __init__(self, status_code):
        super().__init__(f"status {status_code}")
        self.status_code = status_code


def _factory(calls, fail_for=()):
    def make(key_id, secret, paper):
        calls.append((key_id, secret, paper))
        return _FakeClient(key_id, secret, paper, fail=key_id in fail_for)

    return make


# --- crypto ---------------------------------------------------------------------


def test_secret_box_round_trips_and_rejects_another_key():
    box = SecretBox("one")
    token = box.encrypt("s3cr3t")
    assert token != "s3cr3t"
    assert box.decrypt(token) == "s3cr3t"
    with pytest.raises(SecretUnreadable):
        SecretBox("two").decrypt(token)
    with pytest.raises(ValueError):
        SecretBox("")


def test_box_from_settings_prefers_the_dedicated_key():
    a = secret_box_from_settings(_settings(broker_encryption_key="dedicated"))
    b = SecretBox("dedicated")
    assert b.decrypt(a.encrypt("x")) == "x"
    c = secret_box_from_settings(_settings())
    assert SecretBox("session-secret").decrypt(c.encrypt("y")) == "y"


# --- store ------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    s = BrokerStore(str(tmp_path / "db.sqlite3"), SecretBox("k"))
    asyncio.run(s.init_schema())
    return s


def test_store_encrypts_the_secret_and_never_lists_it(store, tmp_path):
    asyncio.run(store.set(2, "paper", "PKGUEST0000ABCD", "guest-secret", verified_account_number="ACCT-1"))
    row = asyncio.run(store.get(2, "paper"))
    assert row["api_secret_key"] == "guest-secret" and row["key_hint"] == "ABCD"
    listed = asyncio.run(store.list_for_user(2))
    assert len(listed) == 1 and "api_secret_key" not in listed[0] and "secret_encrypted" not in listed[0]
    raw = sqlite3.connect(str(tmp_path / "db.sqlite3")).execute("SELECT secret_encrypted FROM user_broker_keys").fetchone()[0]
    assert "guest-secret" not in raw
    assert asyncio.run(store.user_ids_with_keys()) == {2}
    assert asyncio.run(store.delete(2, "paper")) is True
    assert asyncio.run(store.get(2, "paper")) is None
    assert asyncio.run(store.delete(2, "paper")) is False


def test_store_replaces_a_pair_in_place_and_survives_a_rotated_key(tmp_path):
    path = str(tmp_path / "db.sqlite3")
    store = BrokerStore(path, SecretBox("k1"))
    asyncio.run(store.init_schema())
    asyncio.run(store.set(2, "live", "AKLIVE00001111", "first"))
    asyncio.run(store.set(2, "live", "AKLIVE00002222", "second"))
    assert asyncio.run(store.get(2, "live"))["api_secret_key"] == "second"
    rotated = BrokerStore(path, SecretBox("k2"))
    # Unreadable under the new key: reads as not connected, not as a crash.
    assert asyncio.run(rotated.get(2, "live")) is None
    with pytest.raises(ValueError):
        asyncio.run(store.set(2, "margin", "x" * 10, "y" * 10))


# --- resolver -----------------------------------------------------------------------


def test_resolver_prefers_the_users_keys_and_falls_back_to_env_only_for_the_admin(store):
    calls = []
    resolver = BrokerResolver(_settings(), store, client_factory=_factory(calls))
    # Nobody has stored keys: the admin gets .env, the guest nothing.
    admin_creds = asyncio.run(resolver.credentials(ADMIN, "paper"))
    assert admin_creds is not None and admin_creds.source == "env" and admin_creds.key_id == "PKOPERATOR0001"
    assert asyncio.run(resolver.credentials(GUEST, "paper")) is None
    assert asyncio.run(resolver.client(GUEST, "paper")) is None
    assert asyncio.run(resolver.availability(GUEST)) == {"paper": False, "live": False}
    assert asyncio.run(resolver.availability(ADMIN)) == {"paper": True, "live": False}
    # The guest connects their own paper pair.
    asyncio.run(store.set(2, "paper", "PKGUEST0000ABCD", "guest-secret", verified_account_number="ACCT-ABCD"))
    creds = asyncio.run(resolver.credentials(GUEST, "paper"))
    assert creds is not None and creds.source == "user" and creds.key_hint == "ABCD" and creds.paper is True
    client = asyncio.run(resolver.client(GUEST, "paper"))
    assert client.key_id == "PKGUEST0000ABCD" and client.paper is True
    # Cached per (user, account, key): the same object again, a new one after invalidate.
    assert asyncio.run(resolver.client(GUEST, "paper")) is client
    resolver.invalidate(2)
    assert asyncio.run(resolver.client(GUEST, "paper")) is not client
    # The admin's own stored keys win over .env too.
    asyncio.run(store.set(1, "paper", "PKOWNER00009999", "own-secret"))
    assert asyncio.run(resolver.credentials(ADMIN, "paper")).source == "user"


def test_resolver_live_needs_a_live_pair(store):
    calls = []
    resolver = BrokerResolver(_settings(alpaca_live_api_key_id="AKLIVE0000ZZZZ", alpaca_live_api_secret_key="s"), store, client_factory=_factory(calls))
    live = asyncio.run(resolver.credentials(ADMIN, "live"))
    assert live is not None and live.paper is False and live.key_hint == "ZZZZ"
    assert asyncio.run(resolver.credentials(GUEST, "live")) is None
    asyncio.run(store.set(2, "live", "AKGUEST0000LIVE", "gs"))
    client = asyncio.run(resolver.client(GUEST, "live"))
    assert client.paper is False


def test_verify_and_status(store):
    calls = []
    resolver = BrokerResolver(_settings(), store, client_factory=_factory(calls, fail_for={"PKBAD000000000"}))
    summary = asyncio.run(resolver.verify("PKGOOD00001234", "s", "paper"))
    assert summary["account_number"] == "ACCT-1234" and summary["options_trading_level"] == 3
    assert summary["buying_power"] == 1000.5 and summary["status"] == "ACTIVE"
    with pytest.raises(OrderRejected) as exc:
        asyncio.run(resolver.verify("PKBAD000000000", "s", "paper"))
    assert "rejected" in exc.value.message
    assert asyncio.run(resolver.status(GUEST, "paper")) == {"account": "paper", "connected": False, "source": None, "key_hint": None}
    asyncio.run(store.set(2, "paper", "PKGOOD00001234", "s", verified_account_number="ACCT-1234"))
    status = asyncio.run(resolver.status(GUEST, "paper"))
    assert status["connected"] is True and status["source"] == "user" and status["account_number"] == "ACCT-1234"
    assert status["options_trading_level"] == 3 and "api_secret_key" not in status
    asyncio.run(store.set(2, "paper", "PKBAD000000000", "s"))
    resolver.invalidate(2)
    status = asyncio.run(resolver.status(GUEST, "paper"))
    assert status["connected"] is False and status["error"]


def test_account_summary_reads_sdk_objects_and_dicts():
    class _Acct:
        account_number = "A1"
        status = "ACTIVE"
        options_trading_level = None
        options_approved_level = 2
        buying_power = "10"
        equity = None

    assert account_summary(_Acct()) == {"account_number": "A1", "status": "ACTIVE", "options_trading_level": 2, "buying_power": 10.0, "equity": None}


def test_key_hint():
    assert key_hint("PKABCDEFGH") == "EFGH" and key_hint("ab") == "ab"


# --- guards --------------------------------------------------------------------------


def test_live_availability_overrides_the_env_answer():
    settings = _settings(trading_allow_live=True)
    # No live keys in .env, but the user has their own pair.
    assert_can_trade(settings, "live", "LIVE", live_available=True)
    assert can_submit(settings, "live", live_available=True) is True
    with pytest.raises(LiveTradingRefused):
        assert_can_trade(settings, "live", "LIVE", live_available=False)
    assert can_submit(settings, "live", live_available=False) is False
    # None keeps the .env answer.
    with pytest.raises(LiveTradingRefused):
        assert_can_trade(settings, "live", "LIVE")
    env_live = _settings(trading_allow_live=True, alpaca_live_api_key_id="k", alpaca_live_api_secret_key="s")
    assert_can_trade(env_live, "live", "LIVE")
    with pytest.raises(LiveTradingRefused):
        assert_can_trade(env_live, "live", "LIVE", live_available=False)
