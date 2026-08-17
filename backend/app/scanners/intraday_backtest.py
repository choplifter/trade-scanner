"""Replay a user-built screen at 5-minute resolution.

The daily backtest (app.scanners.backtest) refuses screens touching
volume_1h / volume_surge / rvol_1h, because a daily bar is one number for a
whole session and those fields are rates measured inside it. This module is
the answer to that refusal: it reconstructs each symbol's state *as of every
5-minute bar* -- cumulative volume so far, running session high/low, the
trailing-hour volume rate -- and applies the screen to that, so the whole
screen backtests as one coherent thing rather than only its daily-safe half.

Evaluation is cross-sectional per timestamp, matching how the live screener
works: every symbol's row at 14:35 is screened together, so `sort_by` and
`limit` mean what they mean live. Screening each symbol independently would
quietly turn "top 50 by rank score" into "every symbol that ever qualified".

**Every qualifying bar is a pick.** That is the requested behaviour and it is
not the conservative one: a surge holds a condition true for several
consecutive bars, and those bars are nearly identical, so one event can
contribute a dozen highly-correlated samples. `sample_size` therefore
overstates how much independent evidence there is. `distinct_symbol_days` is
reported next to it for exactly that reason -- the ratio between them is how
much replication is in the number, and a large gap means the sample is
smaller than it looks. See momentum_backtest.simulate_momentum_alerts for the
rising-edge alternative that avoids it.

Still inherited from the daily tool, and still true here: today's universe is
applied to past dates (survivorship bias), and there is no historical news,
so rank_score reduces to magnitude with the fade-risk discount.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.market_data.bars import MOMENTUM_BAR_MINUTES
from app.market_data.candle_shape import is_shaved_top
from app.market_data.momentum import is_regular_session_bar
from app.market_data.volume_profile import fraction_at
from app.scanners import formulas, screener
from app.scanners.engine import _tradable
from app.services.market_clock import ET

logger = logging.getLogger(__name__)

_BAR_LENGTH = timedelta(minutes=MOMENTUM_BAR_MINUTES)


@dataclass
class _IntradayRow:
    """A symbol's state as of one 5-minute bar, shaped for screener.run_screen.

    Mirrors _BacktestRow's duck-typed approach, with the intraday-only fields
    added. Note volume_today/day_high/day_low are *session-to-date* values,
    not the finished day's -- that's the whole point: at 11:00 the screen must
    see what was knowable at 11:00, not the closing figures.
    """

    symbol: str
    last_price: float
    pct_change: float
    gap_pct: float | None
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
    volume_1h: float | None
    volume_surge: float | None
    rvol_1h: float | None


def previous_closes(daily_bars_by_symbol: dict[str, list]) -> dict[str, dict[date, float]]:
    """{symbol: {trading_date: the *previous* session's close}} -- the
    denominator for an intraday gap %, so it matches what the live scanner
    computes against.
    """
    result: dict[str, dict[date, float]] = {}
    for symbol, bars in daily_bars_by_symbol.items():
        per_date: dict[date, float] = {}
        for i in range(1, len(bars)):
            if bars[i - 1].close > 0:
                per_date[bars[i].timestamp.astimezone(ET).date()] = bars[i - 1].close
        result[symbol] = per_date
    return result


def _session_days(bars: list) -> dict[date, list]:
    days: dict[date, list] = defaultdict(list)
    for bar in bars:
        if is_regular_session_bar(bar):
            days[bar.timestamp.astimezone(ET).date()].append(bar)
    for day_bars in days.values():
        day_bars.sort(key=lambda b: b.timestamp)
    return days


def _window_bars(window: timedelta) -> int:
    return max(1, int(window / _BAR_LENGTH))


def build_rows_by_timestamp(
    bars_by_symbol: dict[str, list],
    avg_vol_by_symbol_date: dict[str, dict[date, float]],
    prev_close_by_symbol_date: dict[str, dict[date, float]],
    curve: list,
    window: timedelta,
) -> tuple[dict[datetime, list[_IntradayRow]], dict[tuple[str, date], float]]:
    """({bar_timestamp: [row, ...]}, {(symbol, date): that session's close}).

    Walks each symbol's session once and carries running totals forward, with
    prefix sums for the trailing windows -- recomputing a 12-bar sum at every
    one of ~78 bars per symbol per day would make this quadratic for no
    reason.
    """
    rows_by_ts: dict[datetime, list[_IntradayRow]] = defaultdict(list)
    exit_price: dict[tuple[str, date], float] = {}
    bars_per_window = _window_bars(window)

    for symbol, bars in bars_by_symbol.items():
        avg_by_date = avg_vol_by_symbol_date.get(symbol) or {}
        prev_by_date = prev_close_by_symbol_date.get(symbol) or {}

        for trading_date, day_bars in _session_days(bars).items():
            avg_vol = avg_by_date.get(trading_date)
            prev_close = prev_by_date.get(trading_date)
            if not avg_vol or not prev_close:
                continue

            exit_price[(symbol, trading_date)] = day_bars[-1].close

            # prefix[i] = volume of bars[0:i], so any window sum is a
            # subtraction rather than a re-scan.
            prefix = [0.0]
            for bar in day_bars:
                prefix.append(prefix[-1] + bar.volume)

            running_high = float("-inf")
            running_low = float("inf")
            for i, bar in enumerate(day_bars):
                running_high = max(running_high, bar.high)
                running_low = min(running_low, bar.low)
                cumulative = prefix[i + 1]
                pct = formulas.pct_change(bar.close, prev_close)
                if pct is None or bar.close <= 0:
                    continue

                rvol = formulas.rvol(cumulative, avg_vol) or 0.0
                recent_start = max(0, i + 1 - bars_per_window)
                recent = prefix[i + 1] - prefix[recent_start]
                prior_start = max(0, recent_start - bars_per_window)
                prior = prefix[recent_start] - prefix[prior_start]

                window_end = bar.timestamp + _BAR_LENGTH
                # Same warmup and direction rules as the live field
                # (volume_surge._anchor), or a backtest would validate a
                # signal the scanner never actually emits:
                #  - a full trailing window must fit inside the session, else
                #    a partial numerator is divided by a whole-window
                #    denominator and reads ~12x too quiet at 09:35;
                #  - volume_surge additionally needs a complete *prior*
                #    window, so it starts one hour later still;
                #  - long side only, matching is_momentum_alert.
                green = bar.close > bar.open
                has_full_window = i + 1 >= bars_per_window
                has_full_prior = i + 1 >= 2 * bars_per_window

                expected = None
                if curve and green and has_full_window:
                    share = fraction_at(curve, window_end) - fraction_at(curve, window_end - window)
                    expected = avg_vol * share if share > 0 else None

                rows_by_ts[bar.timestamp].append(
                    _IntradayRow(
                        symbol=symbol,
                        last_price=bar.close,
                        pct_change=pct,
                        # The session's *opening* gap, constant through the
                        # day -- taken from the first regular-session bar's
                        # open, not this bar's, which would just re-measure
                        # pct_change five minutes at a time.
                        gap_pct=formulas.pct_change(day_bars[0].open, prev_close),
                        volume_today=cumulative,
                        avg_vol_20d=avg_vol,
                        dollar_volume_today=formulas.dollar_volume(cumulative, bar.close),
                        rvol=rvol,
                        day_high=running_high,
                        day_low=running_low,
                        is_hod=formulas.is_hod(bar.close, running_high),
                        is_lod=formulas.is_lod(bar.close, running_low),
                        is_fade_risk=formulas.is_fade_risk(rvol),
                        is_shaved_top=is_shaved_top(bar.open, bar.high, bar.low, bar.close),
                        volume_1h=recent if has_full_window else None,
                        volume_surge=(
                            recent / prior if green and has_full_prior and prior > 0 else None
                        ),
                        rvol_1h=(recent / expected if expected else None),
                    )
                )

    return rows_by_ts, exit_price


def benchmark_to_close(benchmark_bars: list) -> dict[datetime, float]:
    """{bar_timestamp: the benchmark's % move from that bar to its own
    session close}.

    Alpha has to be measured over the *same* window as the pick. A pick
    entered at 14:35 and held to the close spans 14:35-16:00, so comparing it
    against SPY's daily close-to-close move -- which is the previous session's
    close to this one's, an almost entirely different window -- would be
    wrong, not merely approximate. Built from the benchmark's own 5-minute
    bars, which the runner already fetches for the volume profile.
    """
    result: dict[datetime, float] = {}
    for trading_date, day_bars in _session_days(benchmark_bars).items():
        if not day_bars:
            continue
        session_close = day_bars[-1].close
        for bar in day_bars:
            if bar.close > 0:
                result[bar.timestamp] = (session_close - bar.close) / bar.close * 100
    return result


def simulate_intraday_screen(
    bars_by_symbol: dict[str, list],
    avg_vol_by_symbol_date: dict[str, dict[date, float]],
    prev_close_by_symbol_date: dict[str, dict[date, float]],
    curve: list,
    screen,
    min_dollar_volume: float,
    window: timedelta,
    benchmark_to_close_by_ts: dict[datetime, float] | None = None,
    fundamentals: dict | None = None,
    catalysts: dict[str, dict[date, str]] | None = None,
) -> list[dict]:
    """One pick per (symbol, bar) the screen matches, held to that session's
    close.

    Held to the close rather than a fixed forward window because that's the
    question the screen is asking -- "is this worth being in now" -- and it
    keeps every pick's exit on the same side of the closing auction instead
    of scoring a 15:55 entry against an after-hours print.
    """
    rows_by_ts, exit_price = build_rows_by_timestamp(
        bars_by_symbol, avg_vol_by_symbol_date, prev_close_by_symbol_date, curve, window
    )

    picks: list[dict] = []
    for timestamp, cohort in sorted(rows_by_ts.items()):
        tradable = _tradable(cohort, min_dollar_volume)
        if not tradable:
            continue
        trading_date = timestamp.astimezone(ET).date()
        # One catalyst per symbol per session, so every bar of a news day
        # carries it. That is the point: the question is whether the move
        # *continues through the session*, which is what the original +9.1pp
        # measured (each appearance's latest same-day snapshot) and what the
        # daily replay cannot ask, since it enters only at the close.
        day_catalysts = (
            {s: m[trading_date] for s, m in catalysts.items() if trading_date in m}
            if catalysts
            else {}
        )
        derived = {
            "rank_score": {
                r.symbol: formulas.rank_score(
                    r.pct_change,
                    r.symbol in day_catalysts,
                    r.rvol,
                    catalyst_boost=r.pct_change > 0,
                )
                for r in tradable
            },
            # Today's values on a past date -- see backtest.LOOK_AHEAD_FIELDS.
            **(fundamentals or {}),
        }
        for row in screener.run_screen(tradable, screen, derived).rows:
            close = exit_price.get((row.symbol, trading_date))
            if close is None or row.last_price <= 0:
                continue
            pct_since_entry = (close - row.last_price) / row.last_price * 100
            # Keyed on the entry *bar*, so the benchmark spans exactly the
            # same minutes the pick does.
            benchmark_pct = (
                benchmark_to_close_by_ts.get(timestamp) if benchmark_to_close_by_ts else None
            )
            picks.append(
                {
                    "symbol": row.symbol,
                    "trading_date": trading_date.isoformat(),
                    "timestamp": timestamp.astimezone(ET).isoformat(),
                    "view": "screen",
                    "entry_pct_change": row.pct_change,
                    "entry_gap_pct": row.gap_pct,
                    "entry_rvol": row.rvol,
                    "entry_rvol_1h": row.rvol_1h,
                    "entry_dollar_volume": row.dollar_volume_today,
                    "is_shaved_top": row.is_shaved_top,
                    "entry_headline": day_catalysts.get(row.symbol),
                    "has_catalyst": row.symbol in day_catalysts,
                    "pct_change_since_entry": pct_since_entry,
                    "benchmark_pct_change_since_entry": benchmark_pct,
                    "alpha_vs_benchmark": (
                        pct_since_entry - benchmark_pct if benchmark_pct is not None else None
                    ),
                }
            )
    return picks


def sample_picks(picks: list[dict], limit: int = 300) -> list[dict]:
    """Up to `limit` picks spread evenly across the whole backtest period,
    newest first.

    Every aggregate in the report is computed over the *full* pick list
    server-side; this only bounds what crosses the wire, since an intraday
    run routinely produces thousands (one real run made 6,818).

    Evenly spaced rather than simply the newest N, which is what this did
    first and was actively misleading: 300 newest picks out of 5,027 covered
    only 12:30-15:55 of the final day, so a 15-day backtest presented a list
    that looked like it had all happened in one afternoon. A stride across
    the time-ordered list keeps the visible sample representative of the
    period the statistics actually describe.
    """
    ordered = sorted(picks, key=lambda p: p.get("timestamp") or p["trading_date"])
    if len(ordered) > limit and limit > 1:
        # Spans both endpoints. A plain `i * len/limit` stride stops short of
        # the final element, which would silently drop the most recent picks
        # -- the ones most likely to be looked at first.
        last = len(ordered) - 1
        ordered = [ordered[round(i * last / (limit - 1))] for i in range(limit)]
    return list(reversed(ordered))


def replication_factor(picks: list[dict]) -> dict:
    """How much of `sample_size` is one event counted many times.

    Every qualifying bar is a pick here, so a single surge that stays true
    for an hour contributes ~12 near-identical rows. This reports the raw
    count beside the number of distinct (symbol, day) events behind it; a
    ratio well above 1 means the sample carries less independent evidence
    than its size suggests, and win rates from it are correspondingly less
    trustworthy.
    """
    events = {(p["symbol"], p["trading_date"]) for p in picks}
    return {
        "sample_size": len(picks),
        "distinct_symbol_days": len(events),
        "picks_per_event": round(len(picks) / len(events), 2) if events else None,
    }
