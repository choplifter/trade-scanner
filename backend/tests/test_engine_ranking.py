"""The catalyst boost is wired into _rank_gainers only.

formulas.rank_score can apply the boost, but whether a given view *asks* it to
is the thing that actually decides ranking -- so these assert the wiring, not
the arithmetic (see test_formulas.py for that). Each case is built so the boost
would flip the order if it were applied, making its absence observable.
"""

from datetime import datetime, timezone

from app.scanners.engine import _rank_gainers, _rank_losers, _rank_most_active
from app.scanners.schemas import ScannerRow


class _StubNewsCache:
    def __init__(self, headlines: dict[str, str]):
        self._headlines = headlines

    def get(self, symbol: str) -> str | None:
        return self._headlines.get(symbol)


def _row(
    symbol: str,
    pct_change: float,
    volume_today: float = 1_000_000.0,
    rvol: float = 5.0,
    last_price: float = 10.0,
):
    return ScannerRow(
        symbol=symbol,
        last_price=last_price,
        prev_close=last_price,
        pct_change=pct_change,
        volume_today=volume_today,
        avg_vol_20d=200_000.0,
        rvol=rvol,
        # Derived, not fixed: most-active ranks on this, so a constant here
        # would make those cases tie and pass on stable-sort order alone.
        dollar_volume_today=volume_today * last_price,
        updated_at=datetime.now(timezone.utc),
    )


def _symbols(rows) -> list[str]:
    return [r.symbol for r in rows]


def test_gainers_apply_the_catalyst_boost():
    # BIG gaps 10%, CAT only 9% -- but 9 * 1.15 = 10.35, so the catalyst wins.
    rows = [_row("BIG", 10.0), _row("CAT", 9.0)]
    ranked = _rank_gainers(rows, _StubNewsCache({"CAT": "FDA approval"}))
    assert _symbols(ranked) == ["CAT", "BIG"]


def test_gainers_without_a_headline_rank_purely_on_magnitude():
    rows = [_row("BIG", 10.0), _row("SMALL", 9.0)]
    ranked = _rank_gainers(rows, _StubNewsCache({}))
    assert _symbols(ranked) == ["BIG", "SMALL"]


def test_losers_do_not_apply_the_catalyst_boost():
    # Mirror of the gainers case: -9 * 1.15 = -10.35 would sort CAT ahead of
    # BIG's -10 if the boost were applied. It must not be.
    rows = [_row("BIG", -10.0), _row("CAT", -9.0)]
    ranked = _rank_losers(rows, _StubNewsCache({"CAT": "Guidance cut"}))
    assert _symbols(ranked) == ["BIG", "CAT"]


def test_most_active_does_not_apply_the_catalyst_boost():
    # 90k * 1.15 = 103.5k would beat BIG's 100k if the boost were applied.
    rows = [_row("BIG", 5.0, volume_today=100_000.0), _row("CAT", 5.0, volume_today=90_000.0)]
    ranked = _rank_most_active(rows, _StubNewsCache({"CAT": "Earnings beat"}))
    assert _symbols(ranked) == ["BIG", "CAT"]


def test_losers_still_apply_the_fade_risk_discount():
    # HOT's -10 discounts to -7, which ranks *below* MILD's -8 ascending. The
    # discount is direction-agnostic, so switching the catalyst boost off for
    # this view must not have switched this off too.
    rows = [_row("HOT", -10.0, rvol=20.0), _row("MILD", -8.0, rvol=5.0)]
    ranked = _rank_losers(rows, _StubNewsCache({}))
    assert _symbols(ranked) == ["MILD", "HOT"]


def test_most_active_still_applies_the_fade_risk_discount():
    rows = [
        _row("HOT", 5.0, volume_today=100_000.0, rvol=20.0),
        _row("MILD", 5.0, volume_today=90_000.0, rvol=5.0),
    ]
    ranked = _rank_most_active(rows, _StubNewsCache({}))
    # 100k * 0.7 = 70k, below MILD's undiscounted 90k.
    assert _symbols(ranked) == ["MILD", "HOT"]


def test_roundup_headline_earns_no_boost_in_gainers():
    # A movers-roundup mention isn't a catalyst (see engine._has_headline), so
    # CAT stays behind BIG despite having a "headline".
    rows = [_row("BIG", 10.0), _row("CAT", 9.0)]
    ranked = _rank_gainers(
        rows, _StubNewsCache({"CAT": "12 Health Care Stocks Moving In Wednesday's Intraday Session"})
    )
    assert _symbols(ranked) == ["BIG", "CAT"]


def test_most_active_ranks_by_dollar_volume_not_share_volume():
    # PENNY trades 5x the shares but at 1/20th the price. Ranking on raw share
    # volume puts it first; ranking on dollar volume -- which is what survives
    # a partial IEX tape more usefully -- puts PRICEY first.
    rows = [
        _row("PENNY", 5.0, volume_today=1_000_000.0, last_price=1.0),  # $1M
        _row("PRICEY", 5.0, volume_today=200_000.0, last_price=20.0),  # $4M
    ]
    ranked = _rank_most_active(rows, _StubNewsCache({}))
    assert _symbols(ranked) == ["PRICEY", "PENNY"]
