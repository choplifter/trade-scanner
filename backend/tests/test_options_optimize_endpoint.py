"""The optimizer end to end -- app.options.optimize plus the two endpoints
that front it. Fakes as in test_options_idea_suggest: a stub service that
answers spot/expiries/chain with a Black-Scholes-consistent chain and
prices a ticket with the real spread_risk arithmetic, counting its account
round trips. The assertions are about the parts that decide whether the
feature can be trusted: only finalists are previewed, the account is
fetched once, every drop is counted, and the sim endpoint says what a
replayed chain is instead of refusing.
"""

import asyncio
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.options.chain import Chain, ExpiryInfo, LegQuote, StrikeRow
from app.options.models import ResolvedSpread, SpreadLeg, resolve_legs
from app.options.optimize import OptimizeRequest, choose_expiries, optimize_structures
from app.options.payoff import bs_greeks, bs_price
from app.options.pricing import net_price, spread_risk
from app.trading.errors import OrderRejected

ET = ZoneInfo("America/New_York")
TODAY = date(2026, 9, 4)
NOW = datetime(2026, 9, 4, 14, 0, tzinfo=timezone.utc)
NEAR = date(2026, 9, 25)
MID = date(2026, 10, 9)
FAR = date(2026, 10, 30)
SPOT = 100.0
IV = 0.30


def _years(expiry: date) -> float:
    return (datetime.combine(expiry, time(16, 0), tzinfo=ET) - NOW).total_seconds() / (365 * 24 * 3600)


def _quote(kind, strike, expiry) -> LegQuote:
    mid = round(max(bs_price(kind, SPOT, strike, _years(expiry), IV), 0.02), 2)
    delta, gamma, theta = bs_greeks(kind, SPOT, strike, _years(expiry), IV)
    return LegQuote(
        symbol=f"X{expiry:%y%m%d}{kind[0].upper()}{int(strike * 1000):08d}",
        strike=strike, kind=kind, expiry=expiry,
        bid=round(mid - 0.01, 2), ask=round(mid + 0.01, 2), mid=mid, last=mid, bid_size=5, ask_size=5,
        delta=round(delta, 3), gamma=round(gamma, 4), theta=round(theta, 3), iv=IV, open_interest=800, tradable=True,
    )


def _chain(expiry, strikes=range(90, 111)) -> Chain:
    return Chain(
        underlying="X", expiry=expiry, spot=SPOT, feed="opra", as_of=NOW,
        rows=[StrikeRow(strike=float(s), call=_quote("call", s, expiry), put=_quote("put", s, expiry)) for s in strikes],
    )


class _Service:
    """spot / expiries / chain / account / preview, the way the optimizer
    uses them. preview prices the ticket with the real spread_risk on the
    chain's mids and records every call and every account fetch."""

    def __init__(self, *, expiries=(NEAR, MID, FAR), spot=SPOT, preview_error=None) -> None:
        self._expiries = list(expiries)
        self._spot = spot
        self.preview_error = preview_error
        self.previewed: list = []
        self.account_calls = 0
        self.chain_calls: list = []

    async def spot(self, underlying):
        return self._spot

    async def expiries(self, underlying):
        return {
            "underlying": underlying, "spot": self._spot,
            "expiries": [ExpiryInfo(expiry=e, dte=(e - TODAY).days, contract_count=40).to_dict() for e in self._expiries],
        }

    async def chain(self, underlying, expiry):
        self.chain_calls.append(expiry)
        return _chain(expiry)

    async def account(self):
        self.account_calls += 1
        return {"options_buying_power": 50_000.0, "options_trading_level": 3}

    async def preview(self, ticket, *, account=None):
        self.previewed.append(ticket)
        if account is None:
            self.account_calls += 1
        if self.preview_error is not None:
            raise self.preview_error
        chains = {e: _chain(e) for e in ticket.expiries}
        legs = resolve_legs(ticket, chains)
        for leg in legs:
            q = _quote(leg.kind, leg.strike, leg.expiry)
            leg.bid, leg.ask, leg.mid, leg.iv, leg.delta = q.bid, q.ask, q.mid, q.iv, q.delta
        signed = net_price(legs, "mid")
        price = round(abs(signed), 2)
        risk = spread_risk(ticket.strategy, ticket.strikes, price, ticket.qty, stock_price=SPOT)
        return ResolvedSpread(
            underlying=ticket.underlying, strategy=ticket.strategy, expiry=ticket.expiry, qty=ticket.qty,
            direction=risk.direction, legs=legs, spot=SPOT, width=risk.width, net_mid=price, net_natural=price + 0.02,
            limit_price=price, alpaca_limit_price=price if risk.direction == "debit" else -price,
            max_profit=risk.max_profit, max_loss=risk.max_loss, breakevens=risk.breakevens, collateral=risk.collateral,
            options_buying_power=50_000.0, dte=(ticket.expiry - TODAY).days, options_level=3, account="paper",
        )


def _req(**over) -> OptimizeRequest:
    base = {"underlying": "x", "target_low": 104.0, "horizon_expiry": NEAR}
    base.update(over)
    return OptimizeRequest(**base)


def _run(service, req):
    return asyncio.run(optimize_structures(service, "X", req, today=TODAY, now=NOW))


# --- the request model -------------------------------------------------------------


def test_the_request_needs_exactly_one_horizon_and_an_ordered_range():
    with pytest.raises(ValueError):
        OptimizeRequest(underlying="x", target_low=100)
    with pytest.raises(ValueError):
        OptimizeRequest(underlying="x", target_low=100, horizon_expiry=NEAR, horizon_date=NEAR)
    with pytest.raises(ValueError):
        OptimizeRequest(underlying="x", target_low=105, target_high=100, horizon_expiry=NEAR)
    req = _req(target_high=106.0)
    assert req.target.low == 104.0 and req.target.high == 106.0 and req.horizon == NEAR


# --- expiry choice -------------------------------------------------------------------


def test_choose_expiries_keeps_the_horizon_first_and_nothing_before_it():
    infos = [ExpiryInfo(expiry=e, dte=(e - TODAY).days, contract_count=40) for e in (date(2026, 9, 11), NEAR, MID, FAR)]
    chosen = choose_expiries(infos, NEAR, TODAY)
    assert chosen[0] == NEAR
    assert all(e >= NEAR for e in chosen)
    assert len(chosen) <= 3
    # A horizon date between expiries drops the ones before it.
    assert choose_expiries(infos, date(2026, 10, 1), TODAY) == [MID, FAR]
    assert choose_expiries(infos, date(2026, 12, 1), TODAY) == []


# --- the run -------------------------------------------------------------------------


def test_results_are_ranked_priced_by_the_service_and_previewed_once_each():
    service = _Service()
    body = _run(service, _req(target_high=106.0, budget=400.0))

    assert body["underlying"] == "X" and body["spot"] == SPOT
    assert body["horizon"]["date"] == NEAR.isoformat()
    assert body["horizon"]["expiries_considered"][0] == NEAR.isoformat()
    assert body["results"], body["skipped"]
    rors = [r["return_on_risk"] for r in body["results"]]
    assert rors == sorted(rors, reverse=True)
    assert [r["rank"] for r in body["results"]] == list(range(1, len(body["results"]) + 1))
    # Only finalists went through preview, and the account was fetched once.
    assert len(service.previewed) <= 12
    assert service.account_calls == 1
    # Every card's numbers are the preview's, and the ticket loads.
    for r in body["results"]:
        assert r["spread"]["strategy"] == r["strategy"]
        assert r["ticket"]["underlying"] == "X" and r["ticket"]["strategy"] == r["strategy"]
        assert r["risk"] <= 400.0
        assert r["pnl_min"] > 0
        assert r["legs_label"]
    # The bookkeeping: every enumerated shape is either scored or explained.
    skipped = body["skipped"]
    pricing_drops = sum(n for k, n in skipped["reasons"].items() if k in ("no_market", "no_iv", "wrong_way_market", "risk_shape", "candidate_cap"))
    assert skipped["total"] == skipped["scored"] + pricing_drops
    assert "over_budget" in skipped["reasons"]
    assert body["disclaimer"] and body["warnings"] == []


def test_a_horizon_today_or_unlisted_or_beyond_the_board_is_refused():
    service = _Service()
    with pytest.raises(OrderRejected):
        _run(service, _req(horizon_expiry=None, horizon_date=TODAY))
    with pytest.raises(OrderRejected):
        _run(service, _req(horizon_expiry=date(2026, 9, 18)))
    with pytest.raises(OrderRejected):
        _run(service, _req(horizon_expiry=None, horizon_date=date(2027, 1, 15)))


def test_no_price_refuses_rather_than_answering_emptily():
    with pytest.raises(OrderRejected):
        _run(_Service(spot=None), _req())


def test_a_finalist_the_service_refuses_is_reported_not_dropped():
    service = _Service(preview_error=OrderRejected("options level 3 required", field="strategy"))
    body = _run(service, _req())
    assert body["results"] == []
    assert body["rejected"]
    assert all(r["rejected_because"] == "options level 3 required" for r in body["rejected"])


def test_strategy_selection_is_honoured():
    body = _run(_Service(), _req(strategies=["bull_call"], budget=1000.0))
    assert {r["strategy"] for r in body["results"]} == {"bull_call"}


# --- the endpoints ---------------------------------------------------------------------


@pytest.fixture
def api():
    from app.auth.dependency import get_current_user
    from app.routers import trading_options

    service = _Service()
    app = FastAPI()
    app.include_router(trading_options.router, prefix="/api/trading/options")
    app.dependency_overrides[trading_options._service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: {"id": 1, "username": "t"}
    return app, service


def test_the_endpoint_answers_with_ranked_structures(api):
    app, service = api
    resp = TestClient(app).post(
        "/api/trading/options/optimize",
        json={"underlying": "x", "target_low": 104, "target_high": 106, "horizon_expiry": NEAR.isoformat(), "budget": 400},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["results"] and body["results"][0]["rank"] == 1
    assert len(service.previewed) <= 12


def test_the_endpoint_turns_bad_input_and_trading_errors_into_422(api):
    app, _ = api
    client = TestClient(app)
    assert client.post("/api/trading/options/optimize", json={"underlying": "x", "target_low": 104}).status_code == 422
    assert client.post(
        "/api/trading/options/optimize",
        json={"underlying": "x", "target_low": 106, "target_high": 104, "horizon_expiry": NEAR.isoformat()},
    ).status_code == 422
    assert client.post(
        "/api/trading/options/optimize",
        json={"underlying": "x", "target_low": 104, "horizon_expiry": NEAR.isoformat(), "strategies": ["moon_shot"]},
    ).status_code == 422
    # A horizon the board does not reach is a TradingError -> 422 with a detail.
    resp = client.post(
        "/api/trading/options/optimize",
        json={"underlying": "x", "target_low": 104, "horizon_date": "2027-01-15"},
    )
    assert resp.status_code == 422 and "No listed expiry" in resp.json()["detail"]["message"]


@pytest.fixture
def sim_api(monkeypatch):
    from app.auth.dependency import get_current_user
    from app.routers import trading_sim_options

    service = _Service()
    seam = {"value": None}

    async def _fake_service(request, user):
        return service

    async def _fake_seam(request, user_id):
        return seam["value"]

    monkeypatch.setattr(trading_sim_options, "_service", _fake_service)
    monkeypatch.setattr(trading_sim_options, "_replay_seam", _fake_seam)
    app = FastAPI()
    app.include_router(trading_sim_options.router)
    app.dependency_overrides[get_current_user] = lambda: {"id": 1, "username": "t"}
    return app, service, seam


def test_the_sim_endpoint_runs_during_a_replay_and_says_what_the_chain_is(sim_api):
    app, _service_, seam = sim_api
    seam["value"] = (object(), NOW)
    resp = TestClient(app).post(
        "/api/trading/sim/options/optimize",
        json={"underlying": "x", "target_low": 104, "horizon_expiry": NEAR.isoformat()},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["results"]
    assert any("Replay chain" in w for w in body["warnings"])

    seam["value"] = None
    resp = TestClient(app).post(
        "/api/trading/sim/options/optimize",
        json={"underlying": "x", "target_low": 104, "horizon_expiry": NEAR.isoformat()},
    )
    assert resp.status_code == 200 and resp.json()["warnings"] == []
