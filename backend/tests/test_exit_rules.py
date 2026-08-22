"""Where a pick actually gets out, and what that was worth in R.

The assumptions live here rather than in a comment, because every one of
them moves the result in a predictable direction: a bar that spans both
levels, a gap through a stop, and the cost charged on both sides all make
results worse, and leaving any of them out flatters exactly the setups
already measured as the worst.
"""

from dataclasses import dataclass

import pytest

from app.scanners.exit_rules import (
    EXIT_BREAKEVEN,
    EXIT_CLOSE,
    EXIT_STOP,
    EXIT_TARGET,
    SIDE_SHORT,
    STOP_ON_CLOSE,
    Exit,
    ExitRule,
    expectancy,
    simulate_exit,
)


@dataclass
class _Bar:
    open: float
    high: float
    low: float
    close: float


def _bar(open_, high, low, close):
    return _Bar(open=open_, high=high, low=low, close=close)


# Risk 2%, aim for 4% -- so a stop is -1R and a target +2R.
_RULE = ExitRule(stop_pct=0.02, reward_ratio=2.0)


# --- the rule itself -----------------------------------------------------


def test_a_stop_is_minus_one_r():
    bars = [_bar(100.0, 100.5, 97.0, 98.0)]

    result = simulate_exit(100.0, bars, _RULE)

    assert result.reason == EXIT_STOP
    assert result.price == pytest.approx(98.0)
    assert result.r_multiple == pytest.approx(-1.0)


def test_a_target_is_plus_the_reward_ratio():
    bars = [_bar(100.0, 105.0, 99.5, 104.5)]

    result = simulate_exit(100.0, bars, _RULE)

    assert result.reason == EXIT_TARGET
    assert result.r_multiple == pytest.approx(2.0)


def test_neither_level_means_out_at_the_close():
    bars = [_bar(100.0, 101.0, 99.5, 100.5), _bar(100.5, 101.5, 99.8, 101.0)]

    result = simulate_exit(100.0, bars, _RULE)

    assert result.reason == EXIT_CLOSE
    assert result.price == pytest.approx(101.0)
    assert result.r_multiple == pytest.approx(0.5)


def test_the_first_level_touched_wins():
    """A later bar cannot undo an exit that already happened."""
    bars = [
        _bar(100.0, 105.0, 99.0, 104.0),  # target here
        _bar(104.0, 104.0, 90.0, 91.0),   # would have stopped, but we are out
    ]

    assert simulate_exit(100.0, bars, _RULE).reason == EXIT_TARGET


# --- the assumptions -----------------------------------------------------


def test_a_bar_spanning_both_levels_is_scored_as_a_stop():
    """Five-minute bars do not say which level came first. The assumption is
    pessimistic on purpose: the optimistic one flatters the most volatile
    setups most, and those are already the worst performers measured."""
    bars = [_bar(100.0, 105.0, 97.0, 100.0)]

    result = simulate_exit(100.0, bars, _RULE)

    assert result.reason == EXIT_STOP
    assert result.r_multiple == pytest.approx(-1.0)


def test_only_a_bar_spanning_both_levels_is_marked_ambiguous():
    """The flag exists so the assumption's weight is visible in a report
    rather than assumed small. It was judged acceptable against a 2% stop; a
    structural stop can sit far tighter, where the same bar covers both
    levels much more often."""
    spanning = simulate_exit(100.0, [_bar(100.0, 105.0, 97.0, 100.0)], _RULE)
    clean = simulate_exit(100.0, [_bar(100.0, 105.0, 99.5, 104.5)], _RULE)

    assert spanning.ambiguous is True
    assert clean.ambiguous is False


def test_a_gap_is_not_ambiguous_the_open_already_decided_it():
    """Nothing was assumed: the trade was out before the bar had a range to
    argue about."""
    result = simulate_exit(100.0, [_bar(90.0, 105.0, 89.0, 104.0)], _RULE)

    assert result.reason == EXIT_STOP
    assert result.ambiguous is False


def test_a_gap_through_the_stop_fills_at_the_open_not_the_stop():
    """The failure this prevents: assuming a stop always fills at its price
    deletes the worst outcomes from the sample -- precisely the ones a stop
    exists to bound. Here the gap costs 5R, not 1R."""
    bars = [_bar(90.0, 91.0, 89.0, 90.0)]

    result = simulate_exit(100.0, bars, _RULE)

    assert result.reason == EXIT_STOP
    assert result.price == pytest.approx(90.0)
    assert result.r_multiple == pytest.approx(-5.0)


def test_a_gap_through_the_target_also_fills_at_the_open():
    """Symmetric rather than charitable -- the same rule in the trader's
    favour."""
    bars = [_bar(110.0, 111.0, 109.0, 110.0)]

    result = simulate_exit(100.0, bars, _RULE)

    assert result.reason == EXIT_TARGET
    assert result.r_multiple == pytest.approx(5.0)


def test_costs_are_charged_entering_and_leaving():
    """Charged in price terms before converting to R, so a tighter stop is
    penalised more -- which is why scalping cannot be judged without it."""
    free = simulate_exit(100.0, [_bar(100.0, 105.0, 99.5, 104.5)], _RULE)
    costed = simulate_exit(
        100.0,
        [_bar(100.0, 105.0, 99.5, 104.5)],
        ExitRule(stop_pct=0.02, reward_ratio=2.0, cost_bps=10),
    )

    # 10bps each way on a 2% stop = 0.2% of entry against a 2% risk = 0.1R.
    assert free.r_multiple - costed.r_multiple == pytest.approx(0.1, abs=1e-3)


def test_the_same_cost_eats_more_of_a_tighter_stop():
    """Why scalping cannot be judged without costs. Comparing raw R across
    stop widths says nothing -- a narrower stop makes the same price move
    more R, and can reach a nearer target besides. What matters is how much
    of R the fixed cost consumes, and that grows as the stop tightens.

    The bar is sized so neither width exits early, or the two would be
    measuring different outcomes rather than the same one at two scales.
    """
    bars = [_bar(100.0, 100.6, 99.8, 100.5)]

    def cost_drag(stop_pct: float) -> float:
        free = simulate_exit(100.0, bars, ExitRule(stop_pct=stop_pct))
        costed = simulate_exit(100.0, bars, ExitRule(stop_pct=stop_pct, cost_bps=10))
        assert free.reason == costed.reason == EXIT_CLOSE
        return free.r_multiple - costed.r_multiple

    # 10bps each way is 0.2% of entry: 0.1R against a 2% stop, 0.4R against
    # a 0.5% one.
    assert cost_drag(0.005) == pytest.approx(0.4, abs=1e-3)
    assert cost_drag(0.02) == pytest.approx(0.1, abs=1e-3)


def test_entering_on_the_last_bar_is_not_a_trade():
    """Nowhere to exit -- scoring it flat at its own entry would add a free
    zero to the sample."""
    result = simulate_exit(100.0, [], _RULE)

    assert result.reason == EXIT_CLOSE
    assert result.r_multiple == pytest.approx(0.0)


# --- the rule's own guards -----------------------------------------------


def test_a_zero_stop_is_refused():
    """It is the denominator of R."""
    with pytest.raises(ValueError):
        ExitRule(stop_pct=0.0)


def test_a_negative_reward_ratio_is_refused():
    with pytest.raises(ValueError):
        ExitRule(stop_pct=0.02, reward_ratio=-1.0)


# --- expectancy ----------------------------------------------------------


def test_expectancy_is_the_mean_r_not_the_win_rate():
    """The whole point of reporting R. Two losers and one large winner is a
    33% win rate and a positive expectancy."""
    exits = [
        Exit(price=0, reason=EXIT_STOP, r_multiple=-1.0),
        Exit(price=0, reason=EXIT_STOP, r_multiple=-1.0),
        Exit(price=0, reason=EXIT_TARGET, r_multiple=4.0),
    ]

    stats = expectancy(exits)

    assert stats["win_rate"] == pytest.approx(33.3, abs=0.1)
    assert stats["expectancy_r"] == pytest.approx(0.6667, abs=1e-3)


def test_a_high_win_rate_can_still_lose():
    """The trap the user asked for by name: 70% winners, negative
    expectancy, because the losses are five times the wins."""
    exits = [Exit(price=0, reason=EXIT_CLOSE, r_multiple=0.2)] * 7
    exits += [Exit(price=0, reason=EXIT_STOP, r_multiple=-1.0)] * 3

    stats = expectancy(exits)

    assert stats["win_rate"] == pytest.approx(70.0)
    assert stats["expectancy_r"] < 0


def test_expectancy_of_nothing_is_none_not_zero():
    stats = expectancy([])

    assert stats["trades"] == 0
    assert stats["expectancy_r"] is None
    assert stats["win_rate"] is None


def test_ambiguous_exits_are_counted_separately():
    """A large share means the result rests on _AMBIGUOUS_BAR_IS_A_STOP
    rather than on the tape -- which is a caveat on the number, not a number
    of its own, so it is reported beside the expectancy."""
    exits = [
        Exit(price=0, reason=EXIT_STOP, r_multiple=-1.0, ambiguous=True),
        Exit(price=0, reason=EXIT_STOP, r_multiple=-1.0),
        Exit(price=0, reason=EXIT_TARGET, r_multiple=2.0),
    ]

    assert expectancy(exits)["ambiguous_exits"] == 1


def test_nothing_is_ambiguous_when_there_is_nothing():
    assert expectancy([])["ambiguous_exits"] == 0


def test_exit_reasons_are_counted():
    exits = [
        Exit(price=0, reason=EXIT_STOP, r_multiple=-1.0),
        Exit(price=0, reason=EXIT_TARGET, r_multiple=2.0),
        Exit(price=0, reason=EXIT_TARGET, r_multiple=2.0),
    ]

    assert expectancy(exits)["exits"] == {EXIT_STOP: 1, EXIT_TARGET: 2}


# --- the short side ------------------------------------------------------
#
# Not a mirror written out twice: simulate_exit compares everything through
# rule.sign, so these assert that the one implementation really does behave
# symmetrically -- including on the parts easiest to get backwards, the gap
# and the ambiguous bar.


_SHORT = ExitRule(stop_pct=0.02, reward_ratio=2.0, side=SIDE_SHORT)


def test_a_short_stops_out_when_price_rises():
    bars = [_bar(100.0, 103.0, 99.5, 102.5)]

    result = simulate_exit(100.0, bars, _SHORT)

    assert result.reason == EXIT_STOP
    assert result.price == pytest.approx(102.0)
    assert result.r_multiple == pytest.approx(-1.0)


def test_a_short_reaches_its_target_when_price_falls():
    bars = [_bar(100.0, 100.5, 95.0, 95.5)]

    result = simulate_exit(100.0, bars, _SHORT)

    assert result.reason == EXIT_TARGET
    assert result.r_multiple == pytest.approx(2.0)


def test_a_short_gaps_against_at_the_open():
    """The case a sign error hides: for a short the damaging gap is *up*."""
    bars = [_bar(110.0, 111.0, 109.0, 110.0)]

    result = simulate_exit(100.0, bars, _SHORT)

    assert result.reason == EXIT_STOP
    assert result.price == pytest.approx(110.0)
    assert result.r_multiple == pytest.approx(-5.0)


def test_a_short_gapping_in_its_favour_is_the_mirror_image():
    bars = [_bar(90.0, 91.0, 89.0, 90.0)]

    result = simulate_exit(100.0, bars, _SHORT)

    assert result.reason == EXIT_TARGET
    assert result.r_multiple == pytest.approx(5.0)


def test_a_short_bar_spanning_both_levels_is_also_a_stop():
    bars = [_bar(100.0, 103.0, 95.0, 100.0)]

    result = simulate_exit(100.0, bars, _SHORT)

    assert result.reason == EXIT_STOP
    assert result.ambiguous is True


def test_a_short_holding_to_the_close_profits_from_a_fall():
    bars = [_bar(100.0, 100.5, 99.0, 99.0)]

    result = simulate_exit(100.0, bars, _SHORT)

    assert result.reason == EXIT_CLOSE
    assert result.r_multiple == pytest.approx(0.5)


def test_costs_hurt_a_short_the_same_way():
    """Charged against the direction of the trade, not against price."""
    bars = [_bar(100.0, 100.5, 95.0, 95.5)]

    costed = simulate_exit(
        100.0, bars, ExitRule(stop_pct=0.02, reward_ratio=2.0, side=SIDE_SHORT, cost_bps=10)
    )

    assert costed.r_multiple == pytest.approx(2.0 - 0.1, abs=1e-3)


def test_an_unknown_side_is_refused():
    with pytest.raises(ValueError, match="side must be"):
        ExitRule(stop_pct=0.02, side="sideways")


# --- a stop that only reads the close ------------------------------------


_CLOSE_STOP = ExitRule(stop_pct=0.02, reward_ratio=2.0, stop_trigger=STOP_ON_CLOSE)


def test_a_dip_through_a_close_stop_does_not_exit():
    """The whole reason this exists. A rule entering as close to VWAP as it
    can has a stop a few cents away; scoring it on touch would stop it out on
    noise and report a strategy nobody described."""
    bars = [_bar(100.0, 100.5, 97.0, 100.2), _bar(100.2, 105.0, 100.0, 104.5)]

    result = simulate_exit(100.0, bars, _CLOSE_STOP)

    assert result.reason == EXIT_TARGET
    assert result.r_multiple == pytest.approx(2.0)


def test_a_close_through_the_stop_exits_at_that_close():
    """And at the close, not at the level -- the level was never an order."""
    bars = [_bar(100.0, 100.5, 97.0, 97.5)]

    result = simulate_exit(100.0, bars, _CLOSE_STOP)

    assert result.reason == EXIT_STOP
    assert result.price == pytest.approx(97.5)
    assert result.r_multiple == pytest.approx(-1.25)


def test_a_close_stop_can_lose_far_more_than_one_r():
    """Its real risk, and why it must not be smoothed away: nothing is in the
    market, so a collapse is worn in full until the bar ends."""
    bars = [_bar(100.0, 100.0, 80.0, 82.0)]

    result = simulate_exit(100.0, bars, _CLOSE_STOP)

    assert result.reason == EXIT_STOP
    assert result.r_multiple == pytest.approx(-9.0)


def test_with_a_close_stop_a_touched_target_wins_and_nothing_is_assumed():
    """Not the pessimistic rule, because there is no ambiguity to resolve: a
    resting limit fills when price trades there, while a close stop is not
    read until the bar is over. Either order leaves the trade out at the
    target."""
    bars = [_bar(100.0, 105.0, 90.0, 91.0)]

    result = simulate_exit(100.0, bars, _CLOSE_STOP)

    assert result.reason == EXIT_TARGET
    assert result.ambiguous is False
    assert result.r_multiple == pytest.approx(2.0)


def test_a_short_close_stop_reads_the_close_above_the_level():
    bars = [_bar(100.0, 105.0, 99.0, 103.0)]

    result = simulate_exit(
        100.0, bars, ExitRule(stop_pct=0.02, side=SIDE_SHORT, stop_trigger=STOP_ON_CLOSE)
    )

    assert result.reason == EXIT_STOP
    assert result.price == pytest.approx(103.0)
    assert result.r_multiple == pytest.approx(-1.5)


def test_an_unknown_stop_trigger_is_refused():
    with pytest.raises(ValueError, match="stop_trigger must be"):
        ExitRule(stop_pct=0.02, stop_trigger="telepathy")


# --- scaling out and trailing to break-even ------------------------------


_SCALED = ExitRule(stop_pct=0.02, reward_ratio=2.0, scale_out=0.5)


def test_half_off_at_the_target_and_the_runner_to_the_close():
    """Half banks +2R; the rest keeps going and closes at +3R, so the trade
    is worth the average of the two."""
    # The low stays above the entry on purpose: a bar that printed the target
    # *and* traded back through the entry is the ambiguous case covered
    # separately below, and would end the runner at break-even here.
    bars = [
        _bar(100.0, 104.0, 100.5, 103.5),
        _bar(103.5, 106.5, 103.0, 106.0),
    ]

    result = simulate_exit(100.0, bars, _SCALED)

    assert [leg.reason for leg in result.legs] == [EXIT_TARGET, EXIT_CLOSE]
    assert result.legs[0].r_multiple == pytest.approx(2.0)
    assert result.legs[1].r_multiple == pytest.approx(3.0)
    assert result.r_multiple == pytest.approx(2.5)


def test_the_runner_stops_at_break_even_not_at_the_original_stop():
    """The point of moving the stop up: once half is banked, the rest cannot
    turn the trade into a loss."""
    bars = [
        _bar(100.0, 104.0, 103.0, 103.5),
        _bar(103.5, 103.5, 98.0, 98.5),
    ]

    result = simulate_exit(100.0, bars, _SCALED)

    assert result.legs[1].reason == EXIT_BREAKEVEN
    assert result.legs[1].r_multiple == pytest.approx(0.0)
    assert result.r_multiple == pytest.approx(1.0)


def test_a_break_even_exit_is_not_counted_as_a_stop():
    """Pooling the two would report a far worse stop rate than the rule has,
    and a break-even is not a loss."""
    bars = [_bar(100.0, 104.0, 103.0, 103.5), _bar(103.5, 103.5, 98.0, 98.5)]

    stats = expectancy([simulate_exit(100.0, bars, _SCALED)])

    assert stats["exits"] == {EXIT_BREAKEVEN: 1}


def test_a_bar_printing_the_target_and_returning_through_entry_is_pessimistic():
    """Same unknowable ordering as anywhere else, resolved the same way and
    flagged so the assumption stays visible."""
    bars = [_bar(100.0, 104.0, 99.0, 100.0)]

    result = simulate_exit(100.0, bars, _SCALED)

    assert [leg.reason for leg in result.legs] == [EXIT_TARGET, EXIT_BREAKEVEN]
    assert result.ambiguous is True
    assert result.r_multiple == pytest.approx(1.0)


def test_scaling_out_does_nothing_when_the_target_is_never_reached():
    """A stop is a stop -- there was never a partial to take."""
    bars = [_bar(100.0, 100.5, 97.0, 98.0)]

    result = simulate_exit(100.0, bars, _SCALED)

    assert len(result.legs) == 1
    assert result.r_multiple == pytest.approx(-1.0)


def test_scaling_out_shorts_too():
    # Mirror of the long case: for a short the entry must stay untouched from
    # *above*, so the trigger bar high sits below it.
    bars = [_bar(99.5, 99.8, 96.0, 96.5), _bar(96.5, 97.0, 94.0, 94.0)]

    result = simulate_exit(100.0, bars, ExitRule(stop_pct=0.02, scale_out=0.5, side=SIDE_SHORT))

    assert [leg.reason for leg in result.legs] == [EXIT_TARGET, EXIT_CLOSE]
    assert result.legs[0].r_multiple == pytest.approx(2.0)
    assert result.legs[1].r_multiple == pytest.approx(3.0)


def test_the_fractions_of_a_scaled_exit_add_up_to_the_whole_position():
    bars = [_bar(100.0, 104.0, 103.0, 103.5), _bar(103.5, 104.0, 103.0, 103.5)]

    result = simulate_exit(100.0, bars, ExitRule(stop_pct=0.02, scale_out=0.25))

    assert sum(leg.fraction for leg in result.legs) == pytest.approx(1.0)
    assert [leg.fraction for leg in result.legs] == [0.25, 0.75]


def test_an_unscaled_exit_still_reports_exactly_one_leg():
    """So a caller reading legs does not have to special-case the common
    path."""
    result = simulate_exit(100.0, [_bar(100.0, 105.0, 99.5, 104.5)], _RULE)

    assert len(result.legs) == 1
    assert result.legs[0].fraction == 1.0
    assert result.legs[0].r_multiple == result.r_multiple


def test_a_scale_out_of_the_whole_position_is_refused():
    """It is what scale_out=0 already means; two spellings of one behaviour
    invite a report to distinguish them."""
    with pytest.raises(ValueError, match="scale_out"):
        ExitRule(stop_pct=0.02, scale_out=1.0)
    with pytest.raises(ValueError, match="scale_out"):
        ExitRule(stop_pct=0.02, scale_out=-0.5)
