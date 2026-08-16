"""Daily-bar-resolution historical backtest: replays past trading days
through the app's *actual* ranking functions (engine._rank_gainers/
_rank_losers/_rank_most_active, not a reimplementation) to reconstruct
what would have ranked in each of the three live views, then buckets
win-rate/avg-return with the same methodology already trusted for live
data (bucket_analysis.py).

Also checks each entry day's own candle shape via is_shaved_top (see
app.market_data.candle_shape) -- did the pick close at/near that day's
high, regardless of how it got there? Wick-shape analysis works on any
OHLC bar, daily included, so this doesn't need minute data the way the
momentum alarm's 15m % check does.

"most_active" is replayed here too, but it is not a like-for-like replay
the way gainers/losers are, for two reasons worth keeping in mind before
reading anything into its numbers:
- Tape mismatch. Daily bars carry *consolidated*-tape volume; the live
  scanner reads a partial IEX slice, which is exactly why that view ranks
  on dollar volume in the first place (see engine._rank_most_active). So
  this replays the same formula over a *better* tape than production
  sees -- the cohort it produces is not the cohort live ranking would
  have produced. Gainers/losers rank on gap %, a ratio that survives a
  partial tape, so they don't have this problem.
- Selection bias from the caller's symbol cap. Both callers pick the top
  N of the universe *by avg_dollar_vol_20d* (see _top_symbols in the Dash
  backtest page and backtest_report.py's own sort), so "rank by dollar
  volume" is being run over a set already pre-sorted by dollar volume.
  Measured at the CLI defaults (180 days, --max-symbols 300, 243 symbols
  with bars, 129 trading days): most_active drew on 62.6% of the pool
  (152 symbols), its 10 most frequent names held 19.9% of its picks, and
  16 names appeared on >=90% of all days. Gainers/losers over the same
  run drew on ~100% of the pool with a 7.6-7.9% top-10 share and *no*
  name on >=90% of days. So it's meaningfully concentrated -- roughly
  2.6x the top-10 share -- but not the degenerate handful-of-names case;
  read its sample as a narrower, repeat-heavy cohort rather than an
  independent one, and expect it to tighten further as N shrinks.

Deliberately out of scope for this pass:
- Catalyst/headline backtesting -- needs historical news data (a harder,
  separate phase), so every row here is ranked with has_headline=False
  (news_cache=None passed to the ranking functions). Note the catalyst
  boost is gainers-only now anyway (see formulas._CATALYST_BOOST), so on
  the other two views this costs nothing.
- Float. Each live appearance records entry_float_shares (see
  history_store.py) so a float-based ranking rule can eventually be
  validated, but that can't be reconstructed here: FMP's bulk file is
  *today's* float, and applying it to past dates is look-ahead bias --
  plus float is unreliable around a reverse split. A float rule
  "validated" against it would look measured without being measured.
- Time-of-day-normalized RVOL (settings.scanner_rvol_time_normalized).
  Daily bars are whole sessions, so the session fraction is 1.0 and the
  normalized and un-normalized definitions are identical here -- this
  tool cannot re-derive formulas._FADE_RISK_RVOL for the normalized
  definition no matter how much history it's given. That needs an
  intraday replay.
- The momentum alarm itself (15m % + shaved top/bottom) -- 15m % is
  inherently a minute-resolution concept (a trailing-15-*minute* delta),
  so it can't be reconstructed from daily bars at all, unlike
  is_shaved_top above (checked here without any 15m % gate). Would need
  historical minute bars, far more data volume than a daily-bar pass.
- Point-in-time universe reconstruction -- this backtests today's live
  universe membership against past dates, which has survivorship bias
  (a stock that doesn't qualify for today's universe might have
  legitimately qualified months ago). Accepted, not solved, here.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from statistics import mean, median

from app.alpaca.client import AlpacaClients
from app.core.config import Settings
from app.market_data.bars import get_daily_bars_multi
from app.market_data.candle_shape import is_shaved_top
from app.scanners import bucket_analysis, formulas, screener
from app.scanners.engine import _rank_gainers, _rank_losers, _rank_most_active, _tradable

# Trading days of trailing volume history needed before a day can get an
# avg_vol_20d (and therefore an RVOL) computed from the bars themselves --
# never from UniverseSymbol.avg_vol_20d, which is today's live figure and
# not valid for a past date.
_WARMUP_TRADING_DAYS = 20
# Calendar-day padding requested on top of the caller's lookback_days so
# the earliest evaluated day still has a full 20 *trading* days behind it
# -- 20 trading days can span ~28-30+ calendar days across weekends/
# holidays, so this pads generously rather than exactly.
_WARMUP_CALENDAR_PADDING_DAYS = 40
# get_daily_bars_multi doesn't chunk symbols itself -- mirrors
# app.alpaca.universe._BATCH_SIZE's precedent for the same reason.
_FETCH_BATCH_SIZE = 500
# Matches ScannerEngine.benchmark_symbol, so a backtested alpha and a live
# alpha are measured against the same thing.
_BENCHMARK_SYMBOL = "SPY"

logger = logging.getLogger(__name__)


# Screener fields a daily bar can honestly reconstruct, so a user-built
# screen can be replayed over history (see simulate_from_bars' `screen`).
#
# Everything absent from this set is absent for a reason, not an oversight:
#   spread_pct  -- needs historical NBBO quotes, which nothing here fetches.
#   volume_1h / volume_surge / rvol_1h -- intraday by definition; a daily bar
#       is one number for the whole session. Same wall that stops this tool
#       re-deriving the RVOL fade-risk threshold.
#   is_stale    -- describes live feed freshness, meaningless for a past date.
#   float_shares -- FMP's bulk file is *today's* float; applying it to past
#       dates is look-ahead bias, and float is unreliable around a reverse
#       split. Already out of scope for the same reason elsewhere here.
#
# screener.apply_filters deliberately *ignores* unknown fields so a saved
# screen naming a removed one still runs live. That behaviour is wrong here:
# silently dropping half a screen's criteria would produce a plausible number
# for a strategy nobody described. unsupported_filters() is what catches it.
BACKTESTABLE_FIELDS = frozenset(
    {
        "symbol",
        "last_price",
        "pct_change",
        "volume_today",
        "avg_vol_20d",
        "rvol",
        "dollar_volume_today",
        "day_high",
        "day_low",
        "is_hod",
        "is_lod",
        "is_fade_risk",
        "rank_score",
    }
)


# At 5-minute resolution the three intraday volume fields become
# reconstructible -- see app.scanners.intraday_backtest, which rebuilds each
# symbol's state as of every bar. The other three stay out at any resolution:
# spread needs historical quotes nothing fetches, float would be today's value
# on a past date, and is_stale describes a live feed.
INTRADAY_BACKTESTABLE_FIELDS = BACKTESTABLE_FIELDS | {"volume_1h", "volume_surge", "rvol_1h"}

RESOLUTIONS = ("daily", "intraday")


def supported_fields(resolution: str = "daily") -> frozenset:
    return INTRADAY_BACKTESTABLE_FIELDS if resolution == "intraday" else BACKTESTABLE_FIELDS


def unsupported_filters(screen, resolution: str = "daily") -> list[str]:
    """Field names in `screen` that this resolution can't reconstruct --
    filters and sort key alike. Empty means the screen is fully replayable.
    """
    supported = supported_fields(resolution)
    names = [f.field for f in screen.filters] + [screen.sort_by]
    return sorted({n for n in names if n not in supported})


def intraday_would_help(screen) -> list[str]:
    """Fields the daily path rejects that intraday resolution would accept --
    so a refusal can tell the user to switch rather than to delete filters
    they deliberately built a screen around.
    """
    return sorted(set(unsupported_filters(screen, "daily")) - set(unsupported_filters(screen, "intraday")))


@dataclass
class _BacktestRow:
    """Just enough fields for engine._tradable/_rank_gainers/_rank_losers,
    formulas.rank_score and screener.run_screen to work -- those functions
    are duck-typed over any object with these attributes, not tied to the
    full ScannerRow pydantic model (which would need fabricating unused
    fields like updated_at for no benefit here).

    The day_high/day_low/is_hod/is_lod/avg_vol_20d fields exist only so a
    user screen can filter on them; ranking itself never reads them. Note
    is_hod/is_lod mean something weaker here than live: from a daily bar all
    that's knowable is "closed at its high", not "touched the high at this
    moment", which is what the live flag tracks.
    """

    symbol: str
    last_price: float
    pct_change: float
    volume_today: float
    avg_vol_20d: float
    dollar_volume_today: float
    rvol: float
    day_high: float
    day_low: float
    is_hod: bool
    is_lod: bool
    is_fade_risk: bool
    is_shaved_top: bool


def _ranked_views(day_rows, min_dollar_volume: float, screen):
    """(view_name, rows) pairs for one trading day.

    With no screen this is the three live views, which is what the daily
    backtest has always replayed. With one, it's that screen instead -- a
    single "screen" view -- because the question being asked is "how did
    *this* filter set do", and mixing it with the fixed views would put
    unrelated picks in the same buckets.

    The screen path applies engine._tradable first, exactly as the live
    screener does (see screener_service.screen_live_rows), so a backtested
    screen and a live one start from the same eligible set.
    """
    if screen is None:
        return (
            ("gainers", _rank_gainers(day_rows, None, min_dollar_volume)),
            ("losers", _rank_losers(day_rows, None, min_dollar_volume)),
            ("most_active", _rank_most_active(day_rows, None, min_dollar_volume)),
        )

    tradable = _tradable(day_rows, min_dollar_volume)
    # rank_score is a derived field live (it needs the news cache); here
    # has_headline is always False -- there's no historical news -- so it
    # reduces to magnitude with the fade-risk discount, and is supplied the
    # same way the live path supplies it.
    derived = {
        "rank_score": {
            r.symbol: formulas.rank_score(r.pct_change, False, r.rvol, catalyst_boost=r.pct_change > 0)
            for r in tradable
        }
    }
    return (("screen", screener.run_screen(tradable, screen, derived).rows),)


def benchmark_returns_by_date(
    benchmark_bars: list, horizon_days: int
) -> dict[date, float]:
    """{trading_date: the benchmark's own % change over the same
    horizon_days forward window} -- the yardstick a pick's raw return has to
    be measured against.

    Indexed off the benchmark's own series rather than a calendar offset:
    SPY trades every session, so its bar index *is* the canonical
    trading-day sequence, which is the same thing horizon_days counts in
    each symbol's series.
    """
    returns: dict[date, float] = {}
    for i in range(len(benchmark_bars) - horizon_days):
        entry = benchmark_bars[i]
        if entry.close <= 0:
            continue
        exit_close = benchmark_bars[i + horizon_days].close
        returns[entry.timestamp.date()] = (exit_close - entry.close) / entry.close * 100
    return returns


def alpha_by_view(picks: list[dict], views: tuple[str, ...] = bucket_analysis.VIEWS) -> list[dict]:
    """Per view: how often a pick beat the benchmark, next to how often it
    merely closed positive.

    This is the metric the daily backtest was missing entirely. A raw win
    rate answers "did the price go up", which on a broadly green day every
    long does -- it says nothing about whether *this scanner* added
    anything. The live store has measured alpha since it was built (see
    history_store's alpha_vs_benchmark), so without this the historical and
    live views of "did it work" were answering different questions.

    Kept here rather than folded into bucket_analysis.bucket_stats on
    purpose: that helper is shared with momentum_backtest, whose picks have
    no benchmark attached, so adding an alpha term there would either break
    it or force a benchmark concept into a module that has no use for one.
    """
    rows = []
    for view_name in views:
        view_picks = [p for p in picks if p["view"] == view_name]
        with_alpha = [p for p in view_picks if p["alpha_vs_benchmark"] is not None]
        if not view_picks:
            continue
        alpha_wins = sum(1 for p in with_alpha if p["alpha_vs_benchmark"] > 0)
        raw_wins = sum(1 for p in view_picks if p["pct_change_since_entry"] > 0)
        alphas = [p["alpha_vs_benchmark"] for p in with_alpha]
        rows.append(
            {
                "view": view_name,
                "sample_size": len(view_picks),
                "sample_size_with_benchmark": len(with_alpha),
                "win_rate": round(raw_wins / len(view_picks) * 100, 1),
                "alpha_win_rate": round(alpha_wins / len(with_alpha) * 100, 1) if with_alpha else None,
                "avg_alpha": round(sum(alphas) / len(alphas), 2) if alphas else None,
                # Median for the same reason the RVOL replay reports one:
                # these returns are fat-tailed enough that a mean describes
                # its outliers rather than its population.
                "median_alpha": round(median(alphas), 2) if alphas else None,
                "sufficient_sample": len(with_alpha) >= bucket_analysis.MIN_SAMPLE_SIZE,
            }
        )
    return rows


def simulate_from_bars(
    bars_by_symbol: dict[str, list],
    min_dollar_volume: float,
    horizon_days: int = 1,
    benchmark_returns: dict[date, float] | None = None,
    screen=None,
) -> list[dict]:
    """Pure, network-free -- reconstructs each historical trading day's
    gainers/losers/most-active exactly as engine._rank_gainers/_rank_losers/
    _rank_most_active would have ranked it live, using only that day's
    trailing-20-bar history.

    A symbol can legitimately come out in two views on the same date (any
    most-active name that also gapped), which is the same thing the live
    history store records -- appearances are keyed per (symbol, view,
    date), not per symbol. Every consumer here groups by view before
    aggregating, so that's a duplicate row, not a double-count.

    Rows are grouped by each bar's own `.timestamp.date()`, not by list
    index: two symbols' bar lists aren't guaranteed to line up index-for-
    index (a newer listing starts later, a halt leaves a gap), so grouping
    by index would silently rank a symbol against the wrong day's cohort
    the moment any symbol's history isn't perfectly continuous. The
    trailing-RVOL window and the forward-horizon outcome stay index-based
    *within one symbol's own series* -- those are inherently per-symbol,
    trading-day-count concepts -- only the cross-sectional ranking cohort
    is grouped by date.

    `bars_by_symbol` values are Bar-like: need `.close`, `.volume`,
    `.timestamp` (a datetime). Oldest-first per symbol, as
    get_daily_bars_multi returns them.
    """
    rows_by_date: dict[date, list[_BacktestRow]] = defaultdict(list)
    exit_price_by_symbol_date: dict[tuple[str, date], float] = {}

    for symbol, bars in bars_by_symbol.items():
        if len(bars) < _WARMUP_TRADING_DAYS + horizon_days + 1:
            continue
        for i in range(_WARMUP_TRADING_DAYS, len(bars) - horizon_days):
            bar = bars[i]
            prev_close = bars[i - 1].close
            pct = formulas.pct_change(bar.close, prev_close)
            if pct is None:
                continue
            avg_vol_20d = mean(b.volume for b in bars[i - _WARMUP_TRADING_DAYS : i])
            row_rvol = formulas.rvol(bar.volume, avg_vol_20d) or 0.0
            trading_date = bar.timestamp.date()
            rows_by_date[trading_date].append(
                _BacktestRow(
                    symbol=symbol,
                    last_price=bar.close,
                    pct_change=pct,
                    volume_today=bar.volume,
                    avg_vol_20d=avg_vol_20d,
                    dollar_volume_today=formulas.dollar_volume(bar.volume, bar.close),
                    rvol=row_rvol,
                    day_high=bar.high,
                    day_low=bar.low,
                    is_hod=formulas.is_hod(bar.close, bar.high),
                    is_lod=formulas.is_lod(bar.close, bar.low),
                    is_fade_risk=formulas.is_fade_risk(row_rvol),
                    is_shaved_top=is_shaved_top(bar.open, bar.high, bar.low, bar.close),
                )
            )
            exit_price_by_symbol_date[(symbol, trading_date)] = bars[i + horizon_days].close

    picks: list[dict] = []
    for trading_date, day_rows in rows_by_date.items():
        for view_name, ranked in _ranked_views(day_rows, min_dollar_volume, screen):
            for row in ranked:
                exit_price = exit_price_by_symbol_date.get((row.symbol, trading_date))
                if exit_price is None or row.last_price <= 0:
                    continue
                pct_since_entry = (exit_price - row.last_price) / row.last_price * 100
                # None rather than 0.0 when the benchmark has no bar for this
                # date -- a missing yardstick has to stay distinguishable from
                # a flat one, or every unmeasurable pick would silently count
                # as exactly matching the market.
                benchmark_pct = (
                    benchmark_returns.get(trading_date) if benchmark_returns else None
                )
                picks.append(
                    {
                        "symbol": row.symbol,
                        "trading_date": trading_date.isoformat(),
                        "view": view_name,
                        "entry_pct_change": row.pct_change,
                        "entry_rvol": row.rvol,
                        # The magnitude "most_active" actually ranks on --
                        # carried on every pick regardless of view so a
                        # most-active row has a column explaining why it's
                        # there at all (its gap % often being unremarkable).
                        "entry_dollar_volume": row.dollar_volume_today,
                        "is_shaved_top": row.is_shaved_top,
                        "pct_change_since_entry": pct_since_entry,
                        # Named to match history_store's own fields so a
                        # historical read and a live read of "did this work"
                        # are the same measurement, not two similar ones.
                        "benchmark_pct_change_since_entry": benchmark_pct,
                        "alpha_vs_benchmark": (
                            pct_since_entry - benchmark_pct if benchmark_pct is not None else None
                        ),
                    }
                )
    return picks


def active_views(picks: list[dict], screen=None) -> tuple[str, ...]:
    """Which view names the aggregations should break down by.

    A screen run produces a single "screen" view, and every breakdown here
    iterates a fixed view list -- so without this, a screened backtest's
    picks would be silently dropped from every table it produced.
    """
    if screen is not None:
        return ("screen",)
    return bucket_analysis.VIEWS


def fade_risk_by_view(picks: list[dict], views: tuple[str, ...] = bucket_analysis.VIEWS) -> list[dict]:
    """Win rate/avg return above vs. at-or-below formulas._FADE_RISK_RVOL,
    broken down per view and never pooled -- the same shape (and the same
    reasoning) as history_store._fade_risk_drift, so the historical and the
    live read of the fade-risk discount stay directly comparable.

    Pooling would be wrong for exactly the reason bucket_analysis.VIEWS
    gives: "win = positive return" means the opposite thing on losers, so a
    pooled win rate averages contradictory signals. The discount itself is
    direction-agnostic and applies in all three views (see
    formulas.rank_score) -- it's the *measurement* that has to be per view,
    not the multiplier.

    Pure and separate from run_backtest so it can be tested without a
    network round-trip.
    """
    rows = []
    for view_name in views:
        view_picks = [p for p in picks if p["view"] == view_name]
        above = [p for p in view_picks if p["entry_rvol"] > formulas._FADE_RISK_RVOL]
        at_or_below = [p for p in view_picks if p["entry_rvol"] <= formulas._FADE_RISK_RVOL]
        rows.append(
            {
                "view": view_name,
                "rvol_above_threshold": bucket_analysis.bucket_stats(above),
                "rvol_at_or_below_threshold": bucket_analysis.bucket_stats(at_or_below),
                "sufficient_sample": len(above) >= bucket_analysis.MIN_SAMPLE_SIZE,
            }
        )
    return rows


async def run_backtest(
    clients: AlpacaClients,
    settings: Settings,
    symbols: list[str],
    lookback_days: int = 180,
    horizon_days: int = 1,
    batch_size: int = _FETCH_BATCH_SIZE,
    screen=None,
) -> dict:
    """Fetch/orchestration wrapper: chunks symbols (get_daily_bars_multi
    doesn't chunk itself), merges results, replays via simulate_from_bars,
    then buckets with the same bucket_analysis primitives history_store.py
    uses for the live drift report -- so a backtest report and a live
    drift report measure win rate/avg return identically.
    """
    fetch_lookback = lookback_days + _WARMUP_CALENDAR_PADDING_DAYS
    bars_by_symbol: dict[str, list] = {}
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i : i + batch_size]
        bars_by_symbol.update(await get_daily_bars_multi(clients, batch, lookback_days=fetch_lookback))

    # Its own fetch, not folded into the batches above: the benchmark sits
    # outside the universe's $1-$50 price filter and isn't a scanner row --
    # the same reason ScannerEngine._poll_benchmark exists separately.
    benchmark_bars = (await get_daily_bars_multi(clients, [_BENCHMARK_SYMBOL], lookback_days=fetch_lookback)).get(
        _BENCHMARK_SYMBOL
    ) or []
    benchmark_returns = benchmark_returns_by_date(benchmark_bars, horizon_days)
    if not benchmark_returns:
        logger.warning(
            "No %s bars -- alpha will be unavailable and every win rate here stays raw",
            _BENCHMARK_SYMBOL,
        )

    picks = simulate_from_bars(
        bars_by_symbol, settings.scanner_min_dollar_volume, horizon_days, benchmark_returns, screen
    )

    # Every breakdown iterates a fixed view list, so a screened run -- which
    # produces one "screen" view -- has to say so or its picks vanish from
    # all of them. Without a screen this is exactly bucket_analysis.VIEWS.
    views = active_views(picks, screen)

    gap_buckets = bucket_analysis.bucket_breakdown(
        picks, lambda p: abs(p["entry_pct_change"]), bucket_analysis.GAP_BUCKETS, views=views
    )
    rvol_buckets = bucket_analysis.bucket_breakdown(
        picks, lambda p: p["entry_rvol"], bucket_analysis.RVOL_BUCKETS, views=views
    )

    shaved_top: list[dict] = []
    for view_name in views:
        view_picks = [p for p in picks if p["view"] == view_name]
        shaved = [p for p in view_picks if p["is_shaved_top"]]
        not_shaved = [p for p in view_picks if not p["is_shaved_top"]]
        shaved_top.append(
            {
                "view": view_name,
                "shaved_top": bucket_analysis.bucket_stats(shaved),
                "not_shaved_top": bucket_analysis.bucket_stats(not_shaved),
                "sufficient_sample": (
                    len(shaved) >= bucket_analysis.MIN_SAMPLE_SIZE
                    and len(not_shaved) >= bucket_analysis.MIN_SAMPLE_SIZE
                ),
            }
        )

    return {
        "symbol_count": len(symbols),
        "symbols_with_bars": len(bars_by_symbol),
        "lookback_days": lookback_days,
        "horizon_days": horizon_days,
        "sample_size": len(picks),
        "gap_buckets": gap_buckets,
        "rvol_buckets": rvol_buckets,
        "shaved_top": shaved_top,
        "alpha": alpha_by_view(picks, views),
        "benchmark_symbol": _BENCHMARK_SYMBOL,
        # What was actually replayed: the three fixed views, or one screen.
        # A caller rendering these tables shouldn't have to infer it.
        "views": list(views),
        "screened": screen is not None,
        "fade_risk": {
            "threshold": formulas._FADE_RISK_RVOL,
            "views": fade_risk_by_view(picks, views),
        },
        "min_sample_size": bucket_analysis.MIN_SAMPLE_SIZE,
        # Raw per-pick rows, alongside the buckets above -- callers that
        # want to list individual wins/losses (see the Dash backtest page)
        # can use this directly instead of re-deriving it from bars.
        "picks": picks,
    }
