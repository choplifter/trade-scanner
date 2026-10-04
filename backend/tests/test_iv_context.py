"""IV context and the history behind an IV rank -- app.options.iv_context
and app.options.iv_history_store. The pure half needs no store; the store
half runs against a tmp_path sqlite file, as test_trigger_store.py does."""

import asyncio
import math
from datetime import date

import pytest

from app.options.chain import Chain, LegQuote, StrikeRow
from app.options.iv_context import (
    atm_iv,
    iv_premium,
    realized_vol,
    parity_offset,
    skew,
    term_structure,
)
from app.options.iv_history_store import MIN_SAMPLES, IvHistoryStore, rank_within

EXPIRY = date(2026, 9, 18)
FAR_EXPIRY = date(2026, 10, 16)
TODAY = date(2026, 9, 4)
SPOT = 100.0


def _quote(kind, strike, iv, expiry=EXPIRY) -> LegQuote:
    return LegQuote(
        symbol=f"X{strike:g}{kind[0].upper()}",
        strike=strike,
        kind=kind,
        expiry=expiry,
        bid=1.0,
        ask=1.1,
        mid=1.05,
        last=1.05,
        bid_size=1,
        ask_size=1,
        delta=0.5,
        gamma=0.01,
        theta=-0.02,
        iv=iv,
        open_interest=100,
        tradable=True,
    )


def _chain(ivs: dict, *, expiry=EXPIRY, spot=SPOT) -> Chain:
    """`ivs` maps strike -> (call_iv, put_iv); None drops that side."""
    rows = []
    for strike, (call_iv, put_iv) in sorted(ivs.items()):
        rows.append(
            StrikeRow(
                strike=float(strike),
                call=_quote("call", strike, call_iv, expiry) if call_iv is not None else None,
                put=_quote("put", strike, put_iv, expiry) if put_iv is not None else None,
            )
        )
    return Chain(underlying="X", expiry=expiry, spot=spot, feed="opra", as_of=None, rows=rows)  # type: ignore[arg-type]


# --- ATM IV ---------------------------------------------------------------


def test_atm_iv_averages_the_two_sides_of_the_nearest_strike():
    chain = _chain({95: (0.40, 0.44), 100: (0.30, 0.34), 105: (0.28, 0.32)})

    assert atm_iv(chain) == pytest.approx(0.32)


def test_atm_iv_uses_whichever_side_is_quoted():
    chain = _chain({100: (0.30, None)})

    assert atm_iv(chain) == pytest.approx(0.30)


def test_atm_iv_skips_a_strike_whose_iv_did_not_solve():
    # 100 is nearest but carries no IV on either side; 102 is the nearest
    # strike that actually says something.
    chain = _chain({100: (None, None), 102: (0.31, 0.33), 110: (0.28, 0.30)})

    assert atm_iv(chain) == pytest.approx(0.32)


def test_atm_iv_is_none_when_nothing_solved():
    assert atm_iv(_chain({100: (None, None)})) is None


def test_atm_iv_is_none_on_an_empty_chain():
    assert atm_iv(_chain({})) is None


# --- skew and term structure ----------------------------------------------


def test_skew_is_positive_when_downside_costs_more():
    # Both sides agree at the money, so there is no parity offset to remove.
    chain = _chain({95: (0.30, 0.40), 99: (0.32, 0.32), 100: (0.30, 0.30), 105: (0.26, 0.30)})

    # out-of-the-money put at 95 (0.40) minus out-of-the-money call at 105 (0.26)
    assert skew(chain) == pytest.approx(0.14)


def test_skew_is_none_when_one_side_has_no_quotes():
    chain = _chain({95: (None, 0.40), 100: (None, 0.32)})

    assert skew(chain) is None


def test_the_parity_offset_is_the_gap_between_the_sides_near_the_money():
    # Calls read four points above puts on every strike that quotes both:
    # a forward assumption, not a view (see parity_offset).
    chain = _chain({99: (0.34, 0.30), 100: (0.36, 0.32), 101: (0.38, 0.34), 90: (0.50, 0.44)})

    # Only the three strikes within 3 % of spot count; the far one does not.
    assert parity_offset(chain) == pytest.approx(0.04)
    assert parity_offset(_chain({100: (0.36, 0.32)})) is None  # one pair is not an average


def test_skew_is_measured_after_the_two_sides_are_put_on_one_footing():
    """The ORCL shape: calls quoted a constant four points above puts, and
    the curve itself flat. The raw difference would call that a four-point
    skew; there is none."""
    chain = _chain({95: (0.36, 0.32), 99: (0.34, 0.30), 100: (0.34, 0.30), 101: (0.34, 0.30), 105: (0.34, 0.30)})

    assert parity_offset(chain) == pytest.approx(0.04)
    # put at 95 is 0.32; call at 105 is 0.34, worth 0.30 once corrected.
    assert skew(chain) == pytest.approx(0.02, abs=1e-9)


def test_a_chain_too_thin_to_measure_an_offset_falls_back_to_the_raw_gap():
    chain = _chain({95: (None, 0.40), 100: (0.32, None), 105: (0.26, None)})

    assert parity_offset(chain) is None
    assert skew(chain) == pytest.approx(0.14)


def test_skew_ignores_the_in_the_money_twins():
    """A put above spot and a call below it are quoted off their OTM
    partners; taking one of those would measure moneyness, not asymmetry."""
    chain = _chain({95: (0.30, 0.40), 100: (0.30, 0.30), 105: (0.26, 0.90)})

    # The 105 put (0.90) is in the money and must not be picked as "the put".
    assert skew(chain) == pytest.approx(0.14)


def test_term_structure_reports_each_expiry_nearest_first():
    near = _chain({100: (0.44, 0.46)})
    far = _chain({100: (0.30, 0.32)}, expiry=FAR_EXPIRY)

    rows = term_structure([far, near], TODAY)

    assert [row.expiry for row in rows] == [EXPIRY, FAR_EXPIRY]
    assert rows[0].dte == 14
    # A front expiry well above the back one is an event being priced in --
    # the whole reason the term structure is in the payload.
    assert rows[0].atm_iv > rows[1].atm_iv


def test_term_structure_serialises_with_rounded_numbers():
    row = term_structure([_chain({100: (0.123456, 0.123456)})], TODAY)[0].to_dict()

    assert row["atm_iv"] == 0.1235
    assert row["expiry"] == EXPIRY.isoformat()


# --- realised vol ---------------------------------------------------------


def test_realized_vol_of_a_flat_series_is_zero():
    assert realized_vol([100.0] * 25) == pytest.approx(0.0)


def test_realized_vol_annualises_a_known_daily_move():
    # Alternating +1%/-1% log moves: the sample stdev of the returns is
    # very close to the move size, annualised by sqrt(252).
    closes = [100.0]
    for i in range(30):
        closes.append(closes[-1] * (1.01 if i % 2 == 0 else 1 / 1.01))

    vol = realized_vol(closes)

    assert vol == pytest.approx(math.log(1.01) * math.sqrt(252), rel=0.05)


def test_realized_vol_needs_enough_closes():
    assert realized_vol([100.0, 101.0]) is None
    assert realized_vol([]) is None


def test_realized_vol_ignores_junk_closes():
    assert realized_vol([100.0, 0.0, None, 101.0, 102.0, 103.0]) is not None


def test_realized_vol_uses_only_the_last_window():
    calm = [100.0] * 40
    wild = calm + [100.0, 130.0, 90.0, 140.0, 80.0] * 5

    assert realized_vol(wild, window=20) > realized_vol(calm, window=20)


def test_iv_premium_is_the_ratio_and_none_without_both_halves():
    assert iv_premium(0.30, 0.20) == pytest.approx(1.5)
    assert iv_premium(None, 0.20) is None
    assert iv_premium(0.30, None) is None
    assert iv_premium(0.30, 0.0) is None


# --- IV rank --------------------------------------------------------------


def test_rank_within_places_a_reading_in_its_range():
    history = [0.20, 0.40] + [0.30] * (MIN_SAMPLES - 2)

    rank = rank_within(0.30, history)

    assert rank is not None
    assert rank.percent == pytest.approx(50.0)
    assert (rank.low, rank.high) == (0.20, 0.40)
    assert rank.samples == MIN_SAMPLES


def test_rank_within_refuses_a_thin_history():
    assert rank_within(0.30, [0.20, 0.40, 0.30]) is None


def test_rank_within_refuses_a_flat_history():
    assert rank_within(0.30, [0.30] * MIN_SAMPLES) is None


def test_rank_within_clamps_a_fresh_high():
    history = [0.20, 0.40] + [0.30] * (MIN_SAMPLES - 2)

    assert rank_within(0.99, history).percent == pytest.approx(100.0)
    assert rank_within(0.01, history).percent == pytest.approx(0.0)


# --- the store ------------------------------------------------------------


def _store(tmp_path) -> IvHistoryStore:
    store = IvHistoryStore(str(tmp_path / "iv.sqlite3"))
    asyncio.run(store.init_schema())
    return store


def test_the_store_keeps_one_reading_per_symbol_per_day(tmp_path):
    store = _store(tmp_path)

    async def run():
        await store.record("SPY", date(2026, 9, 1), 0.20, 30)
        await store.record("SPY", date(2026, 9, 1), 0.25, 30)  # later in the same session
        await store.record("SPY", date(2026, 9, 2), 0.30, 30)
        return await store.history("SPY")

    history = asyncio.run(run())

    # The day's last reading wins, and the two sessions are two rows.
    assert sorted(history) == [0.25, 0.30]


def test_the_store_separates_symbols(tmp_path):
    store = _store(tmp_path)

    async def run():
        await store.record("SPY", date(2026, 9, 1), 0.20, 30)
        await store.record("AMD", date(2026, 9, 1), 0.60, 30)
        return await store.history("spy"), await store.history("AMD")

    spy, amd = asyncio.run(run())

    assert spy == [0.20]
    assert amd == [0.60]


def test_the_store_ignores_a_missing_or_impossible_reading(tmp_path):
    store = _store(tmp_path)

    async def run():
        await store.record("SPY", date(2026, 9, 1), 0.0, 30)
        await store.record("SPY", date(2026, 9, 2), -1.0, 30)
        return await store.history("SPY")

    assert asyncio.run(run()) == []


def test_rank_reports_the_sample_count_while_history_is_still_thin(tmp_path):
    store = _store(tmp_path)

    async def run():
        for i in range(5):
            await store.record("AMD", date(2026, 9, 1 + i), 0.20 + i / 100, 30)
        return await store.rank("AMD", 0.22)

    rank, samples = asyncio.run(run())

    # The honest answer while the range is still being built: no rank, but
    # say how far along it is.
    assert rank is None
    assert samples == 5


def test_rank_answers_once_there_is_enough_history(tmp_path):
    store = _store(tmp_path)

    async def run():
        for i in range(MIN_SAMPLES):
            await store.record("AMD", date(2026, 1, 1 + i), 0.10 + i / 100, 30)
        return await store.rank("AMD", 0.10)

    rank, samples = asyncio.run(run())

    assert samples == MIN_SAMPLES
    assert rank is not None
    assert rank.percent == pytest.approx(0.0)


def test_rank_without_a_current_reading_asks_nothing_of_the_store(tmp_path):
    store = _store(tmp_path)

    assert asyncio.run(store.rank("AMD", None)) == (None, 0)


def test_a_steady_trend_is_volatility_not_calm():
    """Measured from zero: a stock rising 1 % a day moves 16 % a year as an
    option sees it. A mean-corrected deviation called it motionless."""
    trend = [100.0 * 1.01**i for i in range(25)]
    assert realized_vol(trend) == pytest.approx(math.log(1.01) * math.sqrt(252), rel=1e-9)


def test_the_atm_iv_is_read_from_bid_quotes_only():
    from app.options.chain import Chain, LegQuote, StrikeRow
    from app.options.iv_context import atm_iv

    def q(kind, iv, bid):
        return LegQuote(symbol="X", strike=100.0, kind=kind, expiry=date(2026, 11, 20), bid=bid, ask=1.1, mid=1.0,
                        last=1.0, bid_size=1, ask_size=1, delta=0.5, gamma=0.0, theta=0.0, iv=iv, open_interest=1, tradable=True)

    # The nearest strike's call is unbid (an IV solved from the ask alone);
    # only its bid put counts, and an all-unbid strike is passed over.
    rows = [
        StrikeRow(strike=100.0, call=q("call", 0.90, 0.0), put=q("put", 0.30, 0.95)),
        StrikeRow(strike=101.0, call=q("call", 0.80, None), put=q("put", 0.85, 0.0)),
    ]
    chain = Chain(underlying="X", expiry=date(2026, 11, 20), spot=100.6, feed="opra", as_of=None, rows=rows)
    assert atm_iv(chain) == pytest.approx(0.30)


# --- volatility cone ---------------------------------------------------------


def _path(daily_moves: list[float], start: float = 100.0) -> list[float]:
    out = [start]
    for m in daily_moves:
        out.append(out[-1] * math.exp(m))
    return out


def test_the_cone_ranks_todays_iv_among_the_spells_the_stock_realised():
    from app.options.iv_context import cone_reading, cone_window

    # A quiet half year (0.5 % a day) then a wild one (2 % a day), alternating
    # in sign: 30-session spells realise ~8 % in the first half, ~32 % in the
    # second, and a mix where they straddle the change.
    closes = _path([0.005 * (-1) ** i for i in range(130)] + [0.02 * (-1) ** i for i in range(130)])
    assert cone_window(45) == 31

    calm, middle, wild = (cone_reading(iv, closes, 45) for iv in (0.05, 0.20, 0.40))
    assert calm.percentile == 0.0 and wild.percentile == 100.0
    assert 0.0 < middle.percentile < 100.0
    assert calm.low == pytest.approx(0.005 * math.sqrt(252), rel=1e-6)
    assert calm.high == pytest.approx(0.02 * math.sqrt(252), rel=1e-6)
    assert calm.spells == 230  # one per session from the first full spell, within the year


def test_no_cone_without_the_history_or_the_expiry():
    from app.options.iv_context import cone_reading

    short = _path([0.01] * 40)
    assert cone_reading(0.3, short, 45) is None, "40 sessions hold too few 31-session spells to rank among"
    long = _path([0.01 * (-1) ** i for i in range(260)])
    assert cone_reading(None, long, 45) is None and cone_reading(0.3, long, None) is None


# --- volatility forecast ------------------------------------------------------


def _closes_with(daily_moves: list[float]) -> list[float]:
    out = [100.0]
    for move in daily_moves:
        out.append(out[-1] * math.exp(move))
    return out


def test_the_forecast_leans_on_the_recent_reading_short_and_on_the_year_long():
    from app.options.iv_context import forecast_vol

    # A quiet year (0.5 % a day) ending in a loud month (2 % a day).
    closes = _closes_with([0.005 * (-1) ** i for i in range(232)] + [0.02 * (-1) ** i for i in range(20)])
    near = forecast_vol(closes, 1)
    mid = forecast_vol(closes, 45)
    far = forecast_vol(closes, 400)
    assert near.recent > near.long_run
    assert near.forecast > mid.forecast > far.forecast, "the longer the option, the more it reverts"
    assert abs(near.forecast - near.recent) / near.recent < 0.05
    assert abs(far.forecast - far.long_run) / far.long_run < 0.1


def test_without_a_year_of_closes_the_recent_reading_stands_alone():
    from app.options.iv_context import forecast_vol

    closes = _closes_with([0.01 * (-1) ** i for i in range(40)])
    f = forecast_vol(closes, 45)
    assert f.long_run is None and f.forecast == f.recent and f.weight_recent == 1.0
    assert forecast_vol([100.0], 45) is None
