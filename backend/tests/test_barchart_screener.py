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


def test_enrich_fetches_each_chain_once_and_notes_rows_past_the_budget():
    import asyncio
    import math

    from app.options.barchart_screener import enrich
    from app.options.chain import Chain, LegQuote, StrikeRow

    expiry = date(2026, 11, 20)

    def quote(kind, k, spot=230.0, iv=0.35):
        t = 45 / 365
        sd = iv * math.sqrt(t)
        d1 = (math.log(spot / k) + 0.5 * sd * sd) / sd
        n = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))  # noqa: E731
        delta = n(d1) if kind == "call" else n(d1) - 1
        return LegQuote(symbol=f"{kind}{k}", strike=k, kind=kind, expiry=expiry, bid=1.0, ask=1.1, mid=1.05, last=1.0,
                        bid_size=1, ask_size=1, delta=delta, gamma=0.0, theta=0.0, iv=iv, open_interest=10, tradable=True)

    class Service:
        def __init__(self):
            self.calls = []

        async def chain(self, symbol, exp, width=0.10):
            self.calls.append(symbol)
            self.widths = getattr(self, "widths", []) + [width]
            rows = [StrikeRow(strike=k, call=quote("call", k), put=quote("put", k)) for k in range(150, 330, 5)]
            return Chain(underlying=symbol, expiry=exp, spot=230.0, feed="opra", as_of=None, rows=rows)

    text = CONDOR.split("BAD,")[0]  # NVDA and MU rows
    rows, _ = parse(text + "NVDA,233.95,2026-11-20,48,175.00,Put,0.6,190.00,Put,1.1,300.00,Call,0.4,315.00,Call,0.2,1,1,0.7,14.3,\"20 to 1\",10%,6%\n")
    service = Service()
    out = asyncio.run(enrich(rows, service, today=date(2026, 10, 6), limit=10, max_chains=1))

    assert service.calls == ["NVDA"], "two NVDA rows share one chain; MU is past the budget of one"
    # NVDA's wings at 170 and 320 around 234: 27 % out, so the 50 % band.
    assert service.widths == [0.50]
    nvda = [r for r in out if r["symbol"] == "NVDA"]
    assert len(nvda) == 2 and all("expected_value" in r["ours"] for r in nvda)
    mu = next(r for r in out if r["symbol"] == "MU")
    assert "not checked" in mu["ours"]["note"]


def test_the_export_says_what_session_its_prices_are_from():
    from app.options.barchart_screener import export_date

    assert export_date(CONDOR) == date(2026, 10, 3)
    assert export_date("Symbol,Exp Date\n") is None


def test_our_figures_are_priced_at_todays_natural_for_the_same_strikes():
    from app.options.barchart_screener import natural_net
    from app.options.chain import Chain, LegQuote, StrikeRow

    def q(kind, k, bid, ask):
        return LegQuote(symbol=f"{kind}{k}", strike=k, kind=kind, expiry=date(2026, 11, 20), bid=bid, ask=ask, mid=(bid + ask) / 2,
                        last=bid, bid_size=1, ask_size=1, delta=None, gamma=None, theta=None, iv=0.3, open_interest=1, tradable=True)

    rows = [StrikeRow(170.0, None, q("put", 170.0, 0.40, 0.45)), StrikeRow(185.0, None, q("put", 185.0, 0.80, 0.86)),
            StrikeRow(305.0, q("call", 305.0, 0.20, 0.24), None), StrikeRow(320.0, q("call", 320.0, 0.10, 0.13), None)]
    chain = Chain(underlying="NVDA", expiry=date(2026, 11, 20), spot=233.0, feed="opra", as_of=None, rows=rows)
    nvda = parse(CONDOR)[0][0]
    # Sold at the bids (0.80 + 0.20), bought at the asks (0.45 + 0.13).
    assert natural_net(nvda, chain) == pytest.approx(0.42)
    chain.rows = rows[:3]
    assert natural_net(nvda, chain) is None, "a leg missing from today's chain"
