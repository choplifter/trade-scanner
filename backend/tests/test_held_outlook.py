"""A held spread's outlook from today's mark: what it earns a day to expiry
if the stock moves as its realised-vol forecast says, beside the IV its
legs are priced at."""

import math
from datetime import datetime, timedelta, timezone

from app.options.iv_context import VolForecast
from app.options.models import SpreadLeg
from app.options.service import held_outlook

NOW = datetime(2026, 10, 7, 15, tzinfo=timezone.utc)
EXPIRY = NOW.date() + timedelta(days=30)
SPOT, IV = 100.0, 0.30


def _bs(kind: str, k: float) -> float:
    t = (EXPIRY - NOW.date()).days / 365
    sd = IV * math.sqrt(t)
    d1 = (math.log(SPOT / k) + 0.5 * sd * sd) / sd
    n = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))  # noqa: E731
    call = SPOT * n(d1) - k * n(d1 - sd)
    return call if kind == "call" else call - SPOT + k


def _leg(kind: str, k: float, side: str, iv: float = IV) -> SpreadLeg:
    mid = _bs(kind, k)
    return SpreadLeg(symbol=f"X{kind}{k}", kind=kind, strike=k, expiry=EXPIRY, side=side,
                     position_intent=f"{side}_to_open", mid=mid, bid=mid, ask=mid, iv=iv)


CONDOR = [_leg("put", 85, "buy"), _leg("put", 90, "sell", 0.32), _leg("call", 110, "sell", 0.28), _leg("call", 115, "buy")]


def _vol(f: float) -> VolForecast:
    return VolForecast(forecast=f, recent=f, long_run=None, weight_recent=1.0)


def test_a_short_condor_earns_a_day_on_a_quiet_stock_and_loses_on_a_wild_one():
    quiet = held_outlook(CONDOR, 2, SPOT, NOW, _vol(0.15))
    wild = held_outlook(CONDOR, 2, SPOT, NOW, _vol(0.50))
    assert quiet.dte == 30 and quiet.pnl_per_day > 0 > wild.pnl_per_day
    assert abs(quiet.pnl_per_day - quiet.expected_value_rv / 30) < 0.01
    # The short legs' IV, not the wings'.
    assert quiet.iv == 0.30 and quiet.rv_forecast == 0.15


def test_at_its_own_iv_the_position_expects_about_nothing():
    flat = held_outlook(CONDOR, 1, SPOT, NOW, _vol(IV))
    assert abs(flat.expected_value_rv) < 3


def test_no_forecast_no_mark_or_no_day_left_gives_no_outlook():
    assert held_outlook(CONDOR, 1, SPOT, NOW, None) is None
    unpriced = [CONDOR[0].model_copy(update={"mid": None}), *CONDOR[1:]]
    assert held_outlook(unpriced, 1, SPOT, NOW, _vol(0.2)) is None
    assert held_outlook(CONDOR, 1, SPOT, datetime.combine(EXPIRY, NOW.timetz()), _vol(0.2)) is None


def test_the_list_and_the_risk_chart_read_the_same_outlook():
    """spread_risks hands position_risk the legs as held (signed contracts in
    all); the risk chart hands service the ticket's legs and a quantity."""
    from app.options.position_risk import RiskLeg, held_outlook as from_risk_legs

    held = [RiskLeg(l.kind, l.strike, l.expiry, (1 if l.side == "buy" else -1) * 2, l.iv, l.mid) for l in CONDOR]
    listed = from_risk_legs(held, SPOT, NOW, _vol(0.2))
    charted = held_outlook(CONDOR, 2, SPOT, NOW, _vol(0.2))
    assert listed == charted.model_dump()
    assert from_risk_legs([*held, RiskLeg("stock", 0.0, None, 100)], SPOT, NOW, _vol(0.2)) is None
