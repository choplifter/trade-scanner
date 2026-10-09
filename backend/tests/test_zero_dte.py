"""0DTE put credit spreads on minute bars: the strike picked by delta, the
stop on the spread's value, settlement on the close, and the summary."""

import math
from datetime import date, time

from app.options.payoff import bs_price
from app.options.zero_dte import DayBars, Params, occ_symbol, simulate_day, summarize

DAY = date(2025, 3, 14)
OPEN, ENTRY, CLOSE = 9 * 60 + 30, 10 * 60, 16 * 60
IV = 0.15


def _puts(path: dict[int, float], strikes=range(560, 601)) -> dict[float, dict[int, float]]:
    """Each put priced off the spot path at a flat IV, every minute."""
    out = {}
    for k in strikes:
        out[float(k)] = {
            m: max(bs_price("put", s, k, (CLOSE - m) / (365 * 24 * 60), IV), 0.01) for m, s in path.items() if m < CLOSE
        }
    return out


def _path(start: float, end: float) -> dict[int, float]:
    return {m: start + (end - start) * (m - OPEN) / (CLOSE - OPEN) for m in range(OPEN, CLOSE + 1)}


def test_symbol_is_the_occ_contract():
    assert occ_symbol("SPY", DAY, 585.0) == "SPY250314P00585000"


def test_a_quiet_day_expires_worthless_and_keeps_the_credit():
    path = _path(590.0, 591.0)
    trade = simulate_day(DAY, DayBars(spot=path, puts=_puts(path)), Params(short_delta=0.10, width=2.0))
    assert trade is not None and trade.exit == "expiry" and trade.exit_value == 0
    assert 0.07 <= abs(trade.short_delta) <= 0.13, "the strike nearest 0.10 delta"
    assert trade.long_strike == trade.short_strike - 2
    # Credit after a cent per leg, less 0.03 per contract on the two opening legs.
    assert math.isclose(trade.pnl, trade.credit * 100 - 0.06, abs_tol=0.01)


def test_a_sell_off_hits_the_stop_before_the_close():
    path = _path(590.0, 575.0)
    trade = simulate_day(DAY, DayBars(spot=path, puts=_puts(path)), Params(stop_mult=2.0))
    assert trade.exit == "stop" and trade.exit_at < "16:00"
    assert trade.exit_value >= 2 * trade.credit
    assert trade.pnl < 0
    # Four legs of fees: opened and closed.
    assert math.isclose(trade.pnl, (trade.credit - trade.exit_value) * 100 - 0.12, abs_tol=0.01)


def test_without_a_stop_it_settles_at_intrinsic_and_a_close_between_the_strikes_is_pinned():
    path = _path(590.0, 590.0)
    puts = _puts(path)
    trade = simulate_day(DAY, DayBars(spot=path, puts=puts), Params(stop_mult=None))
    pinned_path = {**path, CLOSE: trade.short_strike - 1.0}
    pinned = simulate_day(DAY, DayBars(spot=pinned_path, puts=puts), Params(stop_mult=None))
    assert pinned.exit == "expiry" and pinned.pinned and math.isclose(pinned.exit_value, 1.0)


def test_no_print_near_the_entry_or_too_little_credit_skips_the_day():
    path = _path(590.0, 590.0)
    stale = {k: {m: v for m, v in s.items() if m < ENTRY - 30} for k, s in _puts(path).items()}
    assert simulate_day(DAY, DayBars(spot=path, puts=stale), Params()) is None
    assert simulate_day(DAY, DayBars(spot=path, puts=_puts(path)), Params(min_credit=5.0)) is None


def test_the_summary_weighs_the_wins_against_the_losses():
    quiet = _path(590.0, 591.0)
    crash = _path(590.0, 575.0)
    trades = [simulate_day(date(2025, 3, d), DayBars(spot=quiet, puts=_puts(quiet)), Params()) for d in (3, 4, 5, 6)]
    trades.append(simulate_day(date(2025, 3, 7), DayBars(spot=crash, puts=_puts(crash)), Params()))
    out = summarize(trades, 5)
    assert out["trades"] == 5 and out["win_rate"] == 0.8 and out["stops"] == 1
    assert math.isclose(out["total"], sum(t.pnl for t in trades), abs_tol=0.01)
    assert out["max_drawdown"] < 0 and out["worst"][0]["exit"] == "stop"
    assert out["by_year"]["2025"]["trades"] == 5


def test_one_calendar_query_gives_each_session_with_its_early_closes():
    from app.services.market_clock import sessions_between, trading_hours_for

    sessions = sessions_between(date(2024, 11, 25), date(2024, 12, 2))
    assert date(2024, 11, 28) not in sessions, "Thanksgiving"
    assert sessions[date(2024, 11, 29)] == trading_hours_for(date(2024, 11, 29))
    assert sessions[date(2024, 11, 29)][1].hour == 13


def test_a_day_whose_lowest_strike_is_still_priced_does_not_cover():
    from app.options.zero_dte import covers

    path = _path(590.0, 590.0)
    assert covers(DayBars(spot=path, puts=_puts(path, strikes=range(560, 601))))
    # Strikes only down to 588: the lowest is worth far more than a dime.
    assert not covers(DayBars(spot=path, puts=_puts(path, strikes=range(588, 601))))


def test_a_day_that_rallied_above_the_highest_strike_does_not_cover():
    from app.options.zero_dte import covers

    rally = _path(590.0, 606.0)
    assert not covers(DayBars(spot=rally, puts=_puts(rally, strikes=range(560, 601))))
    assert covers(DayBars(spot=rally, puts=_puts(rally, strikes=range(560, 607))))
