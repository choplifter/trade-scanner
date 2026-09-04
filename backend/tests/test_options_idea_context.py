"""What the model is shown for one underlying -- app.ai.options_context.

The expiry choice is pure and tested directly. gather_context runs against
hand-written async fakes (no Alpaca, no Anthropic, no FMP), which is the
point of the module taking its collaborators as arguments: the enrichment
blocks each have to survive their own source failing, and that is only
checkable if the sources can be made to fail on purpose."""

import asyncio
from datetime import date, datetime, timezone

import pytest

from app.ai.options_context import (
    IdeaContext,
    gather_context,
    pick_expiries,
)
from app.options.chain import Chain, ExpiryInfo, LegQuote, StrikeRow
from app.trading.errors import OrderRejected

TODAY = date(2026, 9, 4)
NEAR = date(2026, 9, 11)
MID = date(2026, 9, 25)
FAR = date(2026, 10, 16)
SPOT = 100.0


def _quote(kind, strike, expiry) -> LegQuote:
    return LegQuote(
        symbol=f"X{strike:g}{kind[0].upper()}",
        strike=strike,
        kind=kind,
        expiry=expiry,
        bid=1.00,
        ask=1.10,
        mid=1.05,
        last=1.05,
        bid_size=5,
        ask_size=5,
        delta=0.35,
        gamma=0.02,
        theta=-0.03,
        iv=0.30,
        open_interest=800,
        tradable=True,
    )


def _chain(expiry, strikes=(95, 100, 105)) -> Chain:
    return Chain(
        underlying="X",
        expiry=expiry,
        spot=SPOT,
        feed="opra",
        as_of=datetime.now(timezone.utc),
        rows=[
            StrikeRow(strike=float(s), call=_quote("call", s, expiry), put=_quote("put", s, expiry))
            for s in strikes
        ],
    )


class _Service:
    """The slice of OptionsService gather_context actually uses."""

    def __init__(self, expiries=(NEAR, MID, FAR), spot=SPOT, chains=None) -> None:
        self._expiries = list(expiries)
        self._spot = spot
        self._chains = chains or {e: _chain(e) for e in expiries}

    async def spot(self, underlying):
        return self._spot

    async def expiries(self, underlying):
        return {
            "underlying": underlying,
            "spot": self._spot,
            "expiries": [
                ExpiryInfo(expiry=e, dte=(e - TODAY).days, contract_count=40).to_dict() for e in self._expiries
            ],
        }

    async def chain(self, underlying, expiry):
        return self._chains[expiry]


class _Boom:
    """Every collaborator method raises -- the enrichment blocks must each
    swallow their own failure."""

    def __getattr__(self, name):
        async def fail(*args, **kwargs):
            raise RuntimeError(f"{name} exploded")

        return fail


def _gather(service=None, clients=None, **kwargs) -> IdeaContext:
    return asyncio.run(
        gather_context(service or _Service(), clients or _Boom(), "x", today=TODAY, **kwargs)
    )


# --- expiry choice --------------------------------------------------------


def _info(dte, count=10) -> ExpiryInfo:
    return ExpiryInfo(expiry=date.fromordinal(TODAY.toordinal() + dte), dte=dte, contract_count=count)


def test_pick_expiries_takes_the_listed_expiry_nearest_each_target():
    chosen = pick_expiries([_info(d) for d in (1, 8, 15, 22, 44, 120)])

    assert [(d - TODAY).days for d in chosen] == [8, 22, 44]


def test_pick_expiries_excludes_todays_contracts():
    # 0DTE carries no greeks from Alpaca, so there is nothing to reason with.
    chosen = pick_expiries([_info(0), _info(3)])

    assert [(d - TODAY).days for d in chosen] == [3]


def test_pick_expiries_collapses_duplicates_on_a_thin_board():
    # Only monthlies listed: all three targets land on the same two.
    chosen = pick_expiries([_info(30), _info(60)])

    assert len(chosen) == 2
    assert chosen == sorted(chosen)


def test_pick_expiries_still_answers_when_only_one_expiry_is_listed():
    assert pick_expiries([_info(9)]) == [_info(9).expiry]


def test_pick_expiries_skips_an_expiry_with_no_contracts():
    assert pick_expiries([_info(8, count=0), _info(9)]) == [_info(9).expiry]


def test_pick_expiries_of_nothing_is_nothing():
    assert pick_expiries([]) == []


def test_pick_expiries_never_returns_more_than_the_limit():
    chosen = pick_expiries([_info(d) for d in range(1, 90)])

    assert len(chosen) == 3


# --- the payload ----------------------------------------------------------


def test_the_payload_carries_a_condensed_chain_per_chosen_expiry():
    ctx = _gather()

    expiries = [block["expiry"] for block in ctx.payload["expiries"]]
    assert expiries == [NEAR.isoformat(), MID.isoformat(), FAR.isoformat()]
    assert ctx.payload["expiries"][0]["dte"] == 7
    assert ctx.payload["expiries"][0]["strikes"][0]["call"]["mid"] == 1.05


def test_the_strikes_kept_for_the_resolver_match_what_was_shown():
    ctx = _gather()

    # The model must never be offered a strike the resolver would refuse,
    # nor snapped onto one it was not shown.
    for block in ctx.payload["expiries"]:
        expiry = date.fromisoformat(block["expiry"])
        shown = {row["strike"] for row in block["strikes"] if row["call"] is not None}
        assert shown == set(ctx.strikes[expiry].call)


def test_the_chains_are_kept_for_pricing_but_stay_out_of_the_payload():
    ctx = _gather()

    assert set(ctx.chains) == {NEAR, MID, FAR}
    assert "chains" not in ctx.payload


def test_a_symbol_with_no_price_is_refused():
    with pytest.raises(OrderRejected):
        _gather(service=_Service(spot=0))


def test_a_symbol_with_no_expiries_beyond_today_is_refused():
    with pytest.raises(OrderRejected):
        _gather(service=_Service(expiries=()))


def test_a_chain_with_nothing_tradable_is_refused_rather_than_thinned():
    # Every quote one-sided: condense_chain keeps none of it, and there is
    # no structure to propose.
    dead = _chain(NEAR)
    dead.rows = [StrikeRow(strike=r.strike, call=None, put=None) for r in dead.rows]
    service = _Service(expiries=(NEAR,), chains={NEAR: dead})

    with pytest.raises(OrderRejected):
        _gather(service=service)


def test_every_enrichment_source_may_fail_without_taking_the_context_down():
    # _Boom raises for bars, news and everything else; the chain is what
    # matters and it is intact.
    ctx = _gather()

    assert ctx.payload["underlying"] == "X"
    assert ctx.payload["spot"] == 100.0
    assert ctx.payload["expiries"]
    assert ctx.payload["gex"] is None
    assert ctx.payload["news"] is None
    assert ctx.payload["earnings"] is None
    assert ctx.payload["chart_levels"] == []
    assert ctx.payload["underlying_context"] == {}


def test_a_missing_iv_rank_reports_its_sample_count_rather_than_going_quiet():
    ctx = _gather()
    iv = ctx.payload["implied_vol"]

    # "No rank yet, 0 sessions recorded" is a different statement from
    # "IV is not elevated", and the payload has to be able to make it.
    assert iv["iv_rank"] is None
    assert iv["iv_rank_samples"] == 0
    assert iv["atm_iv"] == 0.30
    assert [row["dte"] for row in iv["term_structure"]] == [7, 21, 42]


class _IvStore:
    def __init__(self, rank=None, samples=0) -> None:
        self._rank = rank
        self._samples = samples
        self.recorded: list[tuple] = []

    async def rank(self, symbol, current):
        return self._rank, self._samples

    async def record(self, symbol, session_date, atm_iv, dte):
        self.recorded.append((symbol, session_date, atm_iv, dte))


def test_todays_atm_iv_is_recorded_so_a_rank_can_exist_later():
    store = _IvStore(samples=4)
    ctx = _gather(iv_store=store)

    assert store.recorded == [("X", TODAY, 0.30, 7)]
    assert ctx.payload["implied_vol"]["iv_rank_samples"] == 4


def test_an_iv_store_that_fails_does_not_take_the_context_down():
    ctx = _gather(iv_store=_Boom())

    assert ctx.payload["implied_vol"]["iv_rank"] is None
    assert ctx.payload["expiries"]


class _Earnings:
    def __init__(self, report_date) -> None:
        self._date = report_date

    async def next_earnings(self, symbol):
        from app.market_data.earnings import EarningsDate

        return EarningsDate(symbol=symbol, report_date=self._date, days_until=(self._date - TODAY).days)


def test_earnings_says_which_expiries_would_be_held_through_the_report():
    ctx = _gather(earnings_calendar=_Earnings(date(2026, 9, 30)))
    earnings = ctx.payload["earnings"]

    assert earnings["report_date"] == "2026-09-30"
    # The decision-relevant part: the near expiry is clear of it, the far
    # two are not.
    assert earnings["held_through_by_expiry"] == {
        NEAR.isoformat(): False,
        MID.isoformat(): False,
        FAR.isoformat(): True,
    }


def test_no_upcoming_earnings_is_reported_as_absent():
    class _None:
        async def next_earnings(self, symbol):
            return None

    assert _gather(earnings_calendar=_None()).payload["earnings"] is None


class _GexCache:
    def __init__(self, reading) -> None:
        self._reading = reading

    async def reading(self, symbol):
        return self._reading


def test_gex_context_carries_the_walls_and_the_sample_behind_them():
    from app.market_data.gamma_exposure import GexReading, StrikeGex

    reading = GexReading(
        symbol="X",
        spot_price=SPOT,
        net_gex=-2_000.0,
        call_gex=1_000.0,
        put_gex=-3_000.0,
        contracts_used=9,
        as_of=datetime.now(timezone.utc),
        by_strike=[
            StrikeGex(strike=95.0, net_gex=-3_000.0, call_gex=0.0, put_gex=-3_000.0),
            StrikeGex(strike=105.0, net_gex=1_000.0, call_gex=1_000.0, put_gex=0.0),
        ],
        open_interest_used=1_234,
    )
    gex = _gather(gex_cache=_GexCache(reading)).payload["gex"]

    assert gex["regime"] == "negative"
    assert gex["call_wall"]["strike"] == 105.0
    assert gex["put_wall"]["strike"] == 95.0
    # Whether nine strikes is enough to believe a wall is the reader's call,
    # so the sample travels with the number.
    assert (gex["contracts_used"], gex["open_interest_used"]) == (9, 1_234)


def test_gex_is_absent_rather_than_zero_when_there_is_no_reading():
    assert _gather(gex_cache=_GexCache(None)).payload["gex"] is None
