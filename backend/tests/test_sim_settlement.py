"""The settlement decision at expiry -- app.trading.sim.settlement. Pure, so
every branch of the table is a one-liner against numbers."""

from app.trading.sim.settlement import Settlement, decide_settlement


def _one(**kw) -> Settlement:
    out = decide_settlement(**kw)
    assert len(out) == 1
    return out[0]


def test_out_of_the_money_expires_whatever_the_side():
    for side, kind, strike, spot in (("short", "put", 95.0, 100.0), ("short", "call", 105.0, 100.0), ("long", "call", 105.0, 100.0), ("long", "put", 95.0, 100.0)):
        s = _one(side=side, kind=kind, strike=strike, spot=spot, contracts=2, shares_long=0)
        assert s.outcome == "expired" and s.option_price == 0.0 and not s.moves_shares and s.contracts == 2
    # At the money is worthless too.
    assert _one(side="short", kind="put", strike=100.0, spot=100.0, contracts=1, shares_long=0).outcome == "expired"


def test_an_in_the_money_short_put_is_assigned_at_the_strike_and_the_option_closes_at_zero():
    s = _one(side="short", kind="put", strike=100.0, spot=92.0, contracts=3, shares_long=0)

    assert s.outcome == "assigned"
    assert s.option_price == 0.0
    assert (s.share_side, s.share_qty, s.share_price) == ("buy", 300, 100.0)


def test_a_covered_short_call_is_called_away_and_an_uncovered_one_cash_settled():
    covered = _one(side="short", kind="call", strike=100.0, spot=108.0, contracts=2, shares_long=200)
    assert covered.outcome == "called_away"
    assert (covered.share_side, covered.share_qty, covered.share_price) == ("sell", 200, 100.0)
    assert covered.option_price == 0.0

    naked = _one(side="short", kind="call", strike=100.0, spot=108.0, contracts=2, shares_long=0)
    assert naked.outcome == "cash_settled" and naked.option_price == 8.0 and not naked.moves_shares

    # Ninety-nine shares cover nothing.
    assert _one(side="short", kind="call", strike=100.0, spot=108.0, contracts=1, shares_long=99).outcome == "cash_settled"


def test_a_partly_covered_short_call_splits():
    out = decide_settlement(side="short", kind="call", strike=100.0, spot=108.0, contracts=3, shares_long=150)

    assert [(s.outcome, s.contracts) for s in out] == [("called_away", 1), ("cash_settled", 2)]
    assert out[0].share_qty == 100 and out[1].option_price == 8.0


def test_long_in_the_money_options_are_cash_settled_at_intrinsic():
    call = _one(side="long", kind="call", strike=100.0, spot=105.5, contracts=1, shares_long=0)
    put = _one(side="long", kind="put", strike=100.0, spot=97.25, contracts=1, shares_long=500)

    assert call.outcome == "cash_settled" and call.option_price == 5.5 and not call.moves_shares
    assert put.outcome == "cash_settled" and put.option_price == 2.75 and not put.moves_shares


def test_no_contracts_is_nothing_to_settle():
    assert decide_settlement(side="short", kind="put", strike=100.0, spot=90.0, contracts=0, shares_long=0) == []
