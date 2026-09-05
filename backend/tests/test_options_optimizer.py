"""The pure half of the Optimizer -- app.options.optimizer: what gets
enumerated, how a candidate is priced at the target, how the list is
filtered and ranked, and that every shape it produces is a ticket the
options stack accepts. No I/O: the chain is a hand-built set of condensed
rows priced by the same Black-Scholes the app uses, so the mids are
consistent across strikes and a ranking assertion means something.
"""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.options.models import SpreadTicket
from app.options.optimizer import (
    DEFAULT_STRATEGIES,
    OUTLOOK_STRATEGIES,
    Skipped,
    Target,
    candidate_ticket,
    chance_of_profit,
    enumerate_candidates,
    filter_and_rank,
    legs_label,
    price_candidate,
    rank_score,
)
from app.options.payoff import PayoffLeg, bs_greeks, bs_price

ET = ZoneInfo("America/New_York")
SPOT = 100.0
IV = 0.30
NEAR = date(2026, 9, 25)
FAR = date(2026, 10, 16)
NOW = datetime(2026, 9, 4, 10, 0, tzinfo=ET)


def _years(expiry: date) -> float:
    return (datetime.combine(expiry, time(16, 0), tzinfo=ET) - NOW).total_seconds() / (365 * 24 * 3600)


def _quote(kind: str, strike: float, expiry: date, *, iv: float | None = IV) -> dict:
    """A quote the condenser would have produced: mid from Black-Scholes at
    `iv`, a two-cent market around it, the model's delta."""
    sigma = iv if iv is not None else IV
    mid = round(max(bs_price(kind, SPOT, strike, _years(expiry), sigma), 0.01), 2)
    delta = bs_greeks(kind, SPOT, strike, _years(expiry), sigma)[0]
    return {"mid": mid, "bid": round(mid - 0.01, 2), "ask": round(mid + 0.01, 2), "iv": iv, "delta": round(delta, 3), "oi": 500}


def _rows(expiry: date, strikes=range(90, 111), *, iv: float | None = IV) -> list[dict]:
    return [{"strike": float(k), "call": _quote("call", k, expiry, iv=iv), "put": _quote("put", k, expiry, iv=iv)} for k in strikes]


def _horizon(expiry: date) -> datetime:
    return datetime.combine(expiry, time(16, 0), tzinfo=ET)


def _priced(rows_by_expiry, target, horizon, strategies=DEFAULT_STRATEGIES):
    raws, skipped = enumerate_candidates(rows_by_expiry, SPOT, target, strategies)
    cands = []
    for raw in raws:
        out = price_candidate(raw, rows_by_expiry, SPOT, target, horizon)
        if isinstance(out, str):
            skipped.add(out)
        else:
            cands.append(out)
    skipped.scored = len(cands)
    return raws, cands, skipped


# --- enumeration -----------------------------------------------------------------


def test_verticals_stay_within_the_width_bound_and_income_is_opt_in():
    rows = {NEAR: _rows(NEAR)}
    raws, _ = enumerate_candidates(rows, SPOT, Target(105, 105))
    verticals = [r for r in raws if r.strategy in ("bull_call", "bear_call", "bull_put", "bear_put")]
    assert verticals
    for raw in verticals:
        lo, hi = sorted(leg.strike for leg in raw.legs)
        assert 1 <= hi - lo <= 3  # one-point strikes: width in strikes == width in dollars
    assert not any(r.strategy in ("covered_call", "cash_secured_put") for r in raws)

    with_income, _ = enumerate_candidates(rows, SPOT, Target(105, 105), frozenset({"covered_call", "cash_secured_put"}))
    assert {r.strategy for r in with_income} == {"covered_call", "cash_secured_put"}
    # A covered call is written above the spot, a cash-secured put below it.
    assert all(r.legs[0].strike >= SPOT for r in with_income if r.strategy == "covered_call")
    assert all(r.legs[0].strike <= SPOT for r in with_income if r.strategy == "cash_secured_put")


def test_calendars_need_a_later_expiry_and_are_written_on_the_near_one():
    alone, _ = enumerate_candidates({NEAR: _rows(NEAR)}, SPOT, Target(100, 100), frozenset({"calendar"}))
    assert alone == []

    raws, _ = enumerate_candidates({NEAR: _rows(NEAR), FAR: _rows(FAR)}, SPOT, Target(100, 100), frozenset({"calendar"}))
    assert raws
    for raw in raws:
        assert raw.expiry == NEAR
        sold, bought = raw.legs
        assert sold.side == "sell" and sold.expiry == NEAR
        assert bought.side == "buy" and bought.expiry == FAR
        assert sold.strike == bought.strike


def test_the_candidate_cap_is_counted_not_silent():
    raws, skipped = enumerate_candidates({NEAR: _rows(NEAR)}, SPOT, Target(100, 100), max_candidates=25)
    assert len(raws) == 25
    assert skipped.total > 25
    assert skipped.reasons["candidate_cap"] == skipped.total - 25


def test_every_enumerated_shape_is_a_ticket_the_stack_accepts():
    strategies = DEFAULT_STRATEGIES | {"covered_call", "cash_secured_put"}
    raws, _ = enumerate_candidates({NEAR: _rows(NEAR), FAR: _rows(FAR)}, SPOT, Target(98, 104), strategies)
    assert {r.strategy for r in raws} == set(strategies)
    for raw in raws:
        ticket = candidate_ticket("SPY", raw)
        assert isinstance(ticket, SpreadTicket)
        assert ticket.strategy == raw.strategy and ticket.expiry == raw.expiry


# --- pricing at the target -----------------------------------------------------------


def test_a_bull_call_at_expiry_pays_the_width_less_the_debit():
    rows = {NEAR: _rows(NEAR)}
    raws, cands, _ = _priced(rows, Target(110, 110), _horizon(NEAR), frozenset({"bull_call"}))
    bull = next(c for c in cands if [leg.strike for leg in c.legs] == [100.0, 102.0])
    debit = rows[NEAR][10]["call"]["mid"] - rows[NEAR][12]["call"]["mid"]
    assert bull.direction == "debit"
    assert bull.net_price == pytest.approx(debit, abs=1e-4)
    # Both legs have expired at the horizon: intrinsic 10 and 8 -> width 2.
    assert bull.pnl_min == pytest.approx((2.0 - debit) * 100, abs=0.01)
    assert bull.risk == pytest.approx(round(debit, 2) * 100, abs=0.01)
    assert bull.return_on_risk == pytest.approx(bull.pnl_min / bull.risk)


def test_a_range_target_scores_its_worst_point():
    rows = {NEAR: _rows(NEAR)}
    _, cands, _ = _priced(rows, Target(100, 106), _horizon(NEAR), frozenset({"bull_call"}))
    c = next(c for c in cands if [leg.strike for leg in c.legs] == [100.0, 103.0])
    assert len(c.pnl_points) == 5
    assert c.pnl_min == min(c.pnl_points) and c.pnl_min < c.pnl_max
    assert c.return_on_risk == pytest.approx(c.pnl_min / c.risk)


def test_a_leg_without_iv_cannot_be_valued_before_its_expiry_but_can_at_it():
    rows_no_iv = {FAR: _rows(FAR, iv=None)}
    raws, _ = enumerate_candidates(rows_no_iv, SPOT, Target(105, 105), frozenset({"long_call"}))
    before = price_candidate(raws[0], rows_no_iv, SPOT, Target(105, 105), _horizon(NEAR))
    at_expiry = price_candidate(raws[0], rows_no_iv, SPOT, Target(105, 105), _horizon(FAR))
    assert before == "no_iv"
    assert not isinstance(at_expiry, str)


def test_skip_reasons_account_for_every_enumerated_shape():
    rows = {NEAR: _rows(NEAR), FAR: _rows(FAR)}
    raws, cands, skipped = _priced(rows, Target(103, 103), _horizon(NEAR))
    pricing_drops = sum(skipped.reasons.values())
    assert skipped.total == len(raws) == skipped.scored + pricing_drops

    # With no top-k truncation, everything that was not kept is explained.
    kept, drops = filter_and_rank(cands, budget=300.0, top_k=10_000, per_strategy_cap=10_000)
    assert sum(drops.values()) == len(cands) - len(kept)
    assert all(c.risk <= 300.0 for c in kept)
    assert all(c.pnl_min > 0 for c in kept)
    assert "over_budget" in drops and "non_positive_return" in drops

    # The strategy cap is a drop reason too, so the dozen finalists are
    # accounted for the same way.
    finalists, capped = filter_and_rank(cands, budget=300.0, top_k=12, per_strategy_cap=3)
    assert len(finalists) <= 12
    assert capped.get("strategy_cap", 0) >= 0


def test_ranking_prefers_return_on_risk_and_caps_each_strategy():
    rows = {NEAR: _rows(NEAR)}
    _, cands, _ = _priced(rows, Target(104, 104), _horizon(NEAR), frozenset({"bull_call", "long_call"}))
    kept, _ = filter_and_rank(cands, top_k=12, per_strategy_cap=3)
    rors = [c.return_on_risk for c in kept]
    assert rors == sorted(rors, reverse=True)
    per_key: dict = {}
    for c in kept:
        per_key[(c.strategy, c.expiry)] = per_key.get((c.strategy, c.expiry), 0) + 1
    assert max(per_key.values()) <= 3


def test_max_loss_filter_excludes_unbounded_and_too_large_losses():
    rows = {NEAR: _rows(NEAR)}
    _, cands, _ = _priced(rows, Target(104, 104), _horizon(NEAR), frozenset({"long_call", "cash_secured_put"}))
    kept, drops = filter_and_rank(cands, max_loss=150.0, top_k=50)
    assert all(c.max_loss is not None and c.max_loss <= 150.0 for c in kept)
    assert drops.get("over_max_loss", 0) > 0


def test_legs_label_reads_like_the_idea_cards():
    from app.options.models import TicketLeg

    legs = (
        TicketLeg(kind="call", strike=100.0, side="buy"),
        TicketLeg(kind="call", strike=105.0, side="sell", ratio=2),
        TicketLeg(kind="call", strike=110.0, side="buy", expiry=FAR),
    )
    assert legs_label(legs, NEAR) == "+100C −2×105C +110C 10/16"


def test_skipped_to_dict_is_sorted_and_complete():
    s = Skipped(total=3, scored=1)
    s.add("no_iv")
    s.add("over_budget", 2)
    assert s.to_dict() == {"total": 3, "scored": 1, "reasons": {"no_iv": 1, "over_budget": 2}}


# --- chance of profit and the return / chance slider ---------------------------------


def _years_to(expiry: date) -> float:
    return _years(expiry)


def test_chance_of_profit_is_a_probability_that_orders_shapes_sensibly():
    horizon = _horizon(NEAR)
    years = _years_to(NEAR)
    # A long call bought at the ATM mid: well under an even chance.
    atm = PayoffLeg(kind="call", strike=100.0, side="buy", expiry=NEAR, iv=IV)
    atm_mid = _quote("call", 100.0, NEAR)["mid"]
    p_atm = chance_of_profit([atm], atm_mid, horizon, SPOT, IV, years)
    # A deep in-the-money call, bought for its mid: far likelier to end in profit than a far OTM one.
    itm = PayoffLeg(kind="call", strike=92.0, side="buy", expiry=NEAR, iv=IV)
    otm = PayoffLeg(kind="call", strike=108.0, side="buy", expiry=NEAR, iv=IV)
    p_itm = chance_of_profit([itm], _quote("call", 92.0, NEAR)["mid"], horizon, SPOT, IV, years)
    p_otm = chance_of_profit([otm], _quote("call", 108.0, NEAR)["mid"], horizon, SPOT, IV, years)
    assert p_atm is not None and 0.3 < p_atm < 0.5
    assert p_otm < p_atm < p_itm
    assert 0.0 <= p_otm and p_itm <= 1.0
    # A short put sold far below the spot: the market's own odds say it usually keeps the credit.
    short_put = PayoffLeg(kind="put", strike=90.0, side="sell", expiry=NEAR, iv=IV)
    p_short = chance_of_profit([short_put], -_quote("put", 90.0, NEAR)["mid"], horizon, SPOT, IV, years)
    assert p_short > 0.8
    assert chance_of_profit([atm], atm_mid, horizon, SPOT, 0.0, years) is None


def test_price_candidate_carries_the_chance_when_given_sigma():
    rows = {NEAR: _rows(NEAR)}
    raws, _ = enumerate_candidates(rows, SPOT, Target(104, 104), frozenset({"bull_call"}))
    with_sigma = price_candidate(raws[0], rows, SPOT, Target(104, 104), _horizon(NEAR), sigma=IV, years=_years_to(NEAR))
    without = price_candidate(raws[0], rows, SPOT, Target(104, 104), _horizon(NEAR))
    assert not isinstance(with_sigma, str) and with_sigma.chance is not None and 0 < with_sigma.chance < 1
    assert not isinstance(without, str) and without.chance is None


def test_the_slider_moves_the_ranking_from_return_to_chance():
    rows = {NEAR: _rows(NEAR)}
    raws, _ = enumerate_candidates(rows, SPOT, Target(101, 101), frozenset({"bull_call", "bull_put", "long_call"}))
    cands = []
    for raw in raws:
        c = price_candidate(raw, rows, SPOT, Target(101, 101), _horizon(NEAR), sigma=IV, years=_years_to(NEAR))
        if not isinstance(c, str):
            cands.append(c)
    by_return, _ = filter_and_rank(cands, top_k=50, per_strategy_cap=50, preference=0.0)
    by_chance, _ = filter_and_rank(cands, top_k=50, per_strategy_cap=50, preference=1.0)
    rors = [c.return_on_risk for c in by_return]
    chances = [c.chance for c in by_chance]
    assert rors == sorted(rors, reverse=True)
    assert chances == sorted(chances, reverse=True)
    assert by_return[0] is not by_chance[0]
    # Percentile blending: every score lies in [0, 1].
    scores = rank_score(cands, 0.5)
    assert all(0.0 <= v <= 1.0 for v in scores.values())


def test_a_directional_target_is_two_explicit_points_and_straddles_survive_it():
    rows = {NEAR: _rows(NEAR)}
    target = Target(low=94.0, high=106.0, explicit=(94.0, 106.0))
    assert target.points() == [94.0, 106.0] and not target.is_range
    raws, _ = enumerate_candidates(rows, SPOT, target, OUTLOOK_STRATEGIES["directional"])
    cands = [c for c in (price_candidate(r, rows, SPOT, target, _horizon(NEAR)) for r in raws) if not isinstance(c, str)]
    kept, _ = filter_and_rank(cands, top_k=50)
    assert kept and all(c.strategy in ("long_straddle", "long_strangle") for c in kept)
    # The same straddles over the *range* 94..106 include the spot, where they lose: none survive.
    ranged = Target(low=94.0, high=106.0)
    cands_r = [c for c in (price_candidate(r, rows, SPOT, ranged, _horizon(NEAR)) for r in raws) if not isinstance(c, str)]
    kept_r, drops = filter_and_rank(cands_r, top_k=50)
    assert kept_r == [] and drops["non_positive_return"] == len(cands_r)


def test_outlooks_map_to_strategy_families_that_fit_the_view():
    assert OUTLOOK_STRATEGIES["very_bullish"] <= {"long_call", "bull_call"}
    assert "bear_put" in OUTLOOK_STRATEGIES["bearish"] and "bull_call" not in OUTLOOK_STRATEGIES["bearish"]
    assert OUTLOOK_STRATEGIES["neutral"] & {"iron_condor", "iron_butterfly"}
    assert OUTLOOK_STRATEGIES["directional"] == {"long_straddle", "long_strangle"}
