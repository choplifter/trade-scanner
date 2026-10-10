"""Alpaca's option activities are read page by page: the first hundred
alone left the newest expiries out."""

import asyncio

from app.alpaca.activities import fetch_option_activities


class _Client:
    def __init__(self, total: int):
        self.rows = [{"id": f"a{i:04d}", "activity_type": "OPEXP", "symbol": f"S{i}", "date": "2026-10-09"} for i in range(total)]
        self.calls: list[dict] = []

    def get(self, path, params):
        self.calls.append(dict(params))
        start = 0
        if "page_token" in params:
            start = next(i for i, r in enumerate(self.rows) if r["id"] == params["page_token"]) + 1
        return self.rows[start : start + params["page_size"]]


def test_every_page_is_read_and_the_newest_expiry_is_there():
    client = _Client(250)
    rows = asyncio.run(fetch_option_activities(client, page_size=100))
    assert len(rows) == 250 and rows[-1]["id"] == "a0249"
    assert [c.get("page_token") for c in client.calls] == [None, "a0099", "a0199"]


def test_a_short_first_page_is_the_only_call_and_failure_keeps_what_was_read():
    client = _Client(30)
    assert len(asyncio.run(fetch_option_activities(client, page_size=100))) == 30
    assert len(client.calls) == 1

    class _Broken(_Client):
        def get(self, path, params):
            if "page_token" in params:
                raise RuntimeError("timeout")
            return super().get(path, params)

    assert len(asyncio.run(fetch_option_activities(_Broken(150), page_size=100))) == 100


def test_an_expired_contract_no_longer_held_closes_at_zero_before_alpaca_books_it():
    from datetime import date, datetime, timezone

    from app.trading.trades import Fill, fills_for_lapsed_options, fills_from_activities, round_trips

    opened = datetime(2026, 10, 9, 14, 40, tzinfo=timezone.utc)
    fills = [
        Fill("SPY261009P00773000", "sell", 12, 0.15, opened, "o-short"),
        Fill("SPY261009P00771000", "buy", 12, 0.06, opened, "o-long"),
        Fill("QQQ261120P00700000", "sell", 1, 5.45, opened, "o-q"),
    ]
    _closed, still_open = round_trips(fills)
    lapsed = fills_for_lapsed_options(still_open, held={"QQQ261120P00700000"}, today=date(2026, 10, 10))
    assert sorted(f.order_id for f in lapsed) == ["lapsed:SPY261009P00771000", "lapsed:SPY261009P00773000"]
    closed, still_open = round_trips(fills + lapsed)
    assert abs(sum(t.pnl for t in closed) - 108.0) < 0.01 and list(still_open) == ["QQQ261120P00700000"]
    # Still held, or not yet expired: left open.
    assert fills_for_lapsed_options({"SPY261009P00773000": -12}, held={"SPY261009P00773000"}, today=date(2026, 10, 10)) == []
    assert fills_for_lapsed_options({"SPY261009P00773000": -12}, held=set(), today=date(2026, 10, 9)) == []
    # Alpaca's own expiry activity arriving later is skipped: already flat.
    late = [{"id": "act-1", "activity_type": "OPEXP", "symbol": "SPY261009P00773000", "date": "2026-10-12", "qty": "12"}]
    assert fills_from_activities(late, fills + lapsed) == []
