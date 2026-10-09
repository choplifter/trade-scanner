"""Near expiry Alpaca sends no IV: the chain solves it from the mid, with
the time to the 16:00 close in minutes."""

from datetime import date, datetime

from app.options.chain import Chain, LegQuote, StrikeRow, chain_with_solved_iv, with_solved_iv
from app.options.payoff import bs_price, years_between
from app.services.market_clock import ET

DAY = date(2026, 10, 9)
NOW = datetime(2026, 10, 9, 10, 0, tzinfo=ET)


def _quote(strike: float, mid: float | None, iv: float | None = None, delta: float | None = None) -> LegQuote:
    return LegQuote(
        symbol=f"SPY261009P{int(strike * 1000):08d}", strike=strike, kind="put", expiry=DAY, bid=None, ask=None,
        mid=mid, last=None, bid_size=None, ask_size=None, delta=delta, gamma=None, theta=None, iv=iv,
        open_interest=0, tradable=True,
    )


def test_a_same_day_put_without_alpacas_iv_gets_one_solved_from_its_mid():
    years = years_between(NOW, DAY)
    assert 0 < years < 1 / 365, "six hours to the close, counted in minutes"
    mid = bs_price("put", 775.0, 770.0, years, 0.14)
    solved = with_solved_iv(_quote(770.0, mid), 775.0, NOW)
    assert abs(solved.iv - 0.14) < 0.001
    assert solved.delta is not None and -0.5 < solved.delta < 0 and solved.gamma > 0


def test_alpacas_own_values_are_kept_and_a_quote_without_a_price_stays_empty():
    own = _quote(770.0, 0.20, iv=0.31, delta=-0.2)
    assert with_solved_iv(own, 775.0, NOW) is own
    assert with_solved_iv(_quote(770.0, None), 775.0, NOW).iv is None
    assert with_solved_iv(_quote(770.0, 0.20), None, NOW).iv is None


def test_the_chain_is_solved_as_a_new_chain():
    years = years_between(NOW, DAY)
    rows = [StrikeRow(k, None, _quote(k, bs_price("put", 775.0, k, years, 0.15))) for k in (771.0, 773.0, 775.0)]
    chain = Chain(underlying="SPY", expiry=DAY, spot=775.0, feed="opra", as_of=NOW, rows=rows)
    solved = chain_with_solved_iv(chain, NOW)
    assert solved is not chain and all(abs(r.put.iv - 0.15) < 0.002 for r in solved.rows)
    assert all(r.put.iv is None for r in chain.rows), "the cached chain is left as fetched"
