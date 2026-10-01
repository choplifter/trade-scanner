"""Two pools out of one bar pass (app.alpaca.universe.build_universes).

The scanner hunts intraday catalysts in cheap, fast single names; an option
is written on something else entirely. Measured 2026-10-01 against the live
pool: all sixty of the options screener's candidates priced between 15 and
100 dollars, because the scanner's universe is where they came from, and MU
at 1,070 -- a symbol a condor had just been built on by hand -- was not in
it at all. Index ETFs were missing for a second reason: the scanner drops
them by name, since a leveraged ETF moving with its index crowds out real
single-name setups.

Both filters are right for the scanner. Neither is right for options, so
options get their own pool -- out of the same bars, because fetching twenty
daily bars for every tradable US equity twice would double a startup that
already takes a minute and a half.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.alpaca.universe import build_universe, build_universes
from app.core.config import Settings


@dataclass
class _Asset:
    symbol: str
    name: str
    tradable: bool = True
    shortable: bool = True

    @property
    def exchange(self):
        return _Exchange()


class _Exchange:
    value = "NASDAQ"


@dataclass
class _Bar:
    close: float
    volume: float


class _BarSet:
    def __init__(self, data):
        self.data = data


# price, average daily volume, name
BOARD = {
    "MU": (1070.0, 500_000, "Micron Technology Inc"),
    "AAPL": (330.0, 40_000_000, "Apple Inc"),
    "SPY": (763.0, 60_000_000, "SPDR S&P 500 ETF Trust"),
    "HBAN": (15.2, 20_000_000, "Huntington Bancshares Inc"),
    "PENNY": (3.0, 50_000_000, "Penny Co"),
    "QUIET": (120.0, 1_000, "Quiet Industries Inc"),
}


class _Trading:
    def get_all_assets(self, _request):
        return [_Asset(symbol=s, name=v[2]) for s, v in BOARD.items()]


class _Data:
    def get_stock_bars(self, request):
        symbols = request.symbol_or_symbols
        return _BarSet(
            {s: [_Bar(close=BOARD[s][0], volume=BOARD[s][1])] * 20 for s in symbols if s in BOARD}
        )


class _Clients:
    trading = _Trading()
    data = _Data()
    feed = "sip"


def _settings(**over) -> Settings:
    return Settings(alpaca_api_key_id="k", alpaca_api_secret_key="s", **over)


def _build(**over):
    # The scanner band this user actually runs with (see the .env override).
    base = {"universe_min_price": 2.0, "universe_max_price": 100.0, "universe_min_avg_volume": 300_000}
    base.update(over)
    return asyncio.run(build_universes(_Clients(), _settings(**base)))


def test_the_options_pool_holds_the_expensive_names_the_scanner_drops():
    """The whole point: MU at 1,070 is far outside a 2-100 scanner band and
    is exactly the kind of underlying a condor is written on."""
    pools = _build()
    assert "MU" not in pools.scanner
    assert "MU" in pools.options
    assert "AAPL" not in pools.scanner and "AAPL" in pools.options


def test_etfs_reach_the_options_pool_and_still_stay_out_of_the_scanners():
    pools = _build()
    assert "SPY" not in pools.scanner, "a leveraged/index ETF still crowds out single-name setups"
    assert "SPY" in pools.options, "SPY is the most written-on underlying there is"


def test_a_cheap_liquid_name_is_in_both_when_it_clears_both_bars():
    pools = _build()
    assert "HBAN" in pools.scanner and "HBAN" in pools.options


def test_a_penny_name_is_in_neither():
    pools = _build()
    assert "PENNY" not in pools.options, "15 dollars is where strikes start being spaced usefully"


def test_a_quiet_name_is_kept_out_of_the_options_pool_by_dollar_volume():
    """Liquidity is judged in dollars, not shares: it is the only proxy for
    a tradable chain available before the chain is fetched."""
    pools = _build()
    assert "QUIET" not in pools.options
    # 120 x 1,000 is 120,000 a day -- three orders under the floor.
    assert BOARD["QUIET"][0] * BOARD["QUIET"][1] < 20_000_000


def test_the_options_pool_can_be_switched_back_to_single_names():
    pools = _build(options_universe_etfs=False)
    assert "SPY" not in pools.options and "MU" in pools.options


def test_each_pool_keeps_its_own_cap_and_takes_the_most_liquid_first():
    pools = _build(max_options_universe_size=2)
    assert len(pools.options) == 2
    # SPY and AAPL trade the most dollars of the six.
    assert set(pools.options) == {"SPY", "AAPL"}


def test_the_old_entry_point_still_answers_with_the_scanner_pool_alone():
    """build_universe is what startup and the tests around it call; it must
    keep returning exactly what it always did."""
    scanner = asyncio.run(build_universe(_Clients(), _settings(universe_min_price=2.0, universe_max_price=100.0)))
    assert scanner == _build().scanner


def test_both_pools_come_out_of_one_pass_over_the_bars():
    """A second pass would double a startup that already takes ninety
    seconds, which is the reason these are built together at all."""
    calls = []

    class _CountingData(_Data):
        def get_stock_bars(self, request):
            calls.append(request.symbol_or_symbols)
            return super().get_stock_bars(request)

    class _CountingClients(_Clients):
        data = _CountingData()

    asyncio.run(build_universes(_CountingClients(), _settings()))
    assert len(calls) == 1, "one batch here, and never one batch per pool"


def test_a_span_of_twenty_sessions_is_asked_for():
    """The averages are 20-day; a shorter window would quietly change what
    'average dollar volume' means in both pools at once."""
    seen = {}

    class _RecordingData(_Data):
        def get_stock_bars(self, request):
            seen["start"] = request.start
            return super().get_stock_bars(request)

    class _RecordingClients(_Clients):
        data = _RecordingData()

    asyncio.run(build_universes(_RecordingClients(), _settings()))
    # alpaca-py normalises the request's start, sometimes dropping the
    # offset, so the comparison is made on whichever it handed back.
    start = seen["start"]
    now = datetime.now(timezone.utc) if start.tzinfo else datetime.now(timezone.utc).replace(tzinfo=None)
    assert start < now - timedelta(days=28)
