"""Barchart's screener exports: the header read by position (its "Type"
repeats), each leg's side from its price column, the shape classified and
turned into a ticket, and only export-shaped names accepted."""

from datetime import date

import pytest

from app.options.barchart_screener import classify, is_screener_export, Leg, parse, ticket_for

CONDOR = '''Symbol,Price~,"Exp Date",DTE,"Leg1 Strike",Type,Ask1,"Leg2 Strike",Type,Bid2,"Leg3 Strike",Type,Bid3,"Leg4 Strike",Type,Ask4,BE+,BE-,"Max Profit","Max Loss",Risk/Reward,"IV Rank","Loss Prob"
NVDA,233.95,2026-11-20,48,170.00,Put,0.51,185.00,Put,0.92,305.00,Call,0.26,320.00,Call,0.15,305.52,184.48,0.52,14.48,"27.85 to 1",10.84%,4.8%
MU,"1,074.89",2026-10-16,13,800.00,Put,0.57,850.00,Put,0.77,"1,250.00",Call,3.65,"1,300.00",Call,2.14,1251.71,848.29,1.71,48.29,"28.24 to 1",0.00%,5.1%
BAD,10,not-a-date,13,1,Put,1,2,Put,1,3,Call,1,4,Call,1,1,1,1,1,"1 to 1",1%,1%
"Downloaded from Barchart.com as of 10-03-2026 02:13am CDT"
'''

VERTICAL = '''Symbol,Price~,"Exp Date",DTE,"Leg1 Strike",Type,Ask1,"Leg2 Strike",Type,Bid2,BE,"Max Profit","Max Loss","Loss Prob"
XLF,50.10,2026-11-20,45,46.00,Put,0.20,48.00,Put,0.55,47.65,0.35,1.65,22.0%
'''


def test_a_condor_export_is_read_leg_by_leg_and_the_trailer_and_bad_lines_are_left_out():
    rows, problems = parse(CONDOR)
    assert [r.symbol for r in rows] == ["NVDA", "MU"]
    nvda = rows[0]
    assert [(l.kind, l.side, l.strike) for l in nvda.legs] == [
        ("put", "buy", 170.0), ("put", "sell", 185.0), ("call", "sell", 305.0), ("call", "buy", 320.0),
    ]
    assert nvda.strategy == "iron_condor" and nvda.net == pytest.approx(0.52)  # 0.92 + 0.26 - 0.51 - 0.15
    assert nvda.loss_prob == pytest.approx(0.048) and nvda.iv_rank == pytest.approx(10.84)
    assert rows[1].legs[3].strike == 1300.0, "thousands separators"
    assert any("expiry" in p for p in problems) and not any("Downloaded" in p for p in problems)


def test_the_condor_becomes_the_widgets_ticket():
    ticket = ticket_for(parse(CONDOR)[0][0])
    assert ticket == {
        "underlying": "NVDA", "strategy": "iron_condor", "expiry": "2026-11-20", "qty": 1,
        "put_long_strike": 170.0, "put_short_strike": 185.0, "call_short_strike": 305.0, "call_long_strike": 320.0,
    }


def test_a_two_leg_export_is_a_vertical():
    rows, _ = parse(VERTICAL)
    assert rows[0].strategy == "bull_put" and rows[0].net == pytest.approx(0.35)
    assert ticket_for(rows[0])["long_strike"] == 46.0 and ticket_for(rows[0])["short_strike"] == 48.0


def test_shapes_we_do_not_trade_get_no_ticket():
    odd = [Leg(100, "put", "sell", 1.0), Leg(105, "call", "sell", 1.0)]  # a short strangle
    assert classify(odd) is None
    assert classify([Leg(100, "call", "sell", 2.0), Leg(105, "call", "buy", 1.0)]) == "bear_call"


def test_only_export_shaped_names_are_accepted():
    assert is_screener_export("short-iron-condor-option-screener-10-03-2026.csv")
    assert is_screener_export("short-iron-condor-option-screener-iron-condor-1-10-06-2026.csv")
    assert not is_screener_export("../secrets.csv")
    assert not is_screener_export("spy_options-overview-history-10-03-2026.csv")
    assert not is_screener_export("Packliste.xlsx")


def test_a_file_that_is_not_a_spread_screener_says_so():
    rows, problems = parse("Symbol,Name,Latest\nAAPL,Apple,333\n")
    assert rows == [] and "not a spread screener export" in problems[0]
