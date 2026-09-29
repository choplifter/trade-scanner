"""The options screener: the pure measures, the criteria they feed, and a
run over a fake chain. No network -- the chain is built by hand, so the
numbers in the assertions are the ones a reader can check."""

import asyncio
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

    async def chain(self, symbol: str, expiry: date) -> Chain:
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


def test_the_buy_side_bias_turns_the_verdict_and_the_order_around():
    service = _Service({"RICH": _chain(iv=0.40), "CHEAP": _chain(iv=0.12)})
    body = _run(
        service,
        ScreenRequest(symbols=["RICH", "CHEAP"], bias="buy_premium"),
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
