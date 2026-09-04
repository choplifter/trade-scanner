from datetime import date

import pytest

from app.options.positions import group_spreads

TODAY = date(2026, 9, 2)


def _pos(symbol, qty, entry, current="1.00", mv="100", pl="5", asset_class="us_option") -> dict:
    return {
        "symbol": symbol,
        "asset_class": asset_class,
        "qty": str(qty),
        "avg_entry_price": str(entry),
        "current_price": current,
        "market_value": mv,
        "unrealized_pl": pl,
        "cost_basis": "90",
    }


def test_bull_put_is_recognised_with_a_net_credit_entry():
    groups = group_spreads(
        [_pos("SPY260918P00745000", -2, "2.10"), _pos("SPY260918P00740000", 2, "1.10"), _pos("AAPL", 10, "150", asset_class="us_equity")],
        today=TODAY,
    )
    assert len(groups) == 1
    g = groups[0]
    assert g.id == "SPY:2026-09-18" and g.strategy == "bull_put" and g.qty == 2 and not g.broken
    assert g.dte == 16
    assert [leg.strike for leg in g.legs] == [740.0, 745.0]
    assert g.net_entry == -1.0  # paid 1.10, received 2.10
    assert g.unrealized_pl == 10.0 and g.market_value == 200.0


def test_each_vertical_shape():
    def strat(a, b):
        return group_spreads([a, b], today=TODAY)[0].strategy

    assert strat(_pos("SPY260918C00745000", 1, "3"), _pos("SPY260918C00750000", -1, "1")) == "bull_call"
    assert strat(_pos("SPY260918C00745000", -1, "3"), _pos("SPY260918C00750000", 1, "1")) == "bear_call"
    assert strat(_pos("SPY260918P00750000", 1, "3"), _pos("SPY260918P00745000", -1, "1")) == "bear_put"


def test_iron_condor():
    g = group_spreads(
        [
            _pos("SPY260918P00735000", 1, "0.5"),
            _pos("SPY260918P00740000", -1, "1.0"),
            _pos("SPY260918C00755000", -1, "1.1"),
            _pos("SPY260918C00760000", 1, "0.6"),
        ],
        today=TODAY,
    )[0]
    assert g.strategy == "iron_condor" and g.qty == 1
    assert g.net_entry == -1.0


def test_lone_leg_and_unbalanced_legs_are_broken():
    # A lone short put is cash-secured (Alpaca allows no naked writing at
    # level 3); a lone short call without shares is a spread's remains.
    lone_put = group_spreads([_pos("SPY260918P00745000", -1, "2")], today=TODAY)[0]
    assert lone_put.strategy == "cash_secured_put" and not lone_put.broken and lone_put.qty == 1
    lone = group_spreads([_pos("SPY260918C00745000", -1, "2")], today=TODAY)[0]
    assert lone.strategy == "broken" and lone.broken and lone.qty == 1
    unbalanced = group_spreads(
        [_pos("SPY260918P00745000", -2, "2"), _pos("SPY260918P00740000", 1, "1")], today=TODAY
    )[0]
    assert unbalanced.broken and unbalanced.qty == 1


def test_adjusted_root_groups_under_the_underlying_and_groups_split_by_expiry():
    groups = group_spreads(
        [
            _pos("SPY1260918P00745000", -1, "2"),
            _pos("SPY1260918P00740000", 1, "1"),
            _pos("SPY260925C00750000", 1, "1"),
        ],
        today=TODAY,
    )
    assert [(g.underlying, g.root, g.expiry.isoformat()) for g in groups] == [
        ("SPY", "SPY1", "2026-09-18"),
        ("SPY", "SPY", "2026-09-25"),
    ]
    assert groups[0].strategy == "bull_put"


def test_account_is_carried_and_equities_ignored():
    assert group_spreads([_pos("AAPL", 10, "150", asset_class="us_equity")], today=TODAY) == []
    assert group_spreads([_pos("SPY260918P00745000", -1, "2")], account="live", today=TODAY)[0].account == "live"


def test_a_lone_long_contract_is_a_long_call_or_put_not_broken():
    call = group_spreads([_pos("SPY260918C00750000", 3, "2.50")], today=TODAY)[0]
    assert call.strategy == "long_call" and not call.broken and call.qty == 3
    assert call.net_entry == 2.5
    put = group_spreads([_pos("SPY260918P00740000", 1, "3")], today=TODAY)[0]
    assert put.strategy == "long_put" and not put.broken


def test_straddle_strangle_and_butterflies_are_recognised():
    straddle = group_spreads([_pos("SPY260918P00750000", 2, "4"), _pos("SPY260918C00750000", 2, "4.5")], today=TODAY)[0]
    assert straddle.strategy == "long_straddle" and straddle.qty == 2 and straddle.net_entry == 8.5
    strangle = group_spreads([_pos("SPY260918P00745000", 1, "2"), _pos("SPY260918C00755000", 1, "2")], today=TODAY)[0]
    assert strangle.strategy == "long_strangle"
    fly = group_spreads(
        [_pos("SPY260918C00745000", 1, "6"), _pos("SPY260918C00750000", -2, "3"), _pos("SPY260918C00755000", 1, "1.2")],
        today=TODAY,
    )[0]
    assert fly.strategy == "call_butterfly" and fly.qty == 1 and not fly.broken
    assert fly.net_entry == pytest.approx(6 + 1.2 - 2 * 3)
    iron = group_spreads(
        [
            _pos("SPY260918P00745000", 1, "1"),
            _pos("SPY260918P00750000", -1, "3"),
            _pos("SPY260918C00750000", -1, "3"),
            _pos("SPY260918C00755000", 1, "1"),
        ],
        today=TODAY,
    )[0]
    assert iron.strategy == "iron_butterfly" and iron.net_entry == -4.0


def test_calendar_pairs_across_expiries_and_the_rest_stays_per_expiry():
    groups = group_spreads(
        [
            _pos("SPY260918C00750000", -1, "2"),
            _pos("SPY261016C00750000", 1, "5"),
            _pos("SPY261016P00740000", 1, "1"),
        ],
        today=TODAY,
    )
    assert [(g.strategy, g.expiry.isoformat(), g.long_expiry.isoformat() if g.long_expiry else None) for g in groups] == [
        ("calendar", "2026-09-18", "2026-10-16"),
        ("long_put", "2026-10-16", None),
    ]
    assert groups[0].net_entry == 3.0 and groups[0].qty == 1
    diagonal = group_spreads(
        [_pos("SPY260918P00745000", -2, "2"), _pos("SPY261016P00750000", 2, "5")], today=TODAY
    )[0]
    assert diagonal.strategy == "diagonal" and diagonal.qty == 2


def test_covered_call_needs_the_shares():
    short_call = _pos("SPY260918C00760000", -2, "1.5")
    shares = _pos("SPY", 250, "740", asset_class="us_equity")
    covered = group_spreads([short_call], equity_positions=[shares], today=TODAY)[0]
    assert covered.strategy == "covered_call" and covered.qty == 2 and covered.shares == 200 and not covered.broken
    assert covered.net_entry == -1.5
    # 150 shares cover one contract; the other stands alone.
    partial = group_spreads(
        [short_call], equity_positions=[_pos("SPY", 150, "740", asset_class="us_equity")], today=TODAY
    )[0]
    assert partial.strategy == "broken"
    # Shares of another symbol do not count.
    other = group_spreads([short_call], equity_positions=[_pos("QQQ", 500, "700", asset_class="us_equity")], today=TODAY)[0]
    assert other.strategy == "broken"


def test_a_condor_plus_a_later_long_put_are_two_groups_not_five_legs():
    # The five-leg "custom" row this used to make broke every downstream
    # request (payoff, close, triggers), which take four legs at most.
    groups = group_spreads(
        [
            _pos("SPY260908P00755000", 1, "0.5"),
            _pos("SPY260908P00760000", -1, "1.0"),
            _pos("SPY260908C00780000", -1, "1.0"),
            _pos("SPY260908C00785000", 1, "0.5"),
            _pos("SPY260908P00770000", 1, "2.4"),
        ],
        today=TODAY,
    )
    assert [(g.strategy, len(g.legs), g.qty, g.broken) for g in groups] == [
        ("iron_condor", 4, 1, False),
        ("long_put", 1, 1, False),
    ]
    assert [g.id for g in groups] == ["SPY:2026-09-08", "SPY:2026-09-08:1"]
    assert groups[0].net_entry == -1.0 and groups[1].net_entry == 2.4


def test_a_vertical_plus_a_stray_long_leg_is_peeled_apart():
    groups = group_spreads(
        [
            _pos("SPY260918P00745000", -2, "2.10"),
            _pos("SPY260918P00740000", 2, "1.10"),
            _pos("SPY260918C00760000", 1, "0.9"),
        ],
        today=TODAY,
    )
    assert [(g.strategy, g.qty) for g in groups] == [("bull_put", 2), ("long_call", 1)]


def test_unrecognisable_legs_stay_one_group_up_to_four_then_split_singly():
    # Two short calls without shares: no known shape, still one row.
    groups = group_spreads(
        [_pos("SPY260918C00745000", -1, "3"), _pos("SPY260918C00750000", -1, "1")], today=TODAY
    )
    assert len(groups) == 1 and groups[0].strategy == "custom"
    # Five long calls at different strikes: no structure, one row each.
    groups = group_spreads(
        [_pos(f"SPY260918C00{strike}000", 1, "1") for strike in (740, 745, 750, 755, 760)], today=TODAY
    )
    assert len(groups) == 5 and all(g.strategy == "long_call" for g in groups)
    assert len({g.id for g in groups}) == 5
