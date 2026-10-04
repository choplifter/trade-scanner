"""The chart's delta lines: which expiry, which strikes, and when a wider
chain is worth a second fetch. No network -- a fake service hands out
chains whose deltas fall off with distance from spot."""

import asyncio
from datetime import date, datetime, timedelta, timezone

from app.options.chain import Chain, LegQuote, StrikeRow
from app.options.chain_fetch import STRIKE_PCT_RANGE
from app.options.delta_levels import delta_levels, width_for
from app.options.screener import CHAIN_WIDTH_MAX

TODAY = datetime.now(timezone.utc).date()
SPOT = 100.0


def _quote(strike: float, kind: str, delta: float, expiry: date, iv: float = 0.25) -> LegQuote:
    return LegQuote(
        symbol=f"X{kind[0].upper()}{strike:g}", strike=strike, kind=kind, expiry=expiry, bid=1.0, ask=1.1,
        mid=1.05, last=1.0, bid_size=1, ask_size=1, delta=delta, gamma=0.01, theta=-0.01, iv=iv,
        open_interest=100, tradable=True,
    )


def _chain(expiry: date, width: float, *, slope: float = 0.05, iv: float = 0.25) -> Chain:
    """Strikes every dollar within spot * width; |delta| falls by `slope`
    a strike from 0.5 at the money, so a steeper slope reaches 16 delta
    sooner. Puts fall slower than calls -- the skew."""
    rows = []
    reach = int(SPOT * width)
    for k in range(int(SPOT) - reach, int(SPOT) + reach + 1):
        off = k - SPOT
        call = max(0.01, min(0.99, 0.5 - slope * off))
        put = -max(0.01, min(0.99, 0.5 + slope * 0.7 * off))
        rows.append(
            StrikeRow(strike=float(k), call=_quote(k, "call", call, expiry, iv), put=_quote(k, "put", put, expiry, iv))
        )
    return Chain(underlying="X", expiry=expiry, spot=SPOT, feed="opra", as_of=None, rows=rows)


class _Service:
    def __init__(self, expiries: list[date], *, slope: float = 0.05, iv: float = 0.25):
        self.listed = expiries
        self.slope = slope
        self.iv = iv
        self.fetched: list[tuple[date, float | None]] = []

    async def expiries(self, symbol: str) -> dict:
        return {"expiries": [{"expiry": e.isoformat()} for e in self.listed]}

    async def chain(self, symbol: str, expiry: date, width: float | None = None) -> Chain:
        self.fetched.append((expiry, width))
        return _chain(expiry, width or STRIKE_PCT_RANGE, slope=self.slope, iv=self.iv)


def test_the_lines_sit_on_the_strikes_nearest_the_delta_with_the_put_further_out():
    expiry = TODAY + timedelta(days=45)
    out = asyncio.run(delta_levels(_Service([expiry]), "X", TODAY, 0.16))
    assert out["expiry"] == expiry.isoformat() and out["dte"] == 45
    # Calls: 0.5 - 0.05 * off = 0.16 at off 6.8 -> 107 (0.15). Puts fall
    # at 0.035 a strike: 0.16 at off -9.7 -> 90 (0.15).
    assert out["call"] == {"strike": 107.0, "delta": 0.15}
    assert out["put"] == {"strike": 90.0, "delta": 0.15}
    assert SPOT - out["put"]["strike"] > out["call"]["strike"] - SPOT, "the skew puts the put further out"


def test_the_expiry_is_the_one_in_the_30_60_day_window():
    near, inside, far = (TODAY + timedelta(days=d) for d in (7, 40, 90))
    service = _Service([near, inside, far])
    out = asyncio.run(delta_levels(service, "X", TODAY))
    assert out["expiry"] == inside.isoformat()
    assert all(e == inside for e, _ in service.fetched)


def test_no_expiry_in_the_window_costs_no_chain_fetch():
    service = _Service([TODAY + timedelta(days=5)])
    assert asyncio.run(delta_levels(service, "X", TODAY)) is None
    assert service.fetched == []


def test_a_target_beyond_the_default_band_buys_one_wider_chain():
    # A slope so shallow the default +/-10 strikes stop at 0.30 delta, on
    # an IV high enough to say the target lies a quarter of spot out.
    service = _Service([TODAY + timedelta(days=45)], slope=0.02, iv=0.50)
    out = asyncio.run(delta_levels(service, "X", TODAY, 0.16))
    assert len(service.fetched) == 2 and service.fetched[1][1] > STRIKE_PCT_RANGE
    assert out["call"]["strike"] == 117.0, "found past the default band's edge at 110"
    assert out["put"]["strike"] == 76.0


def test_a_chain_that_already_reaches_the_target_is_not_fetched_twice():
    service = _Service([TODAY + timedelta(days=45)])
    asyncio.run(delta_levels(service, "X", TODAY, 0.16))
    assert len(service.fetched) == 1


def test_the_width_grows_with_iv_and_stays_inside_the_cap():
    assert width_for(0.16, 0.10, 45) == STRIKE_PCT_RANGE, "a quiet name keeps the default band"
    assert width_for(0.16, 0.60, 45) > width_for(0.16, 0.30, 45)
    assert width_for(0.05, 3.0, 60) == CHAIN_WIDTH_MAX
