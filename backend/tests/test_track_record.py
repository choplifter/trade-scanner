"""The track record: settling a prediction at expiry, the calibration
report, recording from the Screener and the ticket, and the historical
reconstruction. No network: sessions are built by hand."""

import asyncio
import json
import math
import random
from datetime import date, timedelta

import pytest

from app.options.track_record import (
    Leg,
    PredictionStore,
    forward_items,
    from_screen_row,
    historical_items,
    lognormal_between,
    report,
    resolve_one,
    settle_outcome,
    strike_for_delta,
    touch_probability,
    touched_level,
    wilson,
)

CONDOR = [Leg("put", 85, "buy"), Leg("put", 90, "sell"), Leg("call", 110, "sell"), Leg("call", 115, "buy")]


def test_a_condor_sold_for_a_credit_wins_between_its_breakevens():
    pnl, won = settle_outcome(CONDOR, -1.5, 100.0)
    assert won and pnl == pytest.approx(150.0)
    pnl, won = settle_outcome(CONDOR, -1.5, 111.0)
    assert won and pnl == pytest.approx(50.0), "inside the breakeven at 111.50"
    pnl, won = settle_outcome(CONDOR, -1.5, 120.0)
    assert not won and pnl == pytest.approx(-350.0), "the full width less the credit"


def test_a_level_is_touched_from_whichever_side_spot_was_on():
    sessions = [(101.0, 99.0), (104.0, 100.0)]
    assert touched_level(103.0, 100.0, sessions) is True
    assert touched_level(105.0, 100.0, sessions) is False
    assert touched_level(99.5, 100.0, sessions) is True
    assert touched_level(None, 100.0, sessions) is None


def _pred(**over) -> dict:
    base = {
        "legs": json.dumps([{"kind": l.kind, "strike": l.strike, "side": l.side, "ratio": 1} for l in CONDOR]),
        "expiry": "2026-09-18",
        "recorded_on": "2026-08-10",
        "price": -1.5,
        "spot": 100.0,
        "touch_at": 111.5,
    }
    return {**base, **over}


def _sessions(closes: list[tuple[date, float]]):
    return [(d, c + 1, c - 1, c) for d, c in closes]


def test_a_prediction_settles_on_the_expiry_close_and_records_a_touch():
    days = [date(2026, 8, 10) + timedelta(days=i) for i in range(0, 40)]
    closes = [(d, 100.0 + (12 if d == date(2026, 9, 1) else 0)) for d in days]
    out = resolve_one(_pred(), _sessions(closes))
    assert out["status"] == "resolved" and out["settle"] == 100.0 and out["won"] is True
    assert out["touched"] is True, "112 on 1 September went through 111.50"


def test_a_missing_expiry_session_or_a_calendar_is_not_settled():
    early = [(date(2026, 8, 10) + timedelta(days=i), 100.0) for i in range(10)]
    assert resolve_one(_pred(), _sessions(early))["status"] == "unresolvable"
    calendar = json.dumps([{"kind": "call", "strike": 100, "side": "sell", "ratio": 1},
                           {"kind": "call", "strike": 100, "side": "buy", "ratio": 1, "expiry": "2026-10-16"}])
    assert resolve_one(_pred(legs=calendar), _sessions(early))["status"] == "unresolvable"


def test_the_wilson_interval_stays_inside_zero_and_one():
    lo, hi = wilson(0, 5)
    assert lo == 0.0 and 0 < hi < 0.6
    lo, hi = wilson(50, 100)
    assert lo < 0.5 < hi


def test_the_report_buckets_by_predicted_chance_and_by_margin():
    items = [
        {"chance": 0.65, "won": True, "pnl": 100.0, "max_loss": -400.0, "margin": 3.0},
        {"chance": 0.68, "won": False, "pnl": -400.0, "max_loss": -400.0, "margin": -3.0},
        {"chance": 0.85, "won": True, "pnl": 50.0, "max_loss": -450.0, "margin": 1.0},
    ]
    r = report(items)
    assert r["overall"]["n"] == 3 and r["overall"]["actual"] == pytest.approx(2 / 3, abs=1e-4)
    ranges = {tuple(b["range"]): b["n"] for b in r["by_chance"]}
    assert ranges[(0.6, 0.7)] == 2 and ranges[(0.8, 0.9)] == 1
    margins = {tuple(b["range"]): b["actual"] for b in r["by_margin"]}
    assert margins[(2.0, 5.0)] == 1.0 and margins[(-5.0, -2.0)] == 0.0


def test_a_screen_row_becomes_the_structure_its_chance_was_computed_for():
    row = {
        "symbol": "IWM", "expiry": "2026-11-20", "spot": 250.0, "atm_iv": 0.2, "forecast_vol": 0.17,
        "put_spread": {"short_strike": 240, "long_strike": 232, "credit": 1.2, "cross": 0.1},
        "call_spread": {"short_strike": 262, "long_strike": 270, "credit": 0.9, "cross": 0.1},
        "outcome": {"win_probability": 0.62, "breakeven_vol": 0.21, "max_loss": -590.0},
    }
    p = from_screen_row(row, "iron_condor")
    assert len(p["legs"]) == 4 and p["price"] == pytest.approx(-(2.1 - 0.2)), "the natural: credit less the cross"
    assert p["chance"] == 0.62 and p["breakeven_vol"] == 0.21
    assert from_screen_row({**row, "outcome": None}, "iron_condor") is None


def test_the_store_keeps_one_prediction_per_structure_a_day_and_hands_back_the_due(tmp_path):
    store = PredictionStore(str(tmp_path / "t.sqlite3"))
    asyncio.run(store.init_schema())
    pred = {"source": "screen", "strategy": "iron_condor", "underlying": "iwm", "expiry": "2026-01-16",
            "legs": [{"kind": "put", "strike": 240, "side": "sell", "ratio": 1}], "price": -1.0, "spot": 250.0,
            "chance": 0.6}
    assert asyncio.run(store.record([pred, pred])) == 1
    assert asyncio.run(store.record([pred])) == 0, "the same structure again today"
    due = asyncio.run(store.due(date(2026, 1, 20)))
    assert len(due) == 1 and due[0]["underlying"] == "IWM"
    asyncio.run(store.settle(due[0]["id"], status="resolved", settle=245.0, settled_on="2026-01-16", pnl=100.0, won=True, touched=None))
    rows = asyncio.run(store.all())
    assert forward_items(rows)[0]["won"] is True


def test_delta_strikes_and_the_lognormal_agree():
    s, sigma, t = 100.0, 0.25, 45 / 365
    kc = strike_for_delta("call", s, sigma, t, 0.16)
    kp = strike_for_delta("put", s, sigma, t, 0.16)
    assert kp < s < kc
    inside = lognormal_between(s, kp, kc, sigma, t)
    assert 0.6 < inside < 0.75, "16-delta shorts hold about two thirds between them"
    assert touch_probability(s, kc, sigma, t) > 1 - lognormal_between(s, 0, kc, sigma, t)


def test_the_reconstruction_is_calibrated_on_a_market_that_moves_as_priced():
    """A random walk at exactly the volatility the IV says: the predicted
    and the actual win rates must meet, within sampling noise."""
    rng = random.Random(7)
    sigma = 0.25
    start = date(2023, 1, 2)
    days, closes = [], []
    price = 100.0
    d = start
    while len(days) < 900:
        if d.weekday() < 5:
            days.append(d)
            closes.append(price)
            price *= math.exp(rng.gauss(-0.5 * sigma * sigma / 252, sigma / math.sqrt(252)))
        d += timedelta(days=1)
    sessions = {"X": [(dd, c * 1.002, c * 0.998, c) for dd, c in zip(days, closes)]}
    ivs = {"X": [(dd, sigma) for dd in days[300:]]}
    items = historical_items(ivs, sessions, last_day=days[-1])
    r = report(items)
    assert r["overall"]["n"] > 300
    assert abs(r["overall"]["actual"] - r["overall"]["predicted"]) < 0.08
