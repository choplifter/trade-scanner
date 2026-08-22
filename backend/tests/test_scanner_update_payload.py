"""The shape of a scanner_update message.

This exists because the two producers drifted. ws.scanner_ws built the
subscribe reply by hand and included window_minutes and
momentum_window_minutes; ScannerEngine.run_loop built the poll broadcast by
hand and did not. The client replaces its whole state from each message, so
the column headers rendered "30m %" and "60m" on subscribe and fell back to
their generic labels one tick later -- a wrong label, visible for the entire
session, with nothing raising anywhere.

TypeScript could not catch it either: the socket payload arrives as untyped
JSON and is cast to ScannerUpdate at the boundary, so a missing required
field is a runtime undefined rather than a compile error.

Both producers now call ScannerEngine.scanner_update_payload, and what is
asserted here is the field set itself -- the thing the client depends on and
the thing that silently went missing.
"""

from datetime import datetime, timezone

from app.core.config import Settings
from app.market_data.momentum import MOMENTUM_WINDOW_MINUTES
from app.scanners.engine import ScannerEngine
from app.scanners.schemas import ScannerRow

# Every key the frontend's ScannerUpdate interface declares as required
# (frontend/src/types/alpaca.ts). A producer dropping any one of them is the
# regression this file exists for.
_REQUIRED_KEYS = {
    "type",
    "scanner",
    "session",
    "is_latest_session",
    "window_minutes",
    "momentum_window_minutes",
    "rows",
}


def _engine(**settings_overrides):
    settings = Settings(alpaca_api_key_id="k", alpaca_api_secret_key="s", **settings_overrides)
    return ScannerEngine(
        clients=None,  # type: ignore[arg-type]
        settings=settings,
        universe={},
        manager=None,  # type: ignore[arg-type]
        fundamentals=None,  # type: ignore[arg-type]
        benchmark_tracker=None,  # type: ignore[arg-type]
        history_store=None,  # type: ignore[arg-type]
        news_cache=None,  # type: ignore[arg-type]
        momentum_cache=None,  # type: ignore[arg-type]
        http_client=None,  # type: ignore[arg-type]
    )


def _row(symbol="AAA"):
    return ScannerRow(
        symbol=symbol,
        exchange="NASDAQ",
        last_price=10.0,
        prev_close=9.0,
        pct_change=11.1,
        volume_today=1_000_000.0,
        avg_vol_20d=500_000.0,
        rvol=2.0,
        dollar_volume_today=10_000_000.0,
        updated_at=datetime.now(timezone.utc),
    )


def test_the_payload_carries_every_field_the_client_requires():
    payload = _engine().scanner_update_payload("gainers", [_row()])

    assert set(payload) == _REQUIRED_KEYS


def test_both_windows_are_present_and_not_none():
    """The specific regression. Present-but-None would blank the label just
    as thoroughly as absent, so it is not enough to assert the key exists."""
    payload = _engine().scanner_update_payload("gainers", [_row()])

    assert payload["momentum_window_minutes"] == MOMENTUM_WINDOW_MINUTES
    assert payload["window_minutes"] is not None


def test_the_momentum_window_is_derived_not_written_out():
    """It must track MOMENTUM_WINDOW itself. A literal here would go stale
    exactly the way the frontend's hardcoded "15m" did."""
    assert MOMENTUM_WINDOW_MINUTES == 30
    payload = _engine().scanner_update_payload("gainers", [])

    assert payload["momentum_window_minutes"] == MOMENTUM_WINDOW_MINUTES


def test_the_volume_window_follows_the_setting():
    """Only a custom screen can override it, but the client labels its column
    from this rather than assuming 60 -- so it has to be the real setting."""
    payload = _engine(scanner_volume_surge_window_minutes=45).scanner_update_payload("gainers", [])

    assert payload["window_minutes"] == 45


def test_rows_are_json_ready():
    """The websocket sends this straight to send_json/broadcast, so a
    datetime left on a row would raise at the socket rather than here."""
    payload = _engine().scanner_update_payload("gainers", [_row("AAA")])

    assert isinstance(payload["rows"][0]["updated_at"], str)
    assert payload["rows"][0]["symbol"] == "AAA"


def test_the_caller_may_pin_the_session_flag():
    """run_loop reads is_latest_session_fallback once per tick so every view
    in that tick agrees, instead of re-reading a property that could flip
    midway through the loop."""
    engine = _engine()

    pinned = engine.scanner_update_payload("gainers", [], is_latest_session=True)

    assert pinned["is_latest_session"] is True


def test_the_flag_defaults_to_the_engines_own():
    """The subscribe path passes nothing and must still report the truth."""
    engine = _engine()

    payload = engine.scanner_update_payload("gainers", [])

    assert payload["is_latest_session"] == engine.is_latest_session_fallback


def test_the_scanner_name_is_echoed_back():
    """The client keys its per-view state off this, so a wrong value would
    file one view's rows under another."""
    assert _engine().scanner_update_payload("losers", [])["scanner"] == "losers"
    assert _engine().scanner_update_payload("gainers", [])["type"] == "scanner_update"
