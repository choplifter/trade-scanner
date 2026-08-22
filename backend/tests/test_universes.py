"""Choosing which symbols a backtest runs over.

The case that matters is the one nothing else can catch. Selecting "symbols
that gapped" out of the window being tested picks the names *for* the days
being measured, and any strategy that trades movement then looks better than
it is -- silently, with a plausible number at the end. So the selection window
must end before the test window starts, and that is asserted directly rather
than inferred from a result.

Pure over stub bars: nothing here reaches Alpaca.
"""

from dataclasses import dataclass
from datetime import date, timedelta

import pytest

from app.alpaca.universe import UniverseSymbol
from app.scanners import universes


@dataclass
class _Bar:
    timestamp: date
    open: float
    close: float


def _series(opens_and_closes):
    """Daily bars, oldest first, one per trading day."""
    day = date(2026, 1, 1)
    return [
        _Bar(day + timedelta(days=i), open_, close)
        for i, (open_, close) in enumerate(opens_and_closes)
    ]


def _flat(n):
    return _series([(100.0, 100.0)] * n)


def _gapping(n, every=2):
    """A series where every `every`-th session opens well away from the
    prior close."""
    bars = []
    for i in range(n):
        gapped = i % every == 0 and i > 0
        bars.append((120.0 if gapped else 100.0, 100.0))
    return _series(bars)


def _universe(symbols):
    return {
        s: UniverseSymbol(
            symbol=s,
            exchange="NASDAQ",
            prev_close=100.0,
            avg_vol_20d=1_000_000.0,
            avg_dollar_vol_20d=float(1_000_000 * (len(symbols) - i)),
            shortable=True,
        )
        for i, s in enumerate(symbols)
    }


class _Clients:
    """Stands in for AlpacaClients with the one call select makes."""

    def __init__(self, bars):
        self._bars = bars
        self.requested_days = None


async def _select(kind, universe, bars, count=2, lookback_days=10, monkeypatch=None):
    async def _fake(clients, symbols, lookback_days):
        _fake.days = lookback_days
        return {s: bars[s] for s in symbols if s in bars}

    monkeypatch.setattr(universes, "get_daily_bars_multi", _fake)
    result = await universes.select(_Clients(bars), universe, kind, count, lookback_days)
    return result, getattr(_fake, "days", None)


# --- the liquid universe --------------------------------------------------


@pytest.mark.asyncio
async def test_liquid_is_the_most_traded_first():
    universe = _universe(["AAA", "BBB", "CCC"])

    result = await universes.select(None, universe, universes.LIQUID, 2, 10)

    assert result == ["AAA", "BBB"]


@pytest.mark.asyncio
async def test_liquid_needs_no_bar_fetch():
    """It is a sort over data build_universe already produced. Reaching for
    daily bars here would be a request per run for nothing."""
    result = await universes.select(None, _universe(["AAA"]), universes.LIQUID, 5, 10)

    assert result == ["AAA"]


@pytest.mark.asyncio
async def test_asking_for_more_than_exists_returns_what_there_is():
    result = await universes.select(None, _universe(["AAA"]), universes.LIQUID, 50, 10)

    assert result == ["AAA"]


# --- the gapper universe --------------------------------------------------


@pytest.mark.asyncio
async def test_gappers_are_the_ones_that_repeatedly_gap(monkeypatch):
    universe = _universe(["GAP", "CALM"])
    bars = {"GAP": _gapping(40), "CALM": _flat(40)}

    result, _ = await _select(universes.GAPPERS, universe, bars, count=2, monkeypatch=monkeypatch)

    assert result == ["GAP"]


@pytest.mark.asyncio
async def test_one_gap_is_an_event_not_a_habit(monkeypatch):
    """A single gap does not survive into the next window, which is where it
    would be measured."""
    universe = _universe(["ONCE"])
    bars = {"ONCE": _series([(100.0, 100.0)] * 20 + [(130.0, 100.0)] + [(100.0, 100.0)] * 19)}

    result, _ = await _select(universes.GAPPERS, universe, bars, monkeypatch=monkeypatch)

    assert result == []


@pytest.mark.asyncio
async def test_a_gap_down_counts_too(monkeypatch):
    """The universe is about names that open away from the close, not names
    that open higher -- the rules being tested trade both directions."""
    universe = _universe(["DOWN"])
    down = _series([(70.0 if i % 2 == 0 and i > 0 else 100.0, 100.0) for i in range(40)])

    result, _ = await _select(universes.GAPPERS, universe, {"DOWN": down}, monkeypatch=monkeypatch)

    assert result == ["DOWN"]


@pytest.mark.asyncio
async def test_more_gap_days_rank_first(monkeypatch):
    universe = _universe(["OFTEN", "SOMETIMES"])
    bars = {"OFTEN": _gapping(40, every=2), "SOMETIMES": _gapping(40, every=6)}

    result, _ = await _select(
        universes.GAPPERS, universe, bars, count=2, monkeypatch=monkeypatch
    )

    assert result[0] == "OFTEN"


# --- the look-ahead guard -------------------------------------------------


@pytest.mark.asyncio
async def test_gaps_inside_the_test_window_do_not_select_a_symbol(monkeypatch):
    """The case this module exists for. A symbol that was quiet beforehand and
    gapped only during the days being measured must not be chosen -- otherwise
    the universe is picked for the very sessions it will be scored on."""
    universe = _universe(["LATE"])
    # 10 calendar days of test window is ~7 sessions; put every gap inside it.
    late = _series([(100.0, 100.0)] * 25 + [(140.0, 100.0)] * 7)

    result, _ = await _select(universes.GAPPERS, universe, {"LATE": late}, monkeypatch=monkeypatch)

    assert result == []


@pytest.mark.asyncio
async def test_the_selection_window_is_longer_than_the_test_window(monkeypatch):
    """It has to hold the test window plus enough history in front of it for
    MIN_GAP_DAYS to mean anything."""
    _, days = await _select(
        universes.GAPPERS, _universe(["AAA"]), {"AAA": _flat(60)}, lookback_days=20,
        monkeypatch=monkeypatch,
    )

    assert days > 20
    assert days == 20 * (universes.SELECTION_MULTIPLE + 1)


# --- guards ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unknown_universe_is_refused():
    with pytest.raises(ValueError, match="universe must be one of"):
        await universes.select(None, _universe(["AAA"]), "penny-stocks", 5, 10)


@pytest.mark.asyncio
async def test_a_symbol_with_almost_no_history_is_skipped(monkeypatch):
    """A fresh listing cannot have a habit yet."""
    universe = _universe(["NEW"])

    result, _ = await _select(
        universes.GAPPERS, universe, {"NEW": _series([(100.0, 100.0)])}, monkeypatch=monkeypatch
    )

    assert result == []
