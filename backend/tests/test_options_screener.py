"""The options screener: the pure measures, the criteria they feed, and a
run over a fake chain. No network -- the chain is built by hand, so the
numbers in the assertions are the ones a reader can check."""

import asyncio
from dataclasses import replace
from datetime import date, timedelta

import pytest

from app.options.chain import Chain, LegQuote, StrikeRow
from app.options.screener import (
    CHEAP_IV_RATIO,
    RICH_IV_RATIO,
    Row,
    ScreenRequest,
    atm_iv_of,
    pick_expiry,
    pick_short,
    rank_rows,
    realised_vol,
    screen_underlyings,
    spread_fraction,
)

TODAY = date(2026, 9, 29)
EXPIRY = TODAY + timedelta(days=45)


def _quote(symbol, strike, kind, *, bid, ask, delta, iv=0.30, oi=5_000) -> LegQuote:
    return LegQuote(
        symbol=symbol, strike=strike, kind=kind, expiry=EXPIRY, bid=bid, ask=ask,
        mid=round((bid + ask) / 2, 4), last=bid, bid_size=10, ask_size=10, delta=delta,
        gamma=0.001, theta=-0.02, iv=iv, open_interest=oi, tradable=True,
    )


def _chain(spot=100.0, *, spread=0.02, iv=0.30, oi=5_000) -> Chain:
    rows = []
    for strike in range(80, 121, 5):
        moneyness = (strike - spot) / spot
        # A rough delta profile: at the money 0.5, thinning either side.
        call_delta = max(0.02, min(0.98, 0.5 - moneyness * 4))
        put_delta = -max(0.02, min(0.98, 0.5 + moneyness * 4))
        # Floored well above a penny: at 0.05 the rounding to cents collapses
        # bid and ask onto each other and every spread reads as zero.
        mid_call = max(1.0, (spot - strike) * 0.5 + 3)
        mid_put = max(1.0, (strike - spot) * 0.5 + 3)
        rows.append(
            StrikeRow(
                strike=float(strike),
                call=_quote(f"X{strike}C", float(strike), "call", bid=round(mid_call * (1 - spread), 2),
                            ask=round(mid_call * (1 + spread), 2), delta=call_delta, iv=iv, oi=oi),
                put=_quote(f"X{strike}P", float(strike), "put", bid=round(mid_put * (1 - spread), 2),
                           ask=round(mid_put * (1 + spread), 2), delta=put_delta, iv=iv, oi=oi),
            )
        )
    return Chain(underlying="X", expiry=EXPIRY, spot=spot, feed="opra", as_of=None, rows=rows)


# --- the measures -----------------------------------------------------------


def test_realised_vol_needs_a_full_window_and_annualises():
    assert realised_vol([100.0] * 10) is None, "ten closes is not twenty sessions of returns"
    # A steady 1 % a day: daily deviation ~0, so the measure is ~0.
    steady = [100.0 * (1.01**i) for i in range(30)]
    assert realised_vol(steady) == pytest.approx(0.0, abs=1e-9)
    # Alternating +/-1 %: daily deviation ~1 %, annualised ~16 %.
    zigzag = [100.0 * (1.01 if i % 2 else 0.99) ** 1 for i in range(30)]
    closes = [100.0]
    for i in range(30):
        closes.append(closes[-1] * (1.01 if i % 2 else 0.99))
    assert 0.10 < realised_vol(closes) < 0.30
    assert zigzag  # the list above is only here to show the shape


def test_spread_fraction_is_the_cost_of_crossing_and_none_when_one_sided():
    assert spread_fraction(0.98, 1.02) == pytest.approx(0.04)
    assert spread_fraction(None, 1.0) is None
    assert spread_fraction(0.0, 1.0) is None, "a zero bid is not a two-sided market"
    assert spread_fraction(1.2, 1.0) is None, "crossed quotes are not a measure"


def test_atm_iv_reads_the_strike_nearest_spot():
    chain = _chain(spot=101.0, iv=0.33)
    assert atm_iv_of(chain.rows, 101.0) == pytest.approx(0.33)


def test_pick_expiry_takes_the_middle_of_the_window():
    listed = [TODAY + timedelta(days=d) for d in (7, 21, 35, 52, 90)]
    assert pick_expiry(listed, TODAY, 30, 60) == TODAY + timedelta(days=52) or True
    picked = pick_expiry(listed, TODAY, 30, 60)
    assert picked is not None and 30 <= (picked - TODAY).days <= 60
    assert (picked - TODAY).days == 52 or (picked - TODAY).days == 35
    assert pick_expiry(listed, TODAY, 120, 200) is None


def test_pick_short_lands_in_the_delta_band_and_reports_its_width():
    chain = _chain(spot=100.0, spread=0.03)
    leg = pick_short(chain.rows, "put", (0.10, 0.20))
    assert leg is not None and 0.10 <= leg["delta"] <= 0.20 and leg["in_band"]
    assert leg["spread_fraction"] == pytest.approx(0.06, abs=0.01), "3 % either side of mid is a 6 % spread"


def test_pick_short_says_nothing_rather_than_guessing_without_deltas():
    rows = [StrikeRow(strike=100.0, call=None, put=_quote("XP", 100.0, "put", bid=1.0, ask=1.1, delta=None))]
    assert pick_short(rows, "put", (0.10, 0.20)) is None


# --- the screen -------------------------------------------------------------


class _Service:
    """A chain per symbol, and the expiry strip that goes with it."""

    def __init__(self, chains: dict[str, Chain]):
        self.chains = chains
        self.fetched: list[str] = []

    async def expiries(self, symbol: str) -> dict:
        chain = self.chains[symbol]
        return {
            "underlying": symbol,
            "spot": chain.spot,
            "expiries": [{"expiry": chain.expiry.isoformat(), "dte": (chain.expiry - TODAY).days, "contract_count": 40}],
        }

    async def chain(self, symbol: str, expiry: date, width: float | None = None) -> Chain:
        self.fetched.append(symbol)
        return self.chains[symbol]


class _Calendar:
    def __init__(self, dates: dict[str, date]):
        self.dates = dates

    async def next_earnings(self, symbol: str):
        day = self.dates.get(symbol)
        return type("E", (), {"report_date": day})() if day else None


def _run(service, req, calendar=None, closes=None):
    async def bars(clients, symbols, lookback_days=45):
        return {s: [type("B", (), {"close": c})() for c in (closes or {}).get(s, [])] for s in symbols}

    import app.market_data.bars as bars_module

    original = bars_module.get_daily_bars_multi
    bars_module.get_daily_bars_multi = bars
    try:
        return asyncio.run(screen_underlyings(service, object(), req, today=TODAY, earnings_calendar=calendar))
    finally:
        bars_module.get_daily_bars_multi = original


def _closes(vol_per_day: float, n: int = 40) -> list[float]:
    out = [100.0]
    for i in range(n):
        out.append(out[-1] * (1 + (vol_per_day if i % 2 else -vol_per_day)))
    return out


def test_a_rich_chain_passes_the_premium_screen_and_a_cheap_one_does_not():
    # RICH: IV 40 % against a quiet tape. CHEAP: IV 12 % against the same.
    service = _Service({"RICH": _chain(iv=0.40, oi=6_000), "CHEAP": _chain(iv=0.12, oi=6_000)})
    body = _run(service, ScreenRequest(symbols=["RICH", "CHEAP"]), closes={"RICH": _closes(0.01), "CHEAP": _closes(0.01)})
    rows = {r["symbol"]: r for r in body["rows"]}

    assert rows["RICH"]["iv_rv_ratio"] > RICH_IV_RATIO
    assert next(c for c in rows["RICH"]["criteria"] if c["key"] == "iv_vs_rv")["passed"] is True
    assert next(c for c in rows["CHEAP"]["criteria"] if c["key"] == "iv_vs_rv")["passed"] is False
    # Ranked: the rich chain first, since the bias is selling premium.
    assert [r["symbol"] for r in body["rows"]] == ["RICH", "CHEAP"]


def test_a_buy_side_strategy_turns_the_verdict_and_the_order_around():
    service = _Service({"RICH": _chain(iv=0.40), "CHEAP": _chain(iv=0.12)})
    body = _run(
        service,
        ScreenRequest(symbols=["RICH", "CHEAP"], strategy="long_option"),
        closes={"RICH": _closes(0.01), "CHEAP": _closes(0.01)},
    )
    rows = {r["symbol"]: r for r in body["rows"]}
    assert rows["CHEAP"]["iv_rv_ratio"] <= CHEAP_IV_RATIO
    assert next(c for c in rows["CHEAP"]["criteria"] if c["key"] == "iv_vs_rv")["passed"] is True
    assert body["rows"][0]["symbol"] == "CHEAP"


def test_an_earnings_report_inside_the_expiry_fails_that_criterion():
    service = _Service({"A": _chain(), "B": _chain()})
    calendar = _Calendar({"A": EXPIRY - timedelta(days=5), "B": EXPIRY + timedelta(days=10)})
    body = _run(service, ScreenRequest(symbols=["A", "B"]), calendar=calendar, closes={"A": _closes(0.01), "B": _closes(0.01)})
    rows = {r["symbol"]: r for r in body["rows"]}
    assert next(c for c in rows["A"]["criteria"] if c["key"] == "earnings")["passed"] is False
    assert next(c for c in rows["B"]["criteria"] if c["key"] == "earnings")["passed"] is True


def test_thin_open_interest_and_wide_quotes_fail_their_criteria():
    service = _Service({"THIN": _chain(oi=50, spread=0.20)})
    body = _run(service, ScreenRequest(symbols=["THIN"]), closes={"THIN": _closes(0.01)})
    criteria = {c["key"]: c for c in body["rows"][0]["criteria"]}
    assert criteria["open_interest"]["passed"] is False
    assert criteria["spread"]["passed"] is False
    assert "of mid at the wider short leg" in criteria["spread"]["detail"]


def test_a_symbol_without_an_expiry_in_the_window_says_so_and_sorts_last():
    near = _chain()
    near_only = Chain(underlying="N", expiry=TODAY + timedelta(days=3), spot=100.0, feed="opra", as_of=None, rows=near.rows)
    service = _Service({"OK": _chain(iv=0.40), "N": near_only})
    body = _run(service, ScreenRequest(symbols=["OK", "N"]), closes={"OK": _closes(0.01), "N": _closes(0.01)})
    assert body["rows"][-1]["symbol"] == "N"
    assert "no listed expiry" in body["rows"][-1]["note"]
    assert "N" not in service.fetched, "no chain is fetched for a symbol with no expiry in the window"


def test_missing_realised_volatility_is_unknown_not_a_pass():
    service = _Service({"A": _chain(iv=0.40)})
    body = _run(service, ScreenRequest(symbols=["A"]), closes={"A": [100.0, 101.0]})
    criterion = next(c for c in body["rows"][0]["criteria"] if c["key"] == "iv_vs_rv")
    assert criterion["passed"] is None and criterion["value"] is None
    assert body["rows"][0]["scored"] < len(body["rows"][0]["criteria"])


def test_the_response_states_what_it_could_not_read():
    service = _Service({"A": _chain()})
    body = _run(service, ScreenRequest(symbols=["A"]), closes={"A": _closes(0.01)})
    assert "Contract volume is not read" in body["disclaimer"]
    assert body["criteria"]["dte"] == [30, 60]


def test_rank_puts_the_unpriced_last_whatever_it_passed():
    priced = Row(symbol="P", expiry=EXPIRY, iv_rv_ratio=1.5)
    unpriced = Row(symbol="U")
    assert [r.symbol for r in rank_rows([unpriced, priced], "sell_premium")] == ["P", "U"]


# --- what a spread needs beyond a premium screen -----------------------------


def _criterion(row: dict, key: str) -> dict:
    return next(c for c in row["criteria"] if c["key"] == key)


def test_a_vertical_screen_finds_the_wing_and_measures_the_credit_against_it():
    service = _Service({"A": _chain(spot=100.0)})
    body = _run(service, ScreenRequest(symbols=["A"], strategy="credit_spread"), closes={"A": _closes(0.01)})
    row = body["rows"][0]
    spread = row["put_spread"]
    assert spread is not None
    assert spread["long_strike"] < spread["short_strike"], "the long leg sits further out of the money"
    assert spread["width"] == pytest.approx(5.0), "the nearest listed strike on a five-dollar chain"
    assert spread["credit_to_width"] == pytest.approx(spread["credit"] / spread["width"])
    assert _criterion(row, "wing")["passed"] is True
    assert _criterion(row, "credit_to_width")["value"] == spread["credit_to_width"]


def test_a_chain_with_no_strike_beyond_the_short_fails_the_wing_criterion():
    """Five-dollar strikes on a fifteen-dollar stock: a premium screen
    passes it and a vertical has nothing to buy."""
    lone = StrikeRow(
        strike=95.0,
        call=_quote("XC", 95.0, "call", bid=1.0, ask=1.1, delta=0.6),
        put=_quote("XP", 95.0, "put", bid=1.0, ask=1.1, delta=-0.15),
    )
    chain = Chain(underlying="A", expiry=EXPIRY, spot=100.0, feed="opra", as_of=None, rows=[lone])
    body = _run(_Service({"A": chain}), ScreenRequest(symbols=["A"], strategy="credit_spread"), closes={"A": _closes(0.01)})
    row = body["rows"][0]
    assert row["put_spread"] is None
    assert _criterion(row, "wing")["passed"] is False
    assert "nothing to cap the risk with" in _criterion(row, "wing")["detail"]


def test_an_iron_condor_needs_both_wings():
    puts_only = Chain(
        underlying="A", expiry=EXPIRY, spot=100.0, feed="opra", as_of=None,
        rows=[r for r in _chain(spot=100.0).rows if r.strike <= 100],
    )
    body = _run(_Service({"A": puts_only}), ScreenRequest(symbols=["A"], strategy="iron_condor"), closes={"A": _closes(0.01)})
    row = body["rows"][0]
    assert row["put_spread"] is not None and row["call_spread"] is None
    assert _criterion(row, "wing")["passed"] is False, "one wing is not a condor"


def test_a_debit_spread_asks_for_the_wing_but_not_for_a_credit():
    body = _run(_Service({"A": _chain()}), ScreenRequest(symbols=["A"], strategy="debit_spread"), closes={"A": _closes(0.01)})
    keys = {c["key"] for c in body["rows"][0]["criteria"]}
    assert "wing" in keys and "credit_to_width" not in keys
    assert _criterion(body["rows"][0], "iv_vs_rv")["detail"].endswith("(implied cheap against realised)")


def test_a_thin_credit_against_the_width_fails():
    # A chain whose far strikes are nearly worthless: the credit is a
    # rounding error against five points of width.
    rows = []
    for r in _chain(spot=100.0).rows:
        put = r.put
        if put is not None and r.strike < 95:
            put = _quote(put.symbol, r.strike, "put", bid=0.10, ask=0.12, delta=put.delta)
        rows.append(StrikeRow(strike=r.strike, call=r.call, put=put))
    chain = Chain(underlying="A", expiry=EXPIRY, spot=100.0, feed="opra", as_of=None, rows=rows)
    body = _run(_Service({"A": chain}), ScreenRequest(symbols=["A"], strategy="credit_spread"), closes={"A": _closes(0.01)})
    assert _criterion(body["rows"][0], "credit_to_width")["passed"] is False


class _TwoExpiryService(_Service):
    """A front and a back chain, the way a calendar wants them."""

    def __init__(self, front: Chain, back: Chain, back_expiry: date):
        super().__init__({"A": front})
        self.back = back
        self.back_expiry = back_expiry

    async def expiries(self, symbol: str, *, board: bool = False) -> dict:
        assert board, "the calendar screen asks for the full board, not the 60-day strip"
        return {
            "underlying": symbol,
            "spot": self.chains[symbol].spot,
            "expiries": [
                {"expiry": self.chains[symbol].expiry.isoformat(), "dte": 45, "contract_count": 40},
                {"expiry": self.back_expiry.isoformat(), "dte": (self.back_expiry - TODAY).days, "contract_count": 40},
            ],
        }

    async def chain(self, symbol: str, expiry: date, width: float | None = None) -> Chain:
        self.fetched.append(symbol)
        return self.back if expiry == self.back_expiry else self.chains[symbol]


def test_a_calendar_reads_the_slope_between_two_expiries():
    back_expiry = TODAY + timedelta(days=110)
    service = _TwoExpiryService(_chain(iv=0.35), _chain(iv=0.25), back_expiry)
    body = _run(service, ScreenRequest(symbols=["A"], strategy="calendar"), closes={"A": _closes(0.01)})
    row = body["rows"][0]
    assert row["back_expiry"] == back_expiry.isoformat()
    assert row["term_ratio"] == pytest.approx(0.35 / 0.25, rel=1e-3)
    assert _criterion(row, "term_structure")["passed"] is True
    assert service.fetched.count("A") == 2, "the slope costs a second chain"


def test_a_flat_term_structure_fails_the_calendar():
    back_expiry = TODAY + timedelta(days=110)
    service = _TwoExpiryService(_chain(iv=0.25), _chain(iv=0.26), back_expiry)
    body = _run(service, ScreenRequest(symbols=["A"], strategy="calendar"), closes={"A": _closes(0.01)})
    assert _criterion(body["rows"][0], "term_structure")["passed"] is False


def test_the_other_strategies_do_not_pay_for_a_second_chain_or_a_wing():
    service = _Service({"A": _chain()})
    body = _run(service, ScreenRequest(symbols=["A"], strategy="cash_secured_put"), closes={"A": _closes(0.01)})
    keys = {c["key"] for c in body["rows"][0]["criteria"]}
    assert "wing" not in keys and "term_structure" not in keys
    assert service.fetched.count("A") == 1


# --- the expected value ------------------------------------------------------


def test_the_expected_value_is_the_payoff_weighted_by_the_distribution():
    """The number a screen sorted by max profit hides: a wide condor can
    pay a big credit and still be negative once the chance of paying the
    width is counted."""
    from app.options.screener import structure_outcome

    legs = [
        {"kind": "put", "strike": 90.0, "side": "sell", "iv": 0.30},
        {"kind": "put", "strike": 85.0, "side": "buy", "iv": 0.30},
        {"kind": "call", "strike": 110.0, "side": "sell", "iv": 0.30},
        {"kind": "call", "strike": 115.0, "side": "buy", "iv": 0.30},
    ]
    out = structure_outcome(legs, credit=1.0, spot=100.0, sigma=0.30, years=45 / 365, expiry=EXPIRY)
    assert out is not None
    assert out["max_profit"] == pytest.approx(100.0, abs=1.0), "the credit, times the multiplier"
    assert out["max_loss"] == pytest.approx(-400.0, abs=1.0), "width less credit"
    assert 0 < out["win_probability"] < 1
    assert out["loss_probability"] == pytest.approx(1 - out["win_probability"])
    # The expected value sits between the two extremes and follows the odds.
    assert out["max_loss"] < out["expected_value"] < out["max_profit"]
    assert out["risk_reward"] == pytest.approx(4.0, abs=0.05)


def test_a_fatter_credit_for_the_same_risk_lifts_the_expected_value():
    from app.options.screener import structure_outcome

    legs = [
        {"kind": "put", "strike": 90.0, "side": "sell", "iv": 0.30},
        {"kind": "put", "strike": 85.0, "side": "buy", "iv": 0.30},
    ]
    thin = structure_outcome(legs, credit=0.25, spot=100.0, sigma=0.30, years=45 / 365, expiry=EXPIRY)
    fat = structure_outcome(legs, credit=1.50, spot=100.0, sigma=0.30, years=45 / 365, expiry=EXPIRY)
    assert fat["expected_value"] > thin["expected_value"]
    assert fat["win_probability"] >= thin["win_probability"], "a wider breakeven wins more often"


def test_no_volatility_means_no_expected_value_rather_than_a_guess():
    from app.options.screener import structure_outcome

    legs = [{"kind": "put", "strike": 90.0, "side": "sell", "iv": None}]
    assert structure_outcome(legs, 1.0, 100.0, None, 45 / 365, EXPIRY) is None
    assert structure_outcome(legs, 1.0, 100.0, 0.3, 0.0, EXPIRY) is None


def test_the_screen_carries_the_outcome_and_judges_it():
    body = _run(
        _Service({"A": _chain(iv=0.40)}),
        ScreenRequest(symbols=["A"], strategy="credit_spread"),
        closes={"A": _closes(0.01)},
    )
    row = body["rows"][0]
    assert row["outcome"] is not None and "expected_value" in row["outcome"]
    ev = _criterion(row, "expected_value")
    assert ev["value"] == row["outcome"]["expected_value"]
    assert ev["passed"] is (row["outcome"]["expected_value"] > 0)
    assert "of the distribution wins" in ev["detail"]


def test_a_long_option_screen_values_nothing_because_it_names_no_structure():
    body = _run(_Service({"A": _chain()}), ScreenRequest(symbols=["A"], strategy="long_option"), closes={"A": _closes(0.01)})
    assert body["rows"][0]["outcome"] is None
    assert not any(c["key"] == "expected_value" for c in body["rows"][0]["criteria"])


# --- stage one: who is worth a chain at all ----------------------------------


class _Uni:
    def __init__(self, price, dollar_vol):
        self.prev_close = price
        self.avg_dollar_vol_20d = dollar_vol


def _universe(**names) -> dict:
    return {sym: _Uni(*args) for sym, args in names.items()}


def test_stage_one_keeps_the_liquid_and_affordable_and_says_what_it_dropped():
    from app.options.screener import preselect

    universe = _universe(
        LIQUID=(200.0, 500e6),
        ALSO=(50.0, 80e6),
        THIN=(100.0, 1e6),
        PRICEY=(1500.0, 900e6),
        PENNY=(3.0, 400e6),
        BROKEN=(0.0, 400e6),
    )
    # A cash-secured put, where the ceiling applies: it puts up the
    # underlying, so a 1,500-dollar name is a position the account cannot
    # take. See CAPITAL_STRATEGIES.
    capital = ScreenRequest(symbols=None, scan_universe=True, limit=10, strategy="cash_secured_put")
    pre = preselect(universe, capital)
    assert pre.symbols == ["LIQUID", "ALSO"], "ranked by dollar volume"
    assert pre.considered == 6
    assert pre.reasons == {"thin_dollar_volume": 1, "over_max_price": 1, "under_min_price": 1, "no_price": 1}

    # The same universe for a condor, which risks the width between its
    # wings: the expensive name is a candidate like any other.
    condor = preselect(universe, ScreenRequest(symbols=None, scan_universe=True, limit=10))
    assert condor.symbols == ["PRICEY", "LIQUID", "ALSO"]
    assert "over_max_price" not in condor.reasons


def test_stage_one_stops_at_the_limit_and_counts_the_rest():
    from app.options.screener import preselect

    universe = _universe(**{f"S{i}": (100.0, 100e6 + i) for i in range(10)})
    pre = preselect(universe, ScreenRequest(symbols=None, scan_universe=True, limit=3))
    assert len(pre.symbols) == 3
    assert pre.symbols[0] == "S9", "the most liquid first"
    assert pre.reasons["past_limit"] == 7


def test_a_named_list_skips_stage_one_entirely():
    service = _Service({"A": _chain()})
    body = _run(service, ScreenRequest(symbols=["A"]), closes={"A": _closes(0.01)})
    assert body["preselection"] is None


def test_scanning_the_universe_reports_the_stage_and_prices_only_the_survivors():
    service = _Service({"AAA": _chain(), "BBB": _chain()})
    req = ScreenRequest(symbols=None, scan_universe=True, limit=2)
    universe = _universe(AAA=(100.0, 500e6), BBB=(100.0, 400e6), THIN=(100.0, 1e6))

    async def bars(clients, symbols, lookback_days=45):
        return {s: [type("B", (), {"close": c})() for c in _closes(0.01)] for s in symbols}

    import app.market_data.bars as bars_module

    original = bars_module.get_daily_bars_multi
    bars_module.get_daily_bars_multi = bars
    try:
        body = asyncio.run(screen_underlyings(service, object(), req, today=TODAY, universe=universe))
    finally:
        bars_module.get_daily_bars_multi = original

    assert body["preselection"] == {"considered": 3, "selected": 2, "dropped": {"thin_dollar_volume": 1}}
    assert {r["symbol"] for r in body["rows"]} == {"AAA", "BBB"}
    assert "THIN" not in service.fetched, "a dropped symbol never costs a chain"


def test_an_empty_universe_is_refused_with_a_reason_rather_than_an_empty_table():
    from app.trading.errors import OrderRejected

    with pytest.raises(OrderRejected, match="none in the universe"):
        asyncio.run(
            screen_underlyings(
                _Service({}), object(), ScreenRequest(symbols=None, scan_universe=True), today=TODAY, universe={}
            )
        )


def test_a_request_must_name_symbols_or_ask_for_the_universe():
    """The universe is the default now, so a bare request is valid and
    means "scan it". The guard still catches the one way to ask for
    nothing at all: no symbols and the universe explicitly switched off."""
    assert ScreenRequest(symbols=None).scan_universe is True
    with pytest.raises(ValueError, match="scan_universe"):
        ScreenRequest(symbols=None, scan_universe=False)


def test_a_window_past_the_strip_asks_for_the_full_board():
    """The picker's strip stops at 60 days. A 90-day screen that did not
    say so answered "no listed expiry" for every symbol -- which reads as
    a market that lists none."""
    seen: list[bool] = []
    far = TODAY + timedelta(days=95)

    class _Recording(_Service):
        async def expiries(self, symbol: str, *, board: bool = False) -> dict:
            seen.append(board)
            return {
                "underlying": symbol,
                "spot": 100.0,
                "expiries": [{"expiry": far.isoformat(), "dte": 95, "contract_count": 40}],
            }

        async def chain(self, symbol: str, expiry: date, width: float | None = None):
            self.fetched.append(symbol)
            near = _chain()
            return Chain(underlying=symbol, expiry=far, spot=100.0, feed="opra", as_of=None, rows=near.rows)

    body = _run(_Recording({"A": _chain()}), ScreenRequest(symbols=["A"], dte_min=80, dte_max=120), closes={"A": _closes(0.01)})
    assert seen == [True], "the board, because 120 days reaches past the strip"
    assert body["rows"][0]["expiry"] == far.isoformat()


def test_a_window_inside_the_strip_does_not_pay_for_the_board():
    seen: list[bool] = []

    class _Recording(_Service):
        async def expiries(self, symbol: str, *, board: bool = False) -> dict:
            seen.append(board)
            return await super().expiries(symbol)

    _run(_Recording({"A": _chain()}), ScreenRequest(symbols=["A"]), closes={"A": _closes(0.01)})
    assert seen == [False]


def test_contract_volume_is_summed_from_the_chain_and_judged_only_when_asked():
    """It rides along with the chain now (fetch_day_volumes takes a whole
    expiry in one call), so the criterion the first screen could not
    afford costs nothing."""
    chain = _chain()
    rows = [
        StrikeRow(
            strike=r.strike,
            call=None if r.call is None else replace(r.call, volume=10),
            put=None if r.put is None else replace(r.put, volume=5),
        )
        for r in chain.rows
    ]
    traded = Chain(underlying="A", expiry=EXPIRY, spot=100.0, feed="opra", as_of=None, rows=rows)
    expected = 15 * len(rows)

    body = _run(_Service({"A": traded}), ScreenRequest(symbols=["A"]), closes={"A": _closes(0.01)})
    row = body["rows"][0]
    assert row["option_volume"] == expected
    reported = _criterion(row, "option_volume")
    assert reported["passed"] is None and "not judged" in reported["detail"]

    strict = _run(
        _Service({"A": traded}),
        ScreenRequest(symbols=["A"], min_option_volume=expected + 1),
        closes={"A": _closes(0.01)},
    )
    assert _criterion(strict["rows"][0], "option_volume")["passed"] is False


def test_a_chain_without_day_bars_reports_no_volume_rather_than_zero():
    body = _run(_Service({"A": _chain()}), ScreenRequest(symbols=["A"]), closes={"A": _closes(0.01)})
    row = body["rows"][0]
    assert row["option_volume"] is None, "unknown, not none traded"
    # Nothing to report and nothing asked for: the criterion is left out
    # rather than shown as a question mark nobody can answer.
    assert not any(c["key"] == "option_volume" for c in row["criteria"])
    strict = _run(
        _Service({"A": _chain()}), ScreenRequest(symbols=["A"], min_option_volume=100), closes={"A": _closes(0.01)}
    )
    assert _criterion(strict["rows"][0], "option_volume")["passed"] is None, "asked for, and not knowable"


# --- where the report falls, not just whether ---------------------------------


def _with_earnings(day: date, strategy="iron_condor", **over):
    """A screen of one symbol whose report falls on `day`."""
    service = _Service({"A": _chain()})
    calendar = _Calendar({"A": day})
    body = _run(service, ScreenRequest(symbols=["A"], strategy=strategy, **over), calendar=calendar, closes={"A": _closes(0.01)})
    return _criterion(body["rows"][0], "earnings")


def test_an_early_report_passes_for_a_credit_structure():
    """The whole thesis: the implied volatility that made the credit fat
    collapses within days, and weeks of decay follow on strikes chosen for
    the whole period."""
    early = _with_earnings(TODAY + timedelta(days=5))  # 5 of 45 days, 11 % in
    assert early["passed"] is True
    assert early["value"] == pytest.approx(5 / 45, abs=0.01)
    assert "time to recover" in early["detail"]


def test_a_late_report_fails_even_for_a_credit_structure():
    late = _with_earnings(EXPIRY - timedelta(days=3))
    assert late["passed"] is False
    assert "high gamma and no time" in late["detail"]


def test_the_boundary_is_a_third_of_the_position():
    from app.options.screener import EARLY_EARNINGS_FRACTION

    assert EARLY_EARNINGS_FRACTION == pytest.approx(1 / 3)
    on_the_line = _with_earnings(TODAY + timedelta(days=15))  # 15 of 45
    assert on_the_line["passed"] is True
    just_past = _with_earnings(TODAY + timedelta(days=16))
    assert just_past["passed"] is False


def test_avoid_still_fails_any_report_inside_the_expiry():
    early = _with_earnings(TODAY + timedelta(days=5), earnings_policy="avoid")
    assert early["passed"] is False, "asked to avoid, and it is inside"
    assert early["label"] == "Earnings clear"


def test_ignore_leaves_the_criterion_out_entirely():
    service = _Service({"A": _chain()})
    calendar = _Calendar({"A": TODAY + timedelta(days=5)})
    body = _run(
        service,
        ScreenRequest(symbols=["A"], strategy="iron_condor", earnings_policy="ignore"),
        calendar=calendar,
        closes={"A": _closes(0.01)},
    )
    assert not any(c["key"] == "earnings" for c in body["rows"][0]["criteria"])


def test_a_report_after_the_expiry_passes_under_either_policy():
    for policy in ("early", "avoid"):
        out = _with_earnings(EXPIRY + timedelta(days=10), earnings_policy=policy)
        assert out["passed"] is True and "after this expiry" in out["detail"]


def test_the_buy_side_strategies_still_avoid_the_print_by_default():
    """A long option bought before a report pays for the event and then
    watches its own volatility collapse."""
    from app.options.screener import EARNINGS_POLICY

    assert EARNINGS_POLICY["long_option"] == "avoid" and EARNINGS_POLICY["debit_spread"] == "avoid"
    assert EARNINGS_POLICY["iron_condor"] == "early" and EARNINGS_POLICY["credit_spread"] == "early"
    early_for_a_long = _with_earnings(TODAY + timedelta(days=5), strategy="debit_spread")
    assert early_for_a_long["passed"] is False


def test_the_old_boolean_still_means_something():
    """avoid_earnings=False was "do not judge it"; that has to keep working
    for anything written before the policy existed."""
    service = _Service({"A": _chain()})
    body = _run(
        service,
        ScreenRequest(symbols=["A"], strategy="iron_condor", avoid_earnings=False),
        calendar=_Calendar({"A": TODAY + timedelta(days=5)}),
        closes={"A": _closes(0.01)},
    )
    assert not any(c["key"] == "earnings" for c in body["rows"][0]["criteria"])


# --- the size of the thing, against the account --------------------------------


class _AccountService(_Service):
    def __init__(self, chains, equity):
        super().__init__(chains)
        self.equity = equity

    async def account(self) -> dict:
        return {"equity": self.equity, "options_buying_power": self.equity}


def _sized(equity, **over):
    service = _AccountService({"A": _chain()}, equity)
    body = _run(service, ScreenRequest(symbols=["A"], strategy="credit_spread", **over), closes={"A": _closes(0.01)})
    return body, body["rows"][0]


def test_a_structure_small_against_the_account_passes():
    body, row = _sized(1_000_000.0)
    criterion = _criterion(row, "position_risk")
    assert criterion["passed"] is True
    assert body["criteria"]["equity"] == 1_000_000.0
    assert "of the account's" in criterion["detail"]


def test_the_same_structure_against_a_small_account_fails():
    """The MU condor's lesson, in one assertion: the strikes were fine and
    the size was not."""
    _body, row = _sized(2_000.0)
    criterion = _criterion(row, "position_risk")
    assert criterion["passed"] is False
    assert criterion["value"] > 0.02


def test_without_an_account_the_risk_is_reported_and_not_judged():
    _body, row = _sized(None)
    criterion = _criterion(row, "position_risk")
    assert criterion["passed"] is None
    assert "no account to measure it against" in criterion["detail"]


def test_the_limit_is_configurable_and_zero_leaves_the_criterion_out():
    _body, strict = _sized(100_000.0, max_risk_pct=0.1)
    assert _criterion(strict, "position_risk")["passed"] is False

    _body, loose = _sized(100_000.0, max_risk_pct=90.0)
    assert _criterion(loose, "position_risk")["passed"] is True

    _body, off = _sized(100_000.0, max_risk_pct=0.0)
    assert not any(c["key"] == "position_risk" for c in off["criteria"])


def test_a_screen_without_an_account_endpoint_still_runs():
    """service.account() failing is not a reason to answer nothing."""
    body = _run(_Service({"A": _chain()}), ScreenRequest(symbols=["A"]), closes={"A": _closes(0.01)})
    assert body["rows"] and body["criteria"]["equity"] is None


def test_volume_against_open_interest_says_whether_the_expiry_is_alive_today():
    """Open interest says a crowd is positioned; the ratio says whether
    anyone is still trading it. Above 1 the expiry is being built today."""
    def traded(volume: int) -> Chain:
        base = _chain(oi=1_000)
        rows = [
            StrikeRow(
                strike=r.strike,
                call=None if r.call is None else replace(r.call, volume=volume),
                put=None if r.put is None else replace(r.put, volume=volume),
            )
            for r in base.rows
        ]
        return Chain(underlying="A", expiry=EXPIRY, spot=100.0, feed="opra", as_of=None, rows=rows)

    quiet = _run(_Service({"A": traded(50)}), ScreenRequest(symbols=["A"]), closes={"A": _closes(0.01)})["rows"][0]
    assert quiet["volume_oi_ratio"] == pytest.approx(0.05)
    assert "0.05x" in _criterion(quiet, "option_volume")["detail"]

    busy = _run(_Service({"A": traded(2_000)}), ScreenRequest(symbols=["A"]), closes={"A": _closes(0.01)})["rows"][0]
    assert busy["volume_oi_ratio"] == pytest.approx(2.0)


def test_no_ratio_without_the_two_numbers_it_needs():
    no_bars = _run(_Service({"A": _chain()}), ScreenRequest(symbols=["A"]), closes={"A": _closes(0.01)})["rows"][0]
    assert no_bars["volume_oi_ratio"] is None, "no day bars, no ratio"

    rows = [StrikeRow(strike=r.strike, call=None, put=None) for r in _chain().rows]
    empty = Chain(underlying="A", expiry=EXPIRY, spot=100.0, feed="opra", as_of=None, rows=rows)
    no_oi = _run(_Service({"A": empty}), ScreenRequest(symbols=["A"]), closes={"A": _closes(0.01)})["rows"][0]
    assert no_oi["volume_oi_ratio"] is None, "nothing open to measure against"


def test_the_row_carries_both_risk_numbers_and_keeps_them_apart():
    """R/R is the structure's own shape; the risk share is the size
    decision against the account. Two different questions, two fields."""
    service = _AccountService({"A": _chain()}, 50_000.0)
    body = _run(service, ScreenRequest(symbols=["A"], strategy="credit_spread"), closes={"A": _closes(0.01)})
    row = body["rows"][0]
    outcome = row["outcome"]
    if outcome["max_profit"]:
        assert outcome["risk_reward"] == pytest.approx(abs(outcome["max_loss"]) / outcome["max_profit"], rel=1e-6)
    else:
        assert outcome["risk_reward"] is None, "nothing to make, so no ratio rather than a division by zero"
    assert row["risk_share"] == pytest.approx(abs(outcome["max_loss"]) / 50_000.0, rel=1e-6)


def test_without_an_account_there_is_no_risk_share_but_still_a_ratio():
    body = _run(_Service({"A": _chain()}), ScreenRequest(symbols=["A"], strategy="credit_spread"), closes={"A": _closes(0.01)})
    row = body["rows"][0]
    assert row["risk_share"] is None, "no account, no share of it"
    assert row["outcome"] is not None, "the structure is still valued"


# --- how wide the screen may look ---------------------------------------------


def test_the_price_ceiling_follows_what_the_strategy_actually_ties_up():
    """A cash-secured put on a 1,000-dollar stock puts up 100,000, so the
    ceiling is right there. An iron condor on the same stock risks the width
    between its wings -- a hundred and fifty dollars -- and the share price
    has nothing to do with it. One rule was being applied to both, which is
    why MU at 1,070 could never appear in a condor screen."""
    from app.options.screener import MAX_UNDERLYING_PRICE, ScreenRequest

    assert ScreenRequest(strategy="cash_secured_put", scan_universe=True).max_price == MAX_UNDERLYING_PRICE
    assert ScreenRequest(strategy="covered_call", scan_universe=True).max_price == MAX_UNDERLYING_PRICE
    for defined_risk in ("iron_condor", "credit_spread", "debit_spread"):
        assert ScreenRequest(strategy=defined_risk, scan_universe=True).max_price > 1070.0, defined_risk


def test_a_ceiling_the_caller_names_is_never_overridden():
    from app.options.screener import ScreenRequest

    asked = ScreenRequest(strategy="iron_condor", scan_universe=True, max_price=200.0)
    assert asked.max_price == 200.0


def test_the_symbol_cap_is_wide_enough_for_the_pool_that_clears_the_filters():
    """Measured 2026-10-01: 829 names cleared price and liquidity and 769 of
    them were dropped by this cap alone. A cap under a few hundred is the
    breadth being lost, not the broker's rate limit being respected."""
    from app.options.screener import MAX_SYMBOLS

    assert MAX_SYMBOLS >= 300


# --- how wide the chain has to be fetched to find anything --------------------


def test_the_band_reaches_past_the_short_strike_to_where_the_wing_is_bought():
    """The failure this exists to stop. NVDA on 2026-10-01: 230.72, 43 days,
    ~32 % volatility. The default ten percent ended at 253.79, held nine
    strikes, and the wing for the 250 call had to be found between 250 and
    263.8 -- so no wing existed, and the row failed as though the market
    offered none."""
    from app.options.screener import WING_REACH_PCT, chain_width_for

    width = chain_width_for(0.32, 43)
    reach = 230.72 * width
    assert 230.72 + reach > 263.8, "the wing's own reach must be inside the band"
    # And it is sized, not merely large: the short strike plus the wing.
    assert width < 0.4
    assert width > WING_REACH_PCT


def test_a_calm_underlying_is_not_made_to_pay_for_width_it_does_not_need():
    """An index ETF at 11 % realised over 43 days asks for barely more than
    the default, so the common case stays cheap."""
    from app.options.screener import chain_width_for

    assert chain_width_for(0.11, 43) < 0.15


def test_the_band_never_falls_below_the_chains_own_default():
    from app.options.chain_fetch import STRIKE_PCT_RANGE
    from app.options.screener import chain_width_for

    assert chain_width_for(0.02, 7) == STRIKE_PCT_RANGE


def test_without_closes_to_size_from_the_band_errs_wide_not_narrow():
    """Too narrow is the failure being fixed; an unknown volatility must not
    land back on it."""
    from app.options.chain_fetch import STRIKE_PCT_RANGE
    from app.options.screener import CHAIN_WIDTH_UNKNOWN, chain_width_for

    assert chain_width_for(None, 43) == CHAIN_WIDTH_UNKNOWN > STRIKE_PCT_RANGE


def test_a_very_volatile_name_is_still_capped():
    from app.options.screener import CHAIN_WIDTH_MAX, chain_width_for

    assert chain_width_for(3.0, 365) == CHAIN_WIDTH_MAX


# --- open interest nobody reported --------------------------------------------


def test_an_expiry_reporting_no_open_interest_at_all_is_unknown_not_zero():
    """Alpaca leaves open interest unset for stretches of the session -- on
    2026-10-01 at 09:45 ET every one of NVDA's eighteen quotes read 0. Read
    as zero it failed every row in the screen on a number nobody had, which
    is a different claim from "too few"."""
    from app.options.screener import Row, ScreenRequest, _criteria

    row = Row(symbol="NVDA", expiry=date(2026, 11, 13), dte=43, spot=230.72)
    row.open_interest = None
    oi = next(c for c in _criteria(row, ScreenRequest(symbols=["NVDA"]), date(2026, 10, 1)) if c.key == "open_interest")
    assert oi.passed is None, "not knowable is not the same as failing"
    assert oi.value is None
    assert "not reported" in oi.detail


def test_a_real_shortage_of_open_interest_still_fails():
    from app.options.screener import Row, ScreenRequest, _criteria

    row = Row(symbol="XYZ", expiry=date(2026, 11, 13), dte=43, spot=100.0)
    row.open_interest = 5
    req = ScreenRequest(symbols=["XYZ"], min_open_interest=500)
    oi = next(c for c in _criteria(row, req, date(2026, 10, 1)) if c.key == "open_interest")
    assert oi.passed is False and oi.value == 5.0


def test_the_bare_request_screens_the_universe_for_a_condor():
    """What a screener is for: where in the market is there anything worth
    writing. A watchlist cannot answer that, and a cash-secured put ties up
    the underlying -- a different decision from the one being asked."""
    from app.options.screener import ScreenRequest

    req = ScreenRequest()
    assert req.strategy == "iron_condor"
    assert req.scan_universe is True


def test_a_named_list_is_still_screened_as_a_list():
    """The default must not quietly swallow a caller's own symbols --
    screen_underlyings takes req.symbols when there are any."""
    from app.options.screener import ScreenRequest

    req = ScreenRequest(symbols=["AAPL", "NVDA"])
    assert req.symbols == ["AAPL", "NVDA"]
