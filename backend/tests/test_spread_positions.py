from datetime import date

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
    lone = group_spreads([_pos("SPY260918P00745000", -1, "2")], today=TODAY)[0]
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
