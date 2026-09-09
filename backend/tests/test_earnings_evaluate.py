"""One reporter end to end: the signals gathered, the families scored,
the picked ones priced -- and what happens when a piece is missing."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timezone

import pytest

from app.market_data.earnings import EarningsDate
from app.market_data.earnings_screen import clear_facts_cache
from app.options.earnings_evaluate import choose_horizon, evaluate_symbol, wing_counts
from app.options.chain import ExpiryInfo
from app.services.market_clock import ET
from tests.test_options_optimize_endpoint import FAR, NEAR, NOW, TODAY, _Service

REPORT = date(2026, 9, 4)


@dataclass
class _Bar:
    timestamp: datetime
    close: float
    high: float
    low: float
    volume: float = 1_000_000.0


def _bars(days: dict[str, float]) -> list[_Bar]:
    # Alpaca stamps a daily bar at midnight ET of its session -- 04:00 UTC
    # in summer, 05:00 in winter. Building it in ET rather than hardcoding
    # an offset keeps a March session on its own day (a 04:00 UTC stamp in
    # EST is 23:00 the evening before, which would shift the whole fixture).
    return [
        _Bar(
            datetime.fromisoformat(d).replace(tzinfo=ET).astimezone(timezone.utc),
            c,
            c * 1.01,
            c * 0.99,
        )
        for d, c in days.items()
    ]


# Two past reports, each a 4 % move -- against a ~7 % implied move that is
# a market charging well over what the stock has delivered.
PAST_CLOSES = {
    "2026-03-03": 100.0,
    "2026-03-05": 104.0,
    "2026-06-02": 100.0,
    "2026-06-04": 96.0,
}


class _Calendar:
    configured = True

    def __init__(self, dates=(date(2026, 3, 4), date(2026, 6, 3), REPORT)):
        self.dates = list(dates)

    async def report_dates(self, symbol):
        return list(self.dates)

    async def next_earnings(self, symbol):
        upcoming = [d for d in self.dates if d >= TODAY]
        return EarningsDate(symbol, upcoming[0], (upcoming[0] - TODAY).days) if upcoming else None


class _IvStore:
    def __init__(self, rank=None, samples=0):
        self._rank = rank
        self._samples = samples
        self.recorded: list = []

    async def rank(self, symbol, current):
        return self._rank, self._samples

    async def record(self, symbol, session_date, atm_iv, dte):
        self.recorded.append((symbol, session_date, atm_iv, dte))


class _GexCache:
    def __init__(self, reading=None):
        self._reading = reading

    async def reading(self, symbol):
        return self._reading


@dataclass
class _Conditions:
    level: str = "green"
    reasons: tuple = ("VIX calm",)


def _evaluate(monkeypatch, service=None, **kw):
    import app.ai.options_context as context_module
    import app.market_data.bars as bars_module

    async def fake_daily(clients, symbols, lookback_days=14):
        return {s: _bars(PAST_CLOSES) for s in symbols}

    async def fake_minute(clients, symbols):
        return {}

    async def fake_historical(clients, symbol, timeframe):
        return []

    monkeypatch.setattr(bars_module, "get_daily_bars_multi", fake_daily)
    monkeypatch.setattr(context_module, "get_daily_bars_multi", fake_daily)
    monkeypatch.setattr(context_module, "get_intraday_minute_bars_multi", fake_minute)
    monkeypatch.setattr(context_module, "get_historical_bars", fake_historical)
    clear_facts_cache()

    params = {
        "gex_cache": _GexCache(),
        "iv_store": _IvStore(),
        "earnings_calendar": _Calendar(),
        "market_conditions": _Conditions(),
        "today": TODAY,
        "now": NOW,
    }
    params.update(kw)
    return asyncio.run(evaluate_symbol(service or _Service(), object(), "X", **params))


# --- the pure pieces -------------------------------------------------------------


def test_the_horizon_is_the_first_expiry_that_survives_the_report():
    infos = [
        ExpiryInfo(expiry=date(2026, 9, 1), dte=-3, contract_count=10),
        ExpiryInfo(expiry=date(2026, 9, 11), dte=7, contract_count=10),
        ExpiryInfo(expiry=date(2026, 9, 25), dte=21, contract_count=10),
    ]
    assert choose_horizon(infos, date(2026, 9, 9), TODAY) == date(2026, 9, 11)
    assert choose_horizon(infos, date(2026, 9, 20), TODAY) == date(2026, 9, 25)
    # Nothing listed past the report: the last expiry, rather than nothing.
    assert choose_horizon(infos, date(2026, 12, 1), TODAY) == date(2026, 9, 25)
    assert choose_horizon(infos, None, TODAY) == date(2026, 9, 11)
    assert choose_horizon([], date(2026, 9, 9), TODAY) is None


def test_wing_counts_only_count_strikes_beyond_the_move_that_are_quoted():
    from app.options.chain import Chain, StrikeRow
    from tests.test_options_optimize_endpoint import _quote

    import dataclasses

    def row(strike, *, put=True, call=True, oi=800):
        p = dataclasses.replace(_quote("put", strike, NEAR), open_interest=oi) if put else None
        c = dataclasses.replace(_quote("call", strike, NEAR), open_interest=oi) if call else None
        return StrikeRow(strike=float(strike), call=c, put=p)

    chain = Chain(
        underlying="X",
        expiry=NEAR,
        spot=100.0,
        feed="opra",
        as_of=NOW,
        rows=[row(90), row(92, put=False, call=False), row(100), row(108, put=False), row(112, put=False)],
    )
    assert wing_counts(chain, 100.0, 0.05) == (1, 2)
    assert wing_counts(chain, 100.0, 0.0) == (0, 0)
    assert wing_counts(None, 100.0, 0.05) == (0, 0)

    # A strike nobody holds is not a wing you can buy.
    illiquid = Chain(underlying="X", expiry=NEAR, spot=100.0, feed="opra", as_of=NOW, rows=[row(90, oi=1), row(112, oi=1)])
    assert wing_counts(illiquid, 100.0, 0.05) == (0, 0)


# --- the whole evaluation ---------------------------------------------------------


def test_a_rich_print_scores_the_neutral_families_and_prices_them(monkeypatch):
    body = _evaluate(monkeypatch)

    signals = body["signals"]
    assert body["horizon_expiry"] == NEAR.isoformat() and body["back_expiry"] == FAR.isoformat()
    assert body["report_date"] == REPORT.isoformat()
    assert signals["implied_move_pct"] == pytest.approx(7.2, abs=0.2)
    assert (signals["hist_median_pct"], signals["samples"]) == (4.0, 2)
    assert signals["move_ratio"] == pytest.approx(1.8, abs=0.1)
    # The chain the stub serves is quoted on both sides all the way out.
    assert signals["wing_room"] is True

    by_family = {s["family"]: s for s in body["scores"]}
    assert by_family["iron_condor"]["score"] == 2
    assert "past reports" in by_family["iron_condor"]["reasons"][0]
    assert by_family["long_straddle"]["score"] < 0

    assert "iron_condor" in body["picks"]
    assert all(by_family[f]["priced"] for f in body["picks"])
    neutral = body["optimizer"]["neutral"]
    assert "error" not in neutral
    assert neutral["results"], "the picked families should price into at least one structure"
    assert {r["strategy"] for r in neutral["results"]} <= set(body["picks"])
    # horizon_only: every leg expires on the horizon, so nothing later was read.
    assert neutral["horizon"]["expiries_considered"] == [NEAR.isoformat()]


def test_the_calendar_and_the_directional_families_come_back_unpriced_with_a_way_on(monkeypatch):
    body = _evaluate(monkeypatch)
    by_family = {s["family"]: s for s in body["scores"]}

    calendar = by_family["calendar"]
    assert calendar["priced"] is False and "will not survive it" in calendar["not_priced_because"]
    assert calendar["open_optimizer"]["strategies"] == ["calendar"]
    assert calendar["open_optimizer"]["horizon_expiry"] == FAR.isoformat()

    bull = by_family["bull_call"]
    assert bull["priced"] is False and bull["open_optimizer"]["outlook"] == "bullish"
    assert by_family["bear_put"]["open_optimizer"]["outlook"] == "bearish"
    assert "calendar" not in body["picks"] and "bull_call" not in body["picks"]


def test_the_target_is_the_typical_past_move_not_the_implied_one(monkeypatch):
    body = _evaluate(monkeypatch)
    assert body["target"]["neutral"]["move"] == pytest.approx(0.04)
    assert body["target"]["neutral"]["basis"] == "median past report move"
    target = body["optimizer"]["neutral"]["target"]
    assert (target["low"], target["high"]) == (96.0, 104.0)


def test_missing_pieces_are_reported_as_unknown_rather_than_scored(monkeypatch):
    body = _evaluate(monkeypatch, gex_cache=_GexCache(None), iv_store=_IvStore())
    signals = body["signals"]
    assert signals["gex_regime"] is None and signals["iv_rank_pct"] is None
    said = " ".join(body["warnings"])
    assert "unknown, not neutral" in said and "unknown, not unremarkable" in said

    by_family = {s["family"]: s["reasons"] for s in body["scores"]}
    assert not any("gamma" in r for r in by_family["iron_condor"])


def test_todays_reading_is_recorded_so_a_rank_can_exist_later(monkeypatch):
    store = _IvStore()
    _evaluate(monkeypatch, iv_store=store)
    assert store.recorded and store.recorded[0][0] == "X"
    assert store.recorded[0][1] == TODAY


def test_a_red_tape_marks_short_premium_down_and_says_so(monkeypatch):
    body = _evaluate(monkeypatch, market_conditions=_Conditions(level="red", reasons=("VIX 31",)))
    by_family = {s["family"]: s["score"] for s in body["scores"]}

    assert by_family["iron_condor"] == 0  # +2 for the rich print, -2 for the tape
    assert body["market"] == {"level": "red", "reasons": ["VIX 31"]}
    assert any("red" in w for w in body["warnings"])
    assert len(body["picks"]) <= 2


def test_a_pricing_failure_leaves_the_signals_and_scores_standing(monkeypatch):
    from app.trading.errors import OrderRejected

    service = _Service(preview_error=OrderRejected("options level too low", field="strategy"))
    body = _evaluate(monkeypatch, service=service)

    assert body["signals"]["move_ratio"] is not None
    assert body["scores"] and body["picks"]
    neutral = body["optimizer"]["neutral"]
    # Every finalist was rejected at preview, so there are no results --
    # but the run itself answered rather than failing the evaluation.
    assert neutral["results"] == []
    assert neutral["rejected"] and "options level too low" in neutral["rejected"][0]["rejected_because"]


def test_a_symbol_with_no_price_is_a_rejection_not_an_empty_answer(monkeypatch):
    from app.trading.errors import OrderRejected

    class _NoPrice(_Service):
        async def spot(self, underlying):
            return None

    with pytest.raises(OrderRejected):
        _evaluate(monkeypatch, service=_NoPrice())
