"""Gather, ask, repair, price -- app.ai.options_suggest, plus the endpoint
that fronts it.

Fakes all the way down: a stub Anthropic client that returns a prepared
OptionsIdeaResponse, a stub OptionsService that answers chain/spot/expiries
and prices a ticket. Nothing here touches the network, and the assertions
are about the parts that decide whether the feature can be trusted -- that
the priced numbers come from the service and not the model, and that a
proposal which cannot be built or priced is reported rather than dropped.
"""

import asyncio
from datetime import date, datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ai.options_idea import OptionsIdea, OptionsIdeaResponse
from app.ai.options_resolve import ProposedLeg
from app.ai.options_suggest import suggest_options_ideas
from app.options.chain import Chain, ExpiryInfo, LegQuote, StrikeRow
from app.options.models import ResolvedSpread, SpreadLeg
from app.trading.errors import OrderRejected

TODAY = date.today()
NEAR = date.fromordinal(TODAY.toordinal() + 7)
MID = date.fromordinal(TODAY.toordinal() + 21)
FAR = date.fromordinal(TODAY.toordinal() + 45)
SPOT = 100.0


def _quote(kind, strike, expiry) -> LegQuote:
    return LegQuote(
        symbol=f"X{strike:g}{kind[0].upper()}",
        strike=strike, kind=kind, expiry=expiry,
        bid=1.00, ask=1.10, mid=1.05, last=1.05, bid_size=5, ask_size=5,
        delta=0.35, gamma=0.02, theta=-0.03, iv=0.30, open_interest=800, tradable=True,
    )


def _chain(expiry, strikes=(90, 95, 100, 105, 110)) -> Chain:
    return Chain(
        underlying="X", expiry=expiry, spot=SPOT, feed="opra", as_of=datetime.now(timezone.utc),
        rows=[
            StrikeRow(strike=float(s), call=_quote("call", s, expiry), put=_quote("put", s, expiry))
            for s in strikes
        ],
    )


def _resolved(ticket) -> ResolvedSpread:
    """A plausible priced spread. The numbers are the service's -- the point
    of several tests below is that these, not the model's, reach the user."""
    return ResolvedSpread(
        underlying=ticket.underlying, strategy=ticket.strategy, expiry=ticket.expiry,
        qty=ticket.qty, direction=ticket.direction,
        legs=[
            SpreadLeg(
                symbol=f"X{leg.strike:g}{leg.kind[0].upper()}", kind=leg.kind, strike=leg.strike,
                expiry=leg.expiry or ticket.expiry, side=leg.side,
                position_intent="buy_to_open" if leg.side == "buy" else "sell_to_open",
            )
            for leg in ticket.leg_specs_full()
        ],
        spot=SPOT, width=5.0, net_mid=1.23, net_natural=1.30, limit_price=1.30,
        alpaca_limit_price=-1.30, max_profit=130.0, max_loss=370.0, breakevens=[96.3],
        collateral=370.0, options_buying_power=50_000.0, dte=7, options_level=3, account="paper",
    )


class _Service:
    def __init__(self, *, preview_error=None, expiries=(NEAR, MID, FAR)) -> None:
        self._expiries = list(expiries)
        self.preview_error = preview_error
        self.previewed: list = []

    async def spot(self, underlying):
        return SPOT

    async def expiries(self, underlying):
        return {
            "underlying": underlying, "spot": SPOT,
            "expiries": [
                ExpiryInfo(expiry=e, dte=(e - TODAY).days, contract_count=40).to_dict()
                for e in self._expiries
            ],
        }

    async def chain(self, underlying, expiry):
        return _chain(expiry)

    async def preview(self, ticket):
        self.previewed.append(ticket)
        if self.preview_error is not None:
            raise self.preview_error
        return _resolved(ticket)


class _Anthropic:
    """Stands in for AsyncAnthropic: .messages.parse(...) -> .parsed_output."""

    def __init__(self, response: OptionsIdeaResponse) -> None:
        self.response = response
        self.calls: list[dict] = []
        self.messages = self

    async def parse(self, **kwargs):
        self.calls.append(kwargs)

        class _Parsed:
            parsed_output = self.response

        return _Parsed()


class _NoClients:
    """Every bar/news fetch raises; the enrichment blocks swallow it and the
    chain still carries the suggestion."""

    def __getattr__(self, name):
        async def fail(*a, **k):
            raise RuntimeError("no market data in this test")

        return fail


def _idea(strategy="bull_put", expiry=None, legs=None, **kwargs) -> OptionsIdea:
    return OptionsIdea(
        strategy=strategy,
        expiry=expiry or NEAR,
        legs=legs or [
            ProposedLeg(kind="put", strike=90.0, side="buy"),
            ProposedLeg(kind="put", strike=95.0, side="sell"),
        ],
        headline=kwargs.get("headline", "Credit put spread under support"),
        reason=kwargs.get("reason", "IV sits above realised and the put wall is at 95."),
        risk_note=kwargs.get("risk_note", "A break below 90 costs the full width."),
        conviction=kwargs.get("conviction", 7),
    )


def _run(response, service=None, **kwargs) -> dict:
    service = service or _Service()
    return asyncio.run(
        suggest_options_ideas(_Anthropic(response), service, _NoClients(), "x", **kwargs)
    ), service


def _response(*ideas, disclaimer="Automated reading of chain data, not advice.") -> OptionsIdeaResponse:
    return OptionsIdeaResponse(ideas=list(ideas), disclaimer=disclaimer)


# --- the happy path -------------------------------------------------------


def test_a_proposal_comes_back_priced_by_the_service_not_the_model():
    result, service = _run(_response(_idea()))

    (idea,) = result["ideas"]
    assert result["rejected"] == []
    # Every number a reader sees is the options stack's.
    assert idea["spread"]["net_mid"] == 1.23
    assert idea["spread"]["max_loss"] == 370.0
    assert idea["spread"]["breakevens"] == [96.3]
    assert len(service.previewed) == 1


def test_the_response_carries_a_ticket_ready_for_the_frontend_to_load():
    result, _ = _run(_response(_idea()))
    ticket = result["ideas"][0]["ticket"]

    assert ticket["strategy"] == "bull_put"
    assert ticket["underlying"] == "X"
    # A bull put uses strike fields, and the long leg sits below the short.
    assert ticket["long_strike"] < ticket["short_strike"]
    assert "legs" not in ticket


def test_the_models_prose_is_carried_through_unchanged():
    result, _ = _run(_response(_idea(headline="Sell the 95 put spread", conviction=9)))
    idea = result["ideas"][0]

    assert idea["headline"] == "Sell the 95 put spread"
    assert idea["conviction"] == 9
    assert idea["risk_note"]
    assert result["disclaimer"] == "Automated reading of chain data, not advice."


def test_a_strike_the_model_invented_is_snapped_before_pricing():
    # 93.4 and 97.1 are not listed; 95 and 100 are.
    result, service = _run(
        _response(
            _idea(
                legs=[
                    ProposedLeg(kind="put", strike=93.4, side="buy"),
                    ProposedLeg(kind="put", strike=97.1, side="sell"),
                ]
            )
        )
    )

    assert result["rejected"] == []
    ticket = service.previewed[0]
    assert (ticket.long_strike, ticket.short_strike) == (95.0, 100.0)


def test_the_model_sees_the_chain_and_the_symbol():
    response = _response(_idea())
    client = _Anthropic(response)
    asyncio.run(suggest_options_ideas(client, _Service(), _NoClients(), "x"))

    (call,) = client.calls
    assert call["model"] == "claude-opus-5"
    assert "X" in call["messages"][0]["content"]
    assert '"strike": 95.0' in call["messages"][0]["content"]


# --- rejections are reported, not swallowed -------------------------------


def test_a_structure_the_account_cannot_trade_is_reported_with_its_reason():
    result, _ = _run(
        _response(_idea()),
        service=_Service(preview_error=OrderRejected("Options level 3 required", field="strategy")),
    )

    assert result["ideas"] == []
    (rejected,) = result["rejected"]
    # The whole point: a shorter list would look like "nothing appeals",
    # which is a different and wrong statement.
    assert "level 3" in rejected["rejected_because"].lower()
    assert rejected["headline"] == "Credit put spread under support"


def test_a_proposal_that_cannot_be_put_on_real_strikes_is_reported():
    # Only one strike is listed for this expiry, so a two-leg spread has
    # nowhere to go.
    class _OneStrike(_Service):
        async def chain(self, underlying, expiry):
            return _chain(expiry, strikes=(100,))

    result, _ = _run(_response(_idea()), service=_OneStrike())

    assert result["ideas"] == []
    assert result["rejected"][0]["rejected_because"]


def test_an_expiry_that_was_never_offered_is_reported():
    result, _ = _run(_response(_idea(expiry=date(2030, 1, 18))))

    assert result["ideas"] == []
    assert "not one of the expiries offered" in result["rejected"][0]["rejected_because"]


def test_one_bad_proposal_does_not_take_the_good_ones_down():
    result, _ = _run(_response(_idea(), _idea(expiry=date(2030, 1, 18))))

    assert len(result["ideas"]) == 1
    assert len(result["rejected"]) == 1


def test_a_pricing_failure_that_is_not_a_trading_error_is_still_reported():
    result, _ = _run(_response(_idea()), service=_Service(preview_error=RuntimeError("boom")))

    assert result["ideas"] == []
    assert result["rejected"][0]["rejected_because"] == "could not be priced right now"


def test_more_ideas_than_asked_for_are_truncated():
    result, service = _run(_response(_idea(), _idea(), _idea(), _idea()), max_ideas=2)

    assert len(service.previewed) == 2
    assert len(result["ideas"]) == 2


def test_no_ideas_is_a_valid_answer():
    result, service = _run(_response())

    assert result["ideas"] == []
    assert result["rejected"] == []
    assert service.previewed == []


# --- what the reader is told about the inputs -----------------------------


def test_the_response_says_what_the_model_could_actually_see():
    result, _ = _run(_response(_idea()))
    used = result["context_used"]

    # Without this a suggestion made with GEX, news and an IV rank looks
    # identical to one made with none of them.
    assert used["has_gex"] is False
    assert used["has_news"] is False
    assert used["has_earnings"] is False
    assert used["iv_rank_samples"] == 0
    assert len(used["expiries"]) == 3


def test_a_symbol_with_no_price_refuses_rather_than_answering_emptily():
    class _NoPrice(_Service):
        async def spot(self, underlying):
            return None

    with pytest.raises(OrderRejected):
        _run(_response(_idea()), service=_NoPrice())


# --- the endpoint ---------------------------------------------------------


@pytest.fixture
def api(monkeypatch):
    from app.auth.dependency import get_current_user
    from app.routers import trading_options

    service = _Service()
    app = FastAPI()
    app.include_router(trading_options.router, prefix="/api/trading/options")
    app.dependency_overrides[trading_options._service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: {"id": 1, "username": "t"}
    app.state.anthropic_client = _Anthropic(_response(_idea()))
    app.state.alpaca_clients = _NoClients()
    app.state.scanner_engine = None
    return app, service


def test_the_endpoint_answers_with_priced_ideas(api):
    app, _ = api
    body = TestClient(app).post("/api/trading/options/idea", json={"underlying": "x"}).json()

    assert body["underlying"] == "X"
    assert body["ideas"][0]["spread"]["net_mid"] == 1.23
    assert body["disclaimer"]


def test_the_endpoint_needs_an_anthropic_key(api):
    app, _ = api
    app.state.anthropic_client = None

    resp = TestClient(app).post("/api/trading/options/idea", json={"underlying": "x"})

    assert resp.status_code == 503
    assert "ANTHROPIC_API_KEY" in resp.json()["detail"]


def test_the_endpoint_turns_a_trading_error_into_a_422(api):
    app, service = api

    class _NoPrice(_Service):
        async def spot(self, underlying):
            return None

    from app.routers import trading_options

    app.dependency_overrides[trading_options._service] = lambda: _NoPrice()
    resp = TestClient(app).post("/api/trading/options/idea", json={"underlying": "x"})

    assert resp.status_code == 422


def test_the_endpoint_rejects_a_missing_underlying(api):
    app, _ = api

    assert TestClient(app).post("/api/trading/options/idea", json={}).status_code == 422
