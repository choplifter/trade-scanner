"""A freely built position through the real preview: its direction read
off the market, its ceilings from the payoff curve, the collateral of an
uncovered short, and the two probabilities -- over the simulated service
with a made-up chain, the way test_sim_pmw_cover drives it."""

import asyncio
from datetime import date, datetime, timezone

import pytest

from app.core.config import Settings
from app.options.chain import Chain, ExpiryInfo, LegQuote, StrikeRow
from app.options.models import SpreadTicket, TicketLeg
from app.options.occ import format_occ
from app.services.market_clock import ET
from app.trading.sim.options_service import SimOptionsService
from app.trading.sim.options_store import SimOptionsStore
from app.trading.sim.store import SimStore

TODAY = date(2026, 8, 12)
EXPIRY = date(2026, 9, 18)
NOW = datetime(2026, 8, 12, 14, 30, tzinfo=timezone.utc)
SPOT = 100.0
STRIKES = [80.0, 85.0, 90.0, 95.0, 100.0, 105.0, 110.0, 115.0, 120.0]


def _quote(symbol: str, kind: str, strike: float) -> LegQuote:
    # A crude but monotonic surface: intrinsic plus time value that fades
    # with distance from the money. Enough for a payoff and a net price.
    intrinsic = max(0.0, strike - SPOT) if kind == "put" else max(0.0, SPOT - strike)
    time_value = max(0.2, 5.0 - abs(strike - SPOT) * 0.25)
    mid = round(intrinsic + time_value, 2)
    return LegQuote(
        symbol=symbol, strike=strike, kind=kind, expiry=EXPIRY, bid=round(mid - 0.05, 2), ask=round(mid + 0.05, 2),
        mid=mid, last=mid, bid_size=5, ask_size=5, delta=0.3 if kind == "call" else -0.3, gamma=0.01,
        theta=-0.05, iv=0.25, open_interest=100, tradable=True,
    )


def _chain(expiry: date = EXPIRY) -> Chain:
    rows = []
    for strike in STRIKES:
        rows.append(
            StrikeRow(
                strike=strike,
                call=_quote(format_occ("XYZ", expiry, "call", strike), "call", strike),
                put=_quote(format_occ("XYZ", expiry, "put", strike), "put", strike),
            )
        )
    return Chain(underlying="XYZ", expiry=expiry, spot=SPOT, feed="sim", as_of=NOW, rows=rows)


class _Source:
    feed = "sim"
    as_of = None

    def now(self):
        return NOW.astimezone(ET)

    async def spot(self, underlying):
        return SPOT

    async def expiries(self, underlying):
        return SPOT, [ExpiryInfo(expiry=EXPIRY, dte=(EXPIRY - TODAY).days, contract_count=len(STRIKES) * 2)]

    async def chain(self, underlying, expiry):
        return _chain(expiry)

    async def leg_quotes(self, symbols):
        quotes = {}
        for row in _chain().rows:
            for quote in (row.call, row.put):
                if quote is not None and quote.symbol in symbols:
                    quotes[quote.symbol] = quote
        return quotes


class _SimService:
    async def account(self):
        return {"equity": "100000.00", "buying_power": "100000.00", "cash": "100000.00"}


@pytest.fixture
def service(tmp_path) -> SimOptionsService:
    db = str(tmp_path / "sim.sqlite3")
    sim = SimStore(db)
    options = SimOptionsStore(db)
    asyncio.run(sim.init_schema())
    asyncio.run(options.init_schema())
    asyncio.run(sim.ensure_account(3, 100_000.0))
    return SimOptionsService(
        None,  # type: ignore[arg-type]
        Settings(alpaca_api_key_id="k", alpaca_api_secret_key="s", trading_max_order_notional=1_000_000),
        sim_store=sim,
        options_store=options,
        user_id=3,
        source=_Source(),
        sim_service=_SimService(),  # type: ignore[arg-type]
    )


def _built(*legs: TicketLeg, qty: int = 1) -> SpreadTicket:
    return SpreadTicket(underlying="XYZ", strategy="custom", expiry=EXPIRY, qty=qty, legs=list(legs))


def test_a_built_credit_spread_reads_its_direction_off_the_market(service):
    """Nothing declares this a credit: selling the 105 and buying the 110
    brings money in, and the preview says so."""
    spread = asyncio.run(
        service.preview(_built(
            TicketLeg(kind="call", strike=105, side="sell"),
            TicketLeg(kind="call", strike=110, side="buy"),
        ))
    )
    assert spread.direction == "credit"
    assert spread.naked is False
    # Defined risk: the width less the credit, times the multiplier.
    assert spread.max_loss is not None and spread.collateral == pytest.approx(abs(spread.max_loss), abs=1.0)
    assert spread.collateral < 5 * 100  # never more than the width
    assert not any("the other way round" in w for w in spread.warnings)


def test_a_built_debit_spread_is_a_debit(service):
    spread = asyncio.run(
        service.preview(_built(
            TicketLeg(kind="call", strike=105, side="buy"),
            TicketLeg(kind="call", strike=110, side="sell"),
        ))
    )
    assert spread.direction == "debit"
    # What a debit spread ties up is what it can lose, which is what was
    # paid for it. (The collateral is priced at the mid, the limit the
    # simulation prefills is the natural, so they differ by the spread.)
    assert spread.max_loss is not None
    assert spread.collateral == pytest.approx(abs(spread.max_loss), abs=0.01)
    assert spread.collateral == pytest.approx(spread.net_mid * 100, abs=1.0)


def test_an_uncovered_short_says_so_and_uses_the_brokers_margin(service):
    spread = asyncio.run(service.preview(_built(TicketLeg(kind="call", strike=105, side="sell"))))
    assert spread.naked is True
    assert spread.max_loss is None  # no ceiling
    # Reg-T: a fifth of the underlying less what it is out of the money,
    # plus the premium -- far more than a spread's width.
    assert spread.collateral > 1_500
    assert any("Uncovered short leg" in w and "no uncovered short option" in w for w in spread.warnings)


def test_the_probabilities_come_back_and_bracket_sensibly(service):
    """A far out-of-the-money credit spread: likely to expire worthless,
    and likelier still to be touched on the way."""
    spread = asyncio.run(
        service.preview(_built(
            TicketLeg(kind="call", strike=115, side="sell"),
            TicketLeg(kind="call", strike=120, side="buy"),
        ))
    )
    assert spread.chance is not None and 0.0 < spread.chance < 1.0
    assert spread.touch is not None and 0.0 < spread.touch <= 1.0
    assert spread.touch_at == pytest.approx(spread.breakevens[0], abs=0.01)
    # The breakeven is reached more often than the position ends a loser.
    assert spread.touch > 1 - spread.chance - 0.05


def test_a_ratio_leaves_a_bare_short_and_the_named_shapes_still_work(service):
    ratio = asyncio.run(
        service.preview(_built(
            TicketLeg(kind="call", strike=105, side="sell", ratio=2),
            TicketLeg(kind="call", strike=110, side="buy"),
        ))
    )
    assert ratio.naked is True and ratio.max_loss is None

    named = asyncio.run(
        service.preview(SpreadTicket(underlying="XYZ", strategy="bull_put", expiry=EXPIRY, qty=1, long_strike=90, short_strike=95))
    )
    assert named.direction == "credit" and named.naked is False
    assert named.chance is not None


def test_an_uncovered_put_reports_the_floor_the_grid_cannot_reach(service):
    """The payoff grid stops a few standard deviations out, so its own
    minimum beside a naked put is an artefact of where it stopped. The
    real floor is the position with the underlying at nothing."""
    spread = asyncio.run(service.preview(_built(TicketLeg(kind="put", strike=95, side="sell"))))
    assert spread.naked is True
    assert spread.max_loss is not None
    # A 95 put sold for a few dollars: the floor is the strike less the
    # premium, times the multiplier -- thousands, not hundreds.
    assert spread.max_loss < -9_000
    assert spread.max_loss == pytest.approx(-(95 - spread.net_mid) * 100, abs=25)


def test_an_uncovered_call_has_no_floor_at_all(service):
    spread = asyncio.run(service.preview(_built(TicketLeg(kind="call", strike=105, side="sell"))))
    assert spread.naked is True and spread.max_loss is None
