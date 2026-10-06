"""The daily method on hand-built chains: the spread it picks and sizes, the
exits it calls, and the market light, group and risk caps around entries."""

import math
from dataclasses import dataclass
from datetime import date, timedelta

import pytest

from app.options.chain import Chain, LegQuote, StrikeRow
from app.options.daily_method import HeldSpread, evaluate, exit_for, held_from_groups, market_light, put_spread

TODAY = date(2026, 10, 6)
EXPIRY = TODAY + timedelta(days=45)


def _put(strike: float, spot: float, iv: float = 0.25) -> LegQuote:
    # A rough lognormal put: price and delta from distance to spot.
    t = 45 / 365
    sd = iv * math.sqrt(t)
    d1 = (math.log(spot / strike) + 0.5 * sd * sd) / sd
    n = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))  # noqa: E731
    price = strike * n(-(d1 - sd)) - spot * n(-d1)
    mid = max(0.05, round(price, 2))
    return LegQuote(symbol=f"P{strike}", strike=strike, kind="put", expiry=EXPIRY, bid=round(mid * 0.97, 2),
                    ask=round(mid * 1.03 + 0.01, 2), mid=mid, last=mid, bid_size=10, ask_size=10,
                    delta=-n(-d1), gamma=0.0, theta=0.0, iv=iv, open_interest=1000, tradable=True)


def _chain(symbol: str = "XLF", spot: float = 50.0, step: float = 0.5) -> Chain:
    strikes = [round(spot * 0.7 + i * step, 2) for i in range(int(spot * 0.6 / step))]
    return Chain(underlying=symbol, expiry=EXPIRY, spot=spot, feed="opra", as_of=None,
                 rows=[StrikeRow(strike=k, call=None, put=_put(k, spot)) for k in strikes])


def test_the_spread_sells_near_30_delta_and_fits_one_percent():
    spread = put_spread(_chain(), equity=100_000)
    assert spread["short_strike"] < 50 and spread["long_strike"] < spread["short_strike"]
    assert abs(spread["short_delta"]) == pytest.approx(0.30, abs=0.04)
    assert spread["width"] == pytest.approx(1.5, abs=0.5), "3 % of a $50 spot"
    assert spread["max_loss"] <= 1_000 and spread["contracts"] >= 1
    assert spread["credit_natural"] <= spread["credit_mid"]


def test_a_small_account_narrows_the_width_until_one_contract_fits():
    wide = put_spread(_chain(spot=500.0, step=5.0), equity=100_000)
    # $400 a trade: the 15-point width risks more, one strike (5 points) fits.
    narrow = put_spread(_chain(spot=500.0, step=5.0), equity=40_000)
    assert narrow["width"] < wide["width"] and narrow["max_loss"] <= 400
    assert put_spread(_chain(spot=500.0, step=5.0), equity=1_000) is None, "not even one strike's width fits $10"


def _held(**kw) -> HeldSpread:
    base = dict(id="g1", symbol="XLF", expiry=EXPIRY, dte=40, qty=2, short_strike=48.0, long_strike=46.5,
                credit=0.40, unrealized_pl=0.0, legs=[("P46.5", 2), ("P48", -2)])
    base.update(kw)
    return HeldSpread(**base)


def test_exits_take_half_stop_at_twice_and_leave_at_21_days():
    assert exit_for(_held(unrealized_pl=45.0)).startswith("target")  # credit 80, half is 40
    assert exit_for(_held(unrealized_pl=-165.0)).startswith("stop")  # twice the credit is 160
    assert exit_for(_held(dte=21)).startswith("time")
    assert exit_for(_held(unrealized_pl=10.0, dte=30)) is None


def test_the_market_light_turns_red_below_the_200_day_and_on_a_spike():
    up = [100 + i * 0.1 for i in range(260)]
    assert market_light(up, [0.15] * 30)["status"] == "green"
    assert market_light(list(reversed(up)), [0.15] * 30)["status"] == "red"
    light = market_light(up, [0.15] * 25 + [0.20])
    assert light["status"] == "red" and light["spy_iv_limit"] == pytest.approx(0.18)


def _evaluate(**over):
    up = [100 + i * 0.1 for i in range(260)]
    calm = [50 * (1.002 if i % 2 else 0.998) ** 1 for i in range(260)]  # ~3 % realised
    args = dict(
        today=TODAY, equity=100_000, symbols=["XLF", "SPY", "QQQ"],
        closes={"XLF": calm, "SPY": up, "QQQ": calm},
        iv_history={s: [0.20] * 100 for s in ("XLF", "SPY", "QQQ")},
        chains={"XLF": _chain("XLF"), "SPY": _chain("SPY", 500.0, 5.0), "QQQ": _chain("QQQ", 400.0, 5.0)},
        atm_iv={"XLF": 0.25, "SPY": 0.25, "QQQ": 0.25},
        spy_closes=up, spy_ivs=[0.15] * 30, held=[],
    )
    args.update(over)
    return evaluate(**args)


def test_entries_respect_the_light_the_groups_and_held_symbols():
    out = _evaluate()
    symbols = [e["symbol"] for e in out.entries]
    # SPY and QQQ share the equity group: only one of them gets in.
    assert "XLF" in symbols and len({"SPY", "QQQ"} & set(symbols)) == 1
    assert any("group 'equity'" in s["reason"] for s in out.skipped)
    assert all(e["ticket"]["strategy"] == "bull_put" for e in out.entries)

    red = _evaluate(spy_closes=list(reversed([100 + i * 0.1 for i in range(260)])))
    assert not red.entries and all("market light red" in s["reason"] for s in red.skipped if "rich" not in s["reason"])

    held = _evaluate(held=[_held(symbol="XLF")])
    assert "XLF" not in [e["symbol"] for e in held.entries]


def test_the_total_risk_cap_stops_entries_at_five_percent():
    many = [_held(id=f"g{i}", symbol=f"X{i}", qty=10, credit=0.10) for i in range(4)]  # 4 x 1,400 = 5,600 open
    out = _evaluate(held=many)
    assert not out.entries and any("total risk cap" in s["reason"] for s in out.skipped)


@dataclass
class _Leg:
    symbol: str
    kind: str
    strike: float
    qty: int


@dataclass
class _Group:
    id: str
    underlying: str
    expiry: date
    strategy: str
    qty: int
    legs: list
    net_entry: float
    unrealized_pl: float
    broken: bool = False


def test_held_spreads_are_read_from_the_accounts_bull_put_groups_only():
    groups = [
        _Group("a", "XLF", EXPIRY, "bull_put", 2, [_Leg("P46.5", "put", 46.5, 2), _Leg("P48", "put", 48.0, -2)], -0.40, 30.0),
        _Group("b", "SPY", EXPIRY, "iron_condor", 1, [], -1.0, 0.0),
    ]
    held = held_from_groups(groups, TODAY)
    assert len(held) == 1 and held[0].short_strike == 48.0 and held[0].long_strike == 46.5
    assert held[0].credit == pytest.approx(0.40) and held[0].max_loss == pytest.approx((1.5 - 0.40) * 100 * 2)
