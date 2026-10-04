"""Position greeks, breakeven volatility and early-assignment warnings
(app.options.position_risk). No network: the clock is handed in and the
dividends are built by hand."""

import asyncio
from datetime import date, datetime, timedelta, timezone

from app.options.chain import LegQuote
from app.options.payoff import bs_price, years_between
from app.options.position_risk import (
    Dividend,
    RiskLeg,
    assignment_risks,
    breakeven_vol,
    bs_vega,
    position_greeks,
    spread_risks,
)
from app.options.positions import SpreadGroup, SpreadPositionLeg
from app.services.market_clock import ET

NOW = datetime.now(timezone.utc).astimezone(ET).replace(hour=11, minute=0, second=0, microsecond=0)
EXPIRY = NOW.date() + timedelta(days=45)
SPOT = 100.0


def _condor(iv: float = 0.20, qty: float = 1) -> list[RiskLeg]:
    return [
        RiskLeg("put", 85.0, EXPIRY, qty, iv),
        RiskLeg("put", 90.0, EXPIRY, -qty, iv),
        RiskLeg("call", 110.0, EXPIRY, -qty, iv),
        RiskLeg("call", 115.0, EXPIRY, qty, iv),
    ]


def _value(legs: list[RiskLeg], sigma: float) -> float:
    years = years_between(NOW, EXPIRY)
    return sum(leg.qty * bs_price(leg.kind, SPOT, leg.strike, years, sigma) for leg in legs)


# --- greeks ------------------------------------------------------------------


def test_vega_is_the_price_change_per_vol_point():
    years = years_between(NOW, EXPIRY)
    numeric = (bs_price("call", SPOT, 100, years, 0.21) - bs_price("call", SPOT, 100, years, 0.19)) / 2
    assert abs(bs_vega(SPOT, 100, years, 0.20) - numeric) < 1e-3


def test_a_short_condor_is_short_vega_and_long_theta():
    g = position_greeks(_condor(qty=2), SPOT, NOW)
    assert g is not None
    assert g.vega < 0, "a condor loses when implied volatility rises"
    assert g.theta > 0, "and earns as time passes"
    assert g.gamma < 0
    assert abs(g.delta) < 5, "near delta-neutral when centred on spot"


def test_greeks_scale_with_the_number_of_structures():
    one = position_greeks(_condor(qty=1), SPOT, NOW)
    three = position_greeks(_condor(qty=3), SPOT, NOW)
    assert abs(three.vega - 3 * one.vega) < 1e-9 and abs(three.theta - 3 * one.theta) < 1e-9


def test_a_leg_without_iv_gives_no_greeks_rather_than_a_partial_sum():
    legs = _condor()
    legs[0] = RiskLeg("put", 85.0, EXPIRY, 1, None)
    assert position_greeks(legs, SPOT, NOW) is None


def test_shares_count_one_delta_each():
    covered = [RiskLeg("call", 105.0, EXPIRY, -1, 0.2), RiskLeg("stock", 0.0, None, 100)]
    g = position_greeks(covered, SPOT, NOW)
    call_only = position_greeks(covered[:1], SPOT, NOW)
    assert abs(g.delta - call_only.delta - 100) < 1e-9


# --- breakeven volatility ----------------------------------------------------


def test_a_package_priced_at_the_model_value_breaks_even_at_its_own_iv():
    legs = _condor(0.20)
    price = _value(legs, 0.20)
    assert abs(breakeven_vol(legs, SPOT, NOW, price, 0.20) - 0.20) < 1e-4


def test_selling_the_condor_for_more_raises_the_breakeven_volatility():
    legs = _condor(0.20)
    richer = _value(legs, 0.26)  # more negative: a bigger credit
    assert abs(breakeven_vol(legs, SPOT, NOW, richer, 0.20) - 0.26) < 1e-3


def test_the_smile_shape_is_kept_and_the_answer_is_an_atm_figure():
    # Wings carry a higher IV than the body; the breakeven is the ATM level
    # the whole smile would have to sit at.
    legs = [
        RiskLeg("put", 85.0, EXPIRY, 1, 0.30),
        RiskLeg("put", 90.0, EXPIRY, -1, 0.26),
        RiskLeg("call", 110.0, EXPIRY, -1, 0.18),
        RiskLeg("call", 115.0, EXPIRY, 1, 0.17),
    ]
    years = years_between(NOW, EXPIRY)
    price = sum(l.qty * bs_price(l.kind, SPOT, l.strike, years, l.iv * 1.1) for l in legs)
    assert abs(breakeven_vol(legs, SPOT, NOW, price, 0.20) - 0.22) < 1e-3


def test_a_price_no_volatility_reaches_has_no_breakeven():
    legs = _condor(0.20)
    assert breakeven_vol(legs, SPOT, NOW, -6.0, 0.20) is None, "a 6.00 credit on 5-wide wings"


# --- early assignment --------------------------------------------------------


def test_an_in_the_money_short_call_before_a_larger_dividend_is_flagged_likely():
    ex = NOW.date() + timedelta(days=10)
    leg = RiskLeg("call", 95.0, EXPIRY, -1, 0.2, mid=5.30)  # 0.30 of time value
    out = assignment_risks([leg], SPOT, NOW, [Dividend(ex, 0.50)])
    assert len(out) == 1 and "likely" in out[0] and "0.50" in out[0]


def test_a_dividend_smaller_than_the_time_value_is_only_a_heads_up():
    ex = NOW.date() + timedelta(days=10)
    leg = RiskLeg("call", 95.0, EXPIRY, -1, 0.2, mid=7.00)  # 2.00 of time value
    out = assignment_risks([leg], SPOT, NOW, [Dividend(ex, 0.50)])
    assert len(out) == 1 and "likely" not in out[0]


def test_far_out_of_the_money_calls_long_calls_and_late_dividends_are_quiet():
    soon = [Dividend(NOW.date() + timedelta(days=10), 0.50)]
    assert assignment_risks([RiskLeg("call", 130.0, EXPIRY, -1, 0.2, mid=0.2)], SPOT, NOW, soon) == []
    assert assignment_risks([RiskLeg("call", 95.0, EXPIRY, 1, 0.2, mid=5.3)], SPOT, NOW, soon) == []
    after = [Dividend(EXPIRY + timedelta(days=3), 0.50)]
    assert assignment_risks([RiskLeg("call", 95.0, EXPIRY, -1, 0.2, mid=5.3)], SPOT, NOW, after) == []


def test_a_deep_short_put_with_less_time_value_than_carry_is_flagged():
    deep = RiskLeg("put", 120.0, EXPIRY, -1, 0.2, mid=20.05)  # 0.05 time value vs ~0.59 carry
    assert len(assignment_risks([deep], SPOT, NOW, [])) == 1
    near = RiskLeg("put", 101.0, EXPIRY, -1, 0.2, mid=3.50)
    assert assignment_risks([near], SPOT, NOW, []) == []


# --- held spreads ------------------------------------------------------------


class _Source:
    def now(self):
        return NOW

    async def spot(self, underlying):
        return SPOT

    async def leg_quotes(self, symbols):
        return {
            s: LegQuote(
                symbol=s, strike=0, kind="call", expiry=EXPIRY, bid=1, ask=1.2, mid=1.1, last=1, bid_size=1,
                ask_size=1, delta=None, gamma=None, theta=None, iv=0.2, open_interest=0, tradable=True,
            )
            for s in symbols
        }


class _Dividends:
    async def upcoming(self, symbol):
        return [Dividend(NOW.date() + timedelta(days=5), 2.0)]


def _leg(symbol, kind, strike, qty):
    return SpreadPositionLeg(symbol, kind, strike, qty, 1.0, 1.1, 0, 0, 0, expiry=EXPIRY)


def test_held_spreads_get_greeks_warnings_and_a_book_total():
    condor = SpreadGroup(
        id="c", underlying="X", root="X", expiry=EXPIRY, dte=45, strategy="iron_condor", qty=1,
        legs=[_leg("P85", "put", 85, 1), _leg("P90", "put", 90, -1), _leg("C110", "call", 110, -1), _leg("C115", "call", 115, 1)],
    )
    itm_call = SpreadGroup(
        id="cc", underlying="X", root="X", expiry=EXPIRY, dte=45, strategy="covered_call", qty=1,
        legs=[_leg("C95", "call", 95, -1)], shares=100,
    )
    risks, totals = asyncio.run(spread_risks(_Source(), [condor, itm_call], _Dividends()))
    assert risks["c"]["greeks"]["vega"] < 0
    assert risks["cc"]["warnings"], "an in-the-money short call ahead of a 2.00 dividend"
    assert totals["structures"] == 2 and totals["vega"] == round(
        risks["c"]["greeks"]["vega"] + risks["cc"]["greeks"]["vega"], 2
    )


def test_the_collateral_of_each_structure_and_of_the_book():
    condor = SpreadGroup(
        id="c", underlying="X", root="X", expiry=EXPIRY, dte=45, strategy="iron_condor", qty=2, net_entry=-1.5,
        legs=[_leg("P85", "put", 85, 2), _leg("P90", "put", 90, -2), _leg("C110", "call", 110, -2), _leg("C115", "call", 115, 2)],
    )
    risks, totals = asyncio.run(spread_risks(_Source(), [condor], None))
    assert risks["c"]["collateral"] == (5 - 1.5) * 100 * 2
    assert totals["collateral"] == 700.0


def test_no_spreads_is_a_zero_total_not_a_missing_one():
    _, totals = asyncio.run(spread_risks(_Source(), [], None))
    assert totals["collateral"] == 0.0 and totals["of"] == 0


def test_a_leg_without_iv_is_read_from_its_mid_and_a_deep_one_moves_one_for_one():
    years = years_between(NOW, EXPIRY)
    mid = bs_price("call", SPOT, 100.0, years, 0.30)
    from_mid = position_greeks([RiskLeg("call", 100.0, EXPIRY, -1, None, mid=mid)], SPOT, NOW)
    with_iv = position_greeks([RiskLeg("call", 100.0, EXPIRY, -1, 0.30)], SPOT, NOW)
    assert abs(from_mid.vega - with_iv.vega) < 0.05
    deep = position_greeks([RiskLeg("call", 60.0, EXPIRY, -1, None, mid=40.0)], SPOT, NOW)
    assert deep is not None and deep.delta == -100 and deep.vega == 0


class _Action:
    def __init__(self, symbol, ex, rate):
        self.symbol, self.ex_date, self.rate = symbol, ex, rate


class _CorporateActions:
    def __init__(self, actions):
        self.actions = actions

    def get_corporate_actions(self, request):
        return type("Set", (), {"data": {"cash_dividends": self.actions}})()


def test_the_dividend_calendar_reads_declared_cash_dividends_inside_the_window():
    from app.options.position_risk import DividendCalendar

    today = datetime.now(timezone.utc).date()
    clients = type("C", (), {})()
    clients.corporate_actions = _CorporateActions(
        [
            _Action("XOM", today + timedelta(days=40), 1.03),
            _Action("xom", today + timedelta(days=130), 1.03),  # past the window
            _Action("ZERO", today + timedelta(days=5), 0.0),
        ]
    )
    cal = DividendCalendar(clients)
    got = asyncio.run(cal.upcoming("XOM"))
    assert [(d.ex_date, d.cash) for d in got] == [(today + timedelta(days=40), 1.03)]
    assert asyncio.run(cal.upcoming("ZERO")) == []


# --- pin risk ----------------------------------------------------------------


def test_a_short_strike_the_stock_sits_on_at_expiry_is_flagged():
    from app.options.position_risk import pin_risks

    today = NOW.date()
    on_it = RiskLeg("call", 100.0, today, -1, 0.20)
    out = pin_risks([on_it], 100.3, NOW)
    assert len(out) == 1 and "Pin risk" in out[0] and "0.30 above" in out[0]


def test_pin_risk_needs_expiry_day_a_short_leg_and_closeness():
    from app.options.position_risk import pin_risks

    today = NOW.date()
    assert pin_risks([RiskLeg("call", 100.0, EXPIRY, -1, 0.2)], 100.0, NOW) == [], "not expiring today"
    assert pin_risks([RiskLeg("call", 100.0, today, 1, 0.2)], 100.0, NOW) == [], "a long leg is the holder's choice"
    assert pin_risks([RiskLeg("put", 90.0, today, -1, 0.2)], 100.0, NOW) == [], "ten dollars away"


# --- skewed delta -------------------------------------------------------------


def _skew_rows(slope: float = -0.002):
    from app.options.chain import StrikeRow

    rows = []
    for k in range(80, 121, 5):
        iv = 0.20 + slope * (k - 100)
        quote = lambda kind: LegQuote(  # noqa: E731
            symbol=f"{kind}{k}", strike=k, kind=kind, expiry=EXPIRY, bid=1, ask=1.1, mid=1.05, last=1,
            bid_size=1, ask_size=1, delta=None, gamma=None, theta=None, iv=iv, open_interest=1, tradable=True,
        )
        rows.append(StrikeRow(strike=float(k), call=quote("call"), put=quote("put")))
    return rows


def test_the_smile_slope_is_read_across_the_neighbouring_strikes():
    from app.options.position_risk import smile_slope

    rows = _skew_rows(-0.002)
    assert abs(smile_slope(rows, 92.5, SPOT) - (-0.002)) < 1e-9
    assert abs(smile_slope(rows, 120.0, SPOT) - (-0.002)) < 1e-9, "one-sided at the edge"
    assert smile_slope(rows[:1], 80.0, SPOT) is None


def test_under_put_skew_a_short_condor_leans_further_short_than_the_flat_delta():
    from app.options.position_risk import skew_delta, smile_slope

    rows = _skew_rows(-0.002)
    legs = _condor(0.20)
    slopes = {(l.kind, l.strike, l.expiry): smile_slope(rows, l.strike, SPOT) for l in legs}
    flat = position_greeks(legs, SPOT, NOW).delta
    skewed = skew_delta(legs, SPOT, NOW, slopes)
    assert skewed < flat, "falling IV as the stock rises costs the short vega on the way up"
    assert skew_delta(legs, SPOT, NOW, {}) == flat, "no smile, no correction"
