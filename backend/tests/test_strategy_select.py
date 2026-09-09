"""The rule matrix: one test per rule, plus the two rules the whole
module rests on -- absent data never scores, and every point carries the
number that produced it."""

from __future__ import annotations

import pytest

from app.options.strategy_select import (
    FAMILY_GROUPS,
    Signals,
    pick_families,
    score_families,
    target_policy,
    warnings_for,
)

SPOT = 100.0


def _scores(**over) -> dict[str, int]:
    base = {"spot": SPOT}
    base.update(over)
    return {s.family: s.score for s in score_families(Signals(**base))}


def _entry(family: str, **over):
    base = {"spot": SPOT}
    base.update(over)
    return next(s for s in score_families(Signals(**base)) if s.family == family)


# --- the two load-bearing rules ----------------------------------------------------


def test_a_symbol_with_no_signals_scores_nothing_either_way():
    scores = _scores()
    assert set(scores) == set(FAMILY_GROUPS)
    assert all(score == 0 for score in scores.values())
    assert _entry("iron_condor").reasons == []


def test_absent_inputs_are_never_read_as_evidence():
    # Every optional input None, including the ones whose "low" side would
    # otherwise favour buying premium.
    quiet = _scores(iv_rank_pct=None, iv_over_realized=None, skew=None, gex_regime=None, wing_room=None)
    assert all(score == 0 for score in quiet.values())
    # And a known-low IV rank does move the same families, so the test
    # above is measuring absence rather than a rule that never fires.
    assert _scores(iv_rank_pct=10.0)["long_call"] == 1


def test_every_point_names_the_number_behind_it():
    entry = _entry("iron_condor", implied_move_pct=18.0, hist_median_pct=10.0, samples=6)
    assert entry.score == 2
    assert "18.0 %" in entry.reasons[0] and "10.0 %" in entry.reasons[0] and "x1.80" in entry.reasons[0]


# --- implied against historical ------------------------------------------------------


def test_an_implied_move_richer_than_history_favours_selling_it():
    scores = _scores(implied_move_pct=18.0, hist_median_pct=10.0, samples=6)
    assert scores["iron_condor"] == 2 and scores["iron_butterfly"] == 2
    assert scores["bull_put"] == 1 and scores["calendar"] == 1
    assert scores["long_straddle"] == -2 and scores["long_strangle"] == -2


def test_an_implied_move_cheaper_than_history_favours_buying_it():
    scores = _scores(implied_move_pct=6.0, hist_median_pct=10.0, samples=6)
    assert scores["long_straddle"] == 2 and scores["long_strangle"] == 2
    assert scores["bull_call"] == 1 and scores["iron_condor"] == -2 and scores["bear_call"] == -1


def test_one_past_report_is_not_a_history():
    # A single sample would make the ratio a coin flip dressed as evidence.
    assert _scores(implied_move_pct=18.0, hist_median_pct=10.0, samples=1)["iron_condor"] == 0
    assert _scores(implied_move_pct=18.0, hist_median_pct=None, samples=6)["iron_condor"] == 0


# --- the other signals ----------------------------------------------------------------


def test_a_steep_term_structure_favours_the_calendar_and_marks_down_buying_the_front():
    scores = _scores(front_iv=2.30, back_iv=0.90)
    assert scores["calendar"] == 2 and scores["long_straddle"] == -1 and scores["long_call"] == -1


def test_iv_rank_moves_premium_sellers_and_buyers_opposite_ways():
    assert _scores(iv_rank_pct=72.0)["iron_condor"] == 1
    assert _scores(iv_rank_pct=72.0)["bull_put"] == 1
    cheap = _scores(iv_rank_pct=12.0)
    assert cheap["call_butterfly"] == 1 and cheap["bull_call"] == 1 and cheap["iron_condor"] == -1


def test_implied_over_realised_works_on_day_one_without_any_rank():
    assert _scores(iv_over_realized=2.4)["iron_condor"] == 1
    assert _scores(iv_over_realized=0.7)["long_straddle"] == 1


def test_gex_positions_the_neutral_families_between_the_walls():
    walled = _scores(gex_regime="positive", call_wall=110.0, put_wall=90.0)
    assert walled["iron_condor"] == 1
    assert "call wall at 110" in _entry("iron_condor", gex_regime="positive", call_wall=110.0, put_wall=90.0).reasons[0]
    # Outside the walls the rule does not fire.
    assert _scores(gex_regime="positive", call_wall=95.0, put_wall=90.0)["iron_condor"] == 0

    short_gamma = _scores(gex_regime="negative")
    assert short_gamma["long_straddle"] == 1 and short_gamma["iron_condor"] == -1
    flip = _scores(gex_regime="positive", near_flip=True, call_wall=110.0, put_wall=90.0)
    assert flip["long_strangle"] == 1 and flip["iron_butterfly"] == -1


def test_skew_picks_the_richer_side_to_sell():
    puts_bid = _scores(skew=0.08)
    assert puts_bid["bull_put"] == 1 and puts_bid["bear_call"] == -1
    calls_bid = _scores(skew=-0.08)
    assert calls_bid["bear_call"] == 1 and calls_bid["bull_put"] == -1


def test_without_wings_the_four_legged_families_fall_below_the_two_legged_ones():
    scores = _scores(wing_room=False, offerable_puts_below=1, offerable_calls_above=0)
    assert scores["iron_condor"] == -2 and scores["iron_butterfly"] == -2
    assert scores["bull_put"] == 1 and scores["call_butterfly"] == 1
    assert scores["bull_put"] > scores["iron_condor"]
    assert "1 quotable strikes below and 0 above" in _entry("iron_condor", wing_room=False, offerable_puts_below=1).reasons[0]


def test_a_red_tape_marks_down_selling_premium_into_a_print():
    scores = _scores(market_level="red", implied_move_pct=18.0, hist_median_pct=10.0, samples=6)
    assert scores["iron_condor"] == 0  # +2 for the rich move, -2 for the tape
    assert scores["bull_put"] == -1


# --- picking and targets ---------------------------------------------------------------


def test_picks_are_the_best_positive_neutral_and_volatility_families():
    signals = Signals(spot=SPOT, implied_move_pct=18.0, hist_median_pct=10.0, samples=6, iv_rank_pct=72.0)
    scores = score_families(signals)
    picks = pick_families(scores)

    assert picks[0] == "iron_condor"  # the only family both rules agree on
    assert len(picks) <= 3
    assert all(FAMILY_GROUPS[f] in ("neutral", "volatility") for f in picks)
    # Nothing the signals argue against is picked, and the order is the score's.
    by_family = {s.family: s.score for s in scores}
    assert all(by_family[f] > 0 for f in picks)
    assert [by_family[f] for f in picks] == sorted((by_family[f] for f in picks), reverse=True)
    assert "long_straddle" not in picks


def test_directional_and_calendar_families_are_scored_but_never_picked():
    signals = Signals(spot=SPOT, front_iv=2.3, back_iv=0.9, iv_rank_pct=10.0)
    scores = score_families(signals)
    assert next(s for s in scores if s.family == "calendar").score == 2
    assert "calendar" not in pick_families(scores)
    assert not any(FAMILY_GROUPS[f] == "directional" for f in pick_families(scores))


def test_a_red_tape_caps_the_picks_at_two():
    signals = Signals(spot=SPOT, implied_move_pct=6.0, hist_median_pct=10.0, samples=6, iv_over_realized=0.7, gex_regime="negative")
    assert len(pick_families(score_families(signals), market_level="red")) <= 2


def test_the_neutral_target_is_half_the_typical_move_and_the_volatility_one_the_larger():
    signals = Signals(spot=SPOT, implied_move_pct=18.0, hist_median_pct=10.0, samples=6)
    policy = target_policy(signals, ["iron_condor", "long_strangle"])

    # Half, not the whole move: a structure judged over the full one has to
    # place its shorts beyond it, where the chain is thin or stops.
    assert policy["neutral"]["move"] == pytest.approx(0.05)
    assert policy["neutral"]["full_move"] == pytest.approx(0.10)
    assert policy["neutral"]["basis"] == "half the median past report move"
    assert policy["volatility"]["move"] == pytest.approx(0.18)
    assert policy["neutral"]["families"] == ["iron_condor"]

    # No history: the implied move stands in, and the basis says so.
    only_implied = target_policy(Signals(spot=SPOT, implied_move_pct=18.0), ["iron_condor"])
    assert only_implied["neutral"]["move"] == pytest.approx(0.09)
    assert only_implied["neutral"]["basis"] == "half the implied move"
    assert only_implied["volatility"] is None


# --- warnings ---------------------------------------------------------------------------


def test_warnings_name_what_is_missing_rather_than_assuming_it():
    said = " ".join(warnings_for(Signals(spot=SPOT, implied_move_pct=18.0, reported=True, market_level="red")))
    assert "already published" in said
    assert "red" in said
    assert "not being compared" in said
    assert "unknown, not unremarkable" in said  # no IV rank
    assert "unknown, not neutral" in said  # no GEX

    quiet = warnings_for(Signals(spot=SPOT, iv_rank_pct=50.0, gex_regime="positive", implied_move_pct=5.0, hist_median_pct=5.0, samples=4))
    assert quiet == []
