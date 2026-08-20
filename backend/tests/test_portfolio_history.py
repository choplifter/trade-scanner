"""The account equity curve, and the two corrections applied to it.

Both corrections exist because of what Alpaca actually returns rather than
what the docs imply, so both are pinned here: a period reaching past account
inception arrives zero-padded, and a daily series stops at the last completed
session.
"""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.trading.errors import OrderRejected
from app.trading.service import (
    _HISTORY_RANGES,
    OrderService,
    _curve_points,
    _with_live_point,
)


def _history(equity, *, start_t=1_000, step=86_400, profit_loss=None, profit_loss_pct=None):
    n = len(equity)
    return SimpleNamespace(
        timestamp=[start_t + i * step for i in range(n)],
        equity=list(equity),
        profit_loss=list(profit_loss) if profit_loss is not None else [0.0] * n,
        profit_loss_pct=list(profit_loss_pct) if profit_loss_pct is not None else [0.0] * n,
        base_value=100_000.0,
        timeframe="1D",
    )


# --- the zero padding ---------------------------------------------------


def test_leading_zero_padding_is_dropped():
    """Observed on the real account: a 1M curve on a two-week-old account
    came back as 23 points, the first 11 of them equity=0.0 for sessions
    before the account existed. Plotted raw that is a line from $0 to the
    opening balance -- a 100% gain that never happened."""
    points = _curve_points(_history([0.0, 0.0, 0.0, 100_000.0, 100_500.0]))

    assert [p["equity"] for p in points] == [100_000.0, 100_500.0]


def test_a_zero_after_funding_is_kept():
    """Only the *leading* run is padding. A zero once the account has held
    money is a real balance, and silently dropping it would hide a total
    loss -- the single most important point on the chart."""
    points = _curve_points(_history([0.0, 100_000.0, 0.0, 5_000.0]))

    assert [p["equity"] for p in points] == [100_000.0, 0.0, 5_000.0]


def test_an_all_zero_series_yields_no_points():
    assert _curve_points(_history([0.0, 0.0])) == []


def test_timestamps_are_carried_through_positionally():
    """The arrays are parallel, so trimming has to keep them aligned -- an
    off-by-one here would date every point to the wrong session."""
    history = _history([0.0, 100_000.0, 101_000.0], start_t=1_000, step=10)
    points = _curve_points(history)

    assert [p["t"] for p in points] == [1_010, 1_020]


# --- units --------------------------------------------------------------


def test_profit_loss_pct_is_converted_from_a_fraction():
    """Alpaca reports it the way it reports a position's unrealized_plpc: as
    a fraction. Verified against the live account, where -0.0289 came back
    alongside a -2.89% move."""
    history = _history([100_000.0, 97_110.0], profit_loss_pct=[0.0, -0.0289])
    points = _curve_points(history)

    assert points[-1]["profit_loss_pct"] == pytest.approx(-2.89)


def test_missing_values_become_none_rather_than_zero():
    """A gap is not a flat day, and 0.0 would plot as one."""
    history = _history([100_000.0, 101_000.0], profit_loss=[None, None], profit_loss_pct=[None, None])
    points = _curve_points(history)

    assert all(p["profit_loss"] is None and p["profit_loss_pct"] is None for p in points)


# --- extending a daily curve to now -------------------------------------


def test_live_point_is_appended_to_a_stale_daily_curve():
    """A 1D-timeframe series stops at the last completed session, so without
    this the Balance tab would end at yesterday's close while the Account tab
    showed today's equity -- the same number in two places, disagreeing."""
    points = _curve_points(_history([100_000.0, 100_262.08]))
    extended = _with_live_point(points, 97_318.08)

    assert len(extended) == len(points) + 1
    assert extended[-1]["equity"] == 97_318.08
    assert extended[-1]["profit_loss"] == pytest.approx(97_318.08 - 100_262.08)
    assert extended[-1]["profit_loss_pct"] == pytest.approx(-2.9366, abs=1e-3)


def test_live_point_is_not_appended_behind_the_last_sample():
    """Guards against a point dated in the past, which would break the
    ascending-time ordering the chart requires."""
    future = int(datetime.now(timezone.utc).timestamp()) + 86_400
    points = [{"t": future, "equity": 100.0, "profit_loss": None, "profit_loss_pct": None}]

    assert _with_live_point(points, 200.0) == points


def test_live_point_on_an_empty_curve_is_the_only_point():
    extended = _with_live_point([], 100.0)

    assert len(extended) == 1
    assert extended[0]["equity"] == 100.0
    assert extended[0]["profit_loss"] is None


# --- the service ---------------------------------------------------------


class _FakeTrading:
    def __init__(self, history, equity="97318.08"):
        self._history = history
        self._equity = equity
        self.requests = []

    def get_portfolio_history(self, request):
        self.requests.append(request)
        return self._history

    def get_account(self):
        return {"equity": self._equity}


def _service(history, equity="97318.08"):
    trading = _FakeTrading(history, equity)
    settings = Settings(alpaca_api_key_id="k", alpaca_api_secret_key="s")
    clients = SimpleNamespace(trading=trading)
    return OrderService(clients, settings), trading  # type: ignore[arg-type]


def test_unknown_range_is_refused_rather_than_sent_to_the_broker():
    service, trading = _service(_history([100_000.0]))

    with pytest.raises(OrderRejected) as exc:
        asyncio.run(service.portfolio_history("6H"))

    assert exc.value.to_detail()["field"] == "range"
    assert trading.requests == []


def test_period_and_timeframe_are_chosen_server_side():
    """The valid timeframe depends on the period's length -- 1H over 1M is a
    400 from Alpaca -- so the pair is never the caller's to assemble."""
    service, trading = _service(_history([100_000.0, 101_000.0]))
    asyncio.run(service.portfolio_history("1y"))

    assert (trading.requests[0].period, trading.requests[0].timeframe) == _HISTORY_RANGES["1Y"]


def test_change_is_measured_from_the_first_plotted_point():
    """Not from Alpaca's base_value, which belongs to the untrimmed series
    and can sit inside the zero-padded region -- dividing by a balance the
    account never held."""
    service, _ = _service(_history([0.0, 0.0, 100_000.0, 110_000.0]), equity="110000.0")
    result = asyncio.run(service.portfolio_history("1M"))

    assert result["start_equity"] == 100_000.0
    assert result["end_equity"] == 110_000.0
    assert result["change"] == pytest.approx(10_000.0)
    assert result["change_pct"] == pytest.approx(10.0)


def test_intraday_range_is_not_extended_with_the_live_point():
    """Intraday series already run to the current moment; appending there
    would add a duplicate sample for no gain."""
    service, _ = _service(_history([100_000.0, 99_000.0]), equity="12345.0")
    result = asyncio.run(service.portfolio_history("1D"))

    assert [p["equity"] for p in result["points"]] == [100_000.0, 99_000.0]


def test_every_advertised_range_maps_to_a_period_and_timeframe():
    for key, (period, timeframe) in _HISTORY_RANGES.items():
        assert key == key.upper()
        assert period and timeframe
