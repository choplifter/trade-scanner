"""Comparing expiries (app.options.optimize.compare_horizons): one run per
horizon, with the target named in implied moves so the rows mean the same
thing at each."""

import asyncio
from dataclasses import replace
from datetime import date, timedelta

import pytest

from app.options.chain import ExpiryInfo
from app.options.optimize import OptimizeRequest, compare_horizons, sweep_horizons
from app.trading.errors import OrderRejected
from tests.test_options_optimize_endpoint import FAR, MID, NEAR, NOW, SPOT, TODAY, _Service


def _infos(*expiries):
    return [ExpiryInfo(expiry=e, dte=(e - TODAY).days, contract_count=40) for e in expiries]


def _req(**over) -> OptimizeRequest:
    base = {"underlying": "x", "target_moves": 1.0, "horizon_expiry": NEAR}
    base.update(over)
    return OptimizeRequest(**base)


def _blind(chain):
    """The same chain with no implied volatility anywhere -- a board whose
    quotes carry no IV, which is what leaves a horizon unable to size an
    implied move. Rebuilt rather than edited: the quotes are frozen."""
    blank = lambda q: None if q is None else replace(q, iv=None)  # noqa: E731
    return replace(chain, rows=[replace(row, call=blank(row.call), put=blank(row.put)) for row in chain.rows])


def _run(service, req, **kw):
    return asyncio.run(compare_horizons(service, "x", req, today=TODAY, now=NOW, **kw))


# --- which horizons ---------------------------------------------------------


def test_one_horizon_per_distance_and_never_the_same_one_twice():
    board = _infos(*[TODAY + timedelta(days=d) for d in (2, 8, 16, 33, 47, 90)])
    picked = sweep_horizons(board, TODAY)
    assert picked == sorted(set(picked))
    assert len(picked) == 4
    # Nearest 7, 14, 30 and 45 days of what is listed.
    assert [(e - TODAY).days for e in picked] == [8, 16, 33, 47]


def test_a_thin_board_answers_with_what_it_has():
    picked = sweep_horizons(_infos(TODAY + timedelta(days=20), TODAY + timedelta(days=60)), TODAY)
    assert [(e - TODAY).days for e in picked] == [20, 60]


def test_an_expiry_today_is_not_a_horizon():
    assert sweep_horizons(_infos(TODAY, TODAY + timedelta(days=30)), TODAY) == [TODAY + timedelta(days=30)]


# --- the target travels as implied moves ------------------------------------


def test_the_target_is_a_different_price_at_every_horizon():
    """The point of the sweep: one implied move is further out the longer
    the horizon, so "+1 sigma" is the same claim and a different price."""
    out = _run(_Service(), _req())
    targets = [(r["dte"], r["target"]["low"], r["implied_move"]) for r in out["runs"] if "target" in r]
    assert len(targets) >= 2
    for dte, low, move in targets:
        assert low == pytest.approx(SPOT + move, abs=0.01)
    # Further out, a bigger move: sqrt of time, never the other way.
    moves = [move for _dte, _low, move in targets]
    assert moves == sorted(moves)


def test_both_sides_for_a_directional_view():
    out = _run(_Service(), _req(target_moves=1.0, target_moves_both=True, outlook="directional"))
    run = next(r for r in out["runs"] if "target" in r)
    assert run["target"]["points"][0] < SPOT < run["target"]["points"][-1]


def test_a_price_target_still_works_and_is_the_same_at_every_horizon():
    out = _run(_Service(), OptimizeRequest(underlying="x", target_low=104.0, horizon_expiry=NEAR))
    lows = {r["target"]["low"] for r in out["runs"] if "target" in r}
    assert lows == {104.0}


# --- the rows ---------------------------------------------------------------


def test_every_horizon_is_a_row_with_its_best_structure():
    out = _run(_Service(expiries=(NEAR, MID, FAR)), _req())
    assert [r["dte"] for r in out["runs"]] == sorted(r["dte"] for r in out["runs"])
    for row in out["runs"]:
        if "unavailable" in row:
            continue
        assert row["best"] is None or row["best"]["rank"] == 1
        assert len(row["results"]) <= 3, "a sweep previews a handful per horizon, not a dozen"


def test_a_horizon_whose_structures_cannot_be_priced_still_answers():
    """A broker that refuses every preview leaves rows with no structure --
    the comparison says "nothing here" per horizon rather than failing."""
    out = _run(_Service(preview_error=OrderRejected("no market", field="legs")), _req())
    assert out["runs"] and all("unavailable" in r or r["best"] is None for r in out["runs"])
    assert all(r.get("results") == [] for r in out["runs"] if "results" in r)


def test_a_symbol_with_no_expiries_is_refused_outright():
    with pytest.raises(OrderRejected):
        _run(_Service(expiries=()), _req())


def test_a_horizon_without_an_atm_iv_says_so_instead_of_targeting_zero():
    """target_moves needs an implied move to become a price. Without one
    and with no price to fall back on, the run is refused -- otherwise the
    target falls to zero and the row reads "nothing reaches it", which is
    true of a price nobody asked for."""
    from app.options.optimize import optimize_structures

    class _NoIv(_Service):
        async def chain(self, underlying, expiry):
            return _blind(await super().chain(underlying, expiry))

    with pytest.raises(OrderRejected) as caught:
        asyncio.run(
            optimize_structures(_NoIv(), "x", _req(horizon_expiry=NEAR), today=TODAY, now=NOW)
        )
    assert "implied moves cannot be sized" in str(caught.value)


def test_such_a_horizon_is_a_row_with_the_reason_not_the_end_of_the_comparison():
    class _NoIvNear(_Service):
        async def chain(self, underlying, expiry):
            chain = await super().chain(underlying, expiry)
            return _blind(chain) if expiry == NEAR else chain

    # With a price target alongside the moves: the sweep still refuses that
    # horizon rather than pricing a target that means something else here.
    out = _run(_NoIvNear(), _req(target_low=104.0))
    rows = {r["expiry"]: r for r in out["runs"]}
    assert "unavailable" in rows[NEAR.isoformat()]
    assert any("target" in r for r in out["runs"]), "the other horizons still answer"
    for row in out["runs"]:
        if "target" in row:
            assert row["target"]["low"] != 104.0, "every priced row is the horizon's own implied move"
