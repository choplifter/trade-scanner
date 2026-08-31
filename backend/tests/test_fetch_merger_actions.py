"""Parsing Alpaca's merger corporate-action announcements into
app.alpaca.universe.MergerAction -- pure over a fake clients.trading, no
real Alpaca call.
"""

import asyncio
from dataclasses import dataclass
from datetime import date

from app.alpaca.universe import fetch_merger_actions


@dataclass
class _Announcement:
    target_symbol: str | None
    initiating_symbol: str | None = None
    ca_sub_type: object = None
    cash: float | None = None
    declaration_date: date | None = None
    ex_date: date | None = None


class _SubType:
    def __init__(self, value: str):
        self.value = value


class _Clients:
    class _Trading:
        def __init__(self, announcements):
            self._announcements = announcements

        def get_corporate_announcements(self, request):
            return self._announcements

    def __init__(self, announcements):
        self.trading = self._Trading(announcements)


def test_parses_a_cash_merger_announcement():
    clients = _Clients(
        [
            _Announcement(
                target_symbol="AAA",
                initiating_symbol="BIGCO",
                ca_sub_type=_SubType("merger_update"),
                cash=42.5,
                declaration_date=date(2026, 8, 1),
                ex_date=date(2026, 9, 1),
            )
        ]
    )
    actions = asyncio.run(fetch_merger_actions(clients))
    assert set(actions) == {"AAA"}
    action = actions["AAA"]
    assert action.acquirer_symbol == "BIGCO"
    assert action.sub_type == "merger_update"
    assert action.cash_consideration == 42.5
    assert action.announced_date == date(2026, 8, 1)
    assert action.effective_date == date(2026, 9, 1)


def test_skips_announcements_with_no_target_symbol():
    clients = _Clients([_Announcement(target_symbol=None, initiating_symbol="BIGCO")])
    actions = asyncio.run(fetch_merger_actions(clients))
    assert actions == {}


def test_most_recently_declared_announcement_wins_for_the_same_target():
    clients = _Clients(
        [
            _Announcement(
                target_symbol="AAA",
                initiating_symbol="OLDBIDDER",
                declaration_date=date(2026, 7, 1),
            ),
            _Announcement(
                target_symbol="AAA",
                initiating_symbol="NEWBIDDER",
                declaration_date=date(2026, 8, 15),
            ),
        ]
    )
    actions = asyncio.run(fetch_merger_actions(clients))
    assert actions["AAA"].acquirer_symbol == "NEWBIDDER"


def test_a_stock_for_stock_deal_has_no_cash_consideration():
    clients = _Clients(
        [_Announcement(target_symbol="AAA", ca_sub_type=_SubType("stock"), cash=None)]
    )
    actions = asyncio.run(fetch_merger_actions(clients))
    assert actions["AAA"].cash_consideration is None
    assert actions["AAA"].sub_type == "stock"


def test_a_fetch_failure_degrades_to_no_actions_rather_than_raising():
    class _BrokenTrading:
        def get_corporate_announcements(self, request):
            raise RuntimeError("rate limited")

    class _BrokenClients:
        trading = _BrokenTrading()

    actions = asyncio.run(fetch_merger_actions(_BrokenClients()))
    assert actions == {}
