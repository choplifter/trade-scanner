"""Replaying a strategy over 5-minute bars, and scoring it where it actually
got out.

Every other backtest in this app measures entry-to-fixed-horizon: "where was
price 15 minutes later". That answers whether a signal precedes drift, which
is not whether it would have been a trade -- a setup whose average forward
move is positive still loses if the path there runs through its stop. So the
outcome here comes from exit_rules.simulate_exit against the signal's own
structural stop and target, and is reported in R.

The walk itself is the skeleton of momentum_backtest.simulate_momentum_alerts,
kept deliberately identical in the four places where it encodes something
that was expensive to learn:

  1. VWAP is fed *every* bar, not only qualifying ones. It is a running
     cumulative total; skipping bars corrupts it.
  2. Only the first bar of a consecutive qualifying run counts (a rising
     edge). A real move keeps a condition true for several bars, and
     counting each one pseudo-replicates a single event into many highly
     correlated samples, inflating apparent n.
  3. Streak state resets at the session boundary, so the first qualifying
     bar of the next session is a fresh edge rather than being swallowed by
     a streak that began before the close.
  4. Exits must resolve inside the same regular session. Otherwise a clean
     late-session entry is scored against a thin after-hours print.

What is new here is that the horizon is gone. Bars after entry are handed to
simulate_exit until the session ends, and the trade ends where the stop, the
target or the close puts it.
"""

from pathlib import Path

from app.alpaca.client import AlpacaClients
from app.market_data import levels as market_levels
from app.market_data import session_marks
from app.market_data.momentum import is_regular_session_bar, same_trading_day
from app.market_data.vwap import SessionVwapState
from app.scanners import bucket_analysis
from app.scanners.bar_cache import DEFAULT_CACHE_DIR, get_cached_5m_bars_multi
from app.scanners.exit_rules import Exit, ExitRule, expectancy, simulate_exit
from app.services.market_clock import ET
from app.strategies.context import Signal, build_context


def _forward_session_bars(bars: list, index: int, entry_bar) -> list:
    """Bars after `index` that are still in the same regular session.

    Walks forward and stops at the first bar of another ET date instead of
    slicing the whole tail and filtering it -- the tail of a multi-week
    series is long, and this runs once per pick.
    """
    forward = []
    for bar in bars[index + 1 :]:
        if not same_trading_day(bar, entry_bar):
            break
        if is_regular_session_bar(bar):
            forward.append(bar)
    return forward


def rule_for(signal: Signal, cost_bps: float = 0.0) -> ExitRule:
    """The signal's structural stop and target, in the percentage form
    ExitRule takes. Both numbers come off the signal -- nothing is chosen
    here -- so a rule aiming at the next level is scored against that level
    rather than against a configured multiple.
    """
    return ExitRule(
        stop_pct=signal.stop_pct,
        reward_ratio=signal.reward_ratio,
        cost_bps=cost_bps,
        side=signal.side,
        stop_trigger=signal.stop_trigger,
        scale_out=signal.scale_out,
    )


def simulate_strategy(
    bars_by_symbol: dict[str, list],
    strategy,
    cost_bps: float = 0.0,
) -> list[dict]:
    """Pure and network-free. One pick per rising edge, each carrying both
    its R multiple and its percentage move.

    The two are not the same measurement, and the difference is deliberate:
    r_multiple charges cost_bps on both sides, pct_change_since_entry does
    not, because it is the field bucket_analysis shares with every other
    backtest in this app and those were all measured cost-free. Comparing a
    percentage here against a percentage there is therefore valid; reading
    the two columns of one row as the same trade priced twice is not.
    """
    picks: list[dict] = []

    for symbol, bars in bars_by_symbol.items():
        # The VWAP at every bar, accumulated once here and handed to
        # build_context. Kept aligned with `bars` index for index: a strategy
        # asking whether price respected the line needs where the line *was*,
        # and rebuilding it inside a strategy file would mean a second
        # accumulator that can drift from this one.
        vwaps: list = []
        # Levels are recomputed once per trading day, from bars that had
        # already printed, and held for that whole session. Once per day
        # rather than once per bar for two reasons: the scan is many
        # vectorised passes, and a level is marked from completed structure --
        # one that appeared at 11:40 was not there to be traded at 09:45.
        levels: tuple = ()
        levels_day = None
        active = False
        # The session the streak belongs to. Resetting only on a bar that
        # falls outside the session is not enough: a series holding nothing
        # but regular-session bars has no such bar between one close and the
        # next open, so a streak running into the close would swallow the
        # first qualifying bar of the next morning -- silently, as one pick
        # fewer rather than as an error.
        streak_day = None
        vwap_state = SessionVwapState(symbol=symbol)
        for i, bar in enumerate(bars):
            vwap = vwap_state.update(
                timestamp=bar.timestamp,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                bar_vwap=getattr(bar, "vwap", None),
            )
            vwaps.append(vwap)

            bar_day = bar.timestamp.astimezone(ET).date()
            if bar_day != streak_day:
                active = False
                streak_day = bar_day

            if bar_day != levels_day:
                levels_day = bar_day
                # Strictly before this session's first bar, so nothing from
                # today can inform a level traded today. levels_as_of is the
                # only look-ahead-safe entry point; see its docstring.
                # Two sources, one list. "The next level" means the nearest
                # one whatever kind it is, so the rule should not have to
                # know which. Multi-day structure sits percent away; the
                # night's range and yesterday's bounds sit where the day is
                # actually being fought, and inside one session those are
                # usually the ones price reaches.
                levels = tuple(
                    sorted(
                        {
                            *(p for p, _ in market_levels.levels_as_of(bars[:i], bar.timestamp)),
                            *session_marks.marks_for_session(bars[:i], bar_day),
                        }
                    )
                )

            # Only the bars walked so far exist yet, which is also the guard
            # that stops a strategy seeing the future through this list.
            ctx = build_context(
                symbol, bars[: i + 1], i, vwaps, vwap_state.premarket_anchored_vwap, levels
            )
            if ctx is None:
                # Outside the regular session: nothing may trigger, and the
                # streak breaks rather than carrying across the gap.
                active = False
                continue

            signal = strategy.evaluate(ctx)
            if signal is None:
                active = False
                continue

            if not active:
                forward = _forward_session_bars(bars, i, bar)
                # No forward bars means the signal fired on the last bar of
                # its session. simulate_exit scores that flat at its own
                # entry; counting it would add a free zero to the sample, so
                # it is dropped instead.
                if forward:
                    exit_ = simulate_exit(signal.entry_price, forward, rule_for(signal, cost_bps))
                    picks.append(
                        {
                            "symbol": symbol,
                            "timestamp": bar.timestamp.isoformat(),
                            "strategy": signal.strategy,
                            "reason": signal.reason,
                            "entry_price": signal.entry_price,
                            "stop_price": signal.stop_price,
                            "target_price": signal.target_price,
                            "exit_price": exit_.price,
                            "exit_reason": exit_.reason,
                            "ambiguous_exit": exit_.ambiguous,
                            "r_multiple": exit_.r_multiple,
                            "pct_change_since_entry": (exit_.price - signal.entry_price)
                            / signal.entry_price
                            * 100,
                        }
                    )

            active = True

    return picks


def strategy_report(picks: list[dict], strategy_name: str) -> dict:
    """Both views of the same exits: R (what the trade was worth against its
    own risk) and the percentage stats bucket_analysis produces everywhere
    else. expectancy leads because win rate is the number that looks like an
    edge and is not one -- see exit_rules.expectancy.
    """
    exits = [
        Exit(
            price=p["exit_price"],
            reason=p["exit_reason"],
            r_multiple=p["r_multiple"],
            ambiguous=p["ambiguous_exit"],
        )
        for p in picks
    ]
    return {
        "strategy": strategy_name,
        "sample_size": len(picks),
        "expectancy": expectancy(exits),
        "returns": bucket_analysis.bucket_stats(picks),
        "min_sample_size": bucket_analysis.MIN_SAMPLE_SIZE,
        "picks": picks,
    }


async def run_strategy_backtest(
    clients: AlpacaClients,
    strategy,
    symbols: list[str],
    lookback_days: int = 30,
    cost_bps: float = 0.0,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    force_refresh: bool = False,
    max_age_hours: float = 12.0,
) -> dict:
    """Fetch/orchestration wrapper, mirroring
    momentum_backtest.run_momentum_backtest -- same disk cache, so replaying
    several strategies against one lookback costs one fetch.
    """
    bars_by_symbol = await get_cached_5m_bars_multi(
        clients,
        symbols,
        lookback_days,
        cache_dir=cache_dir,
        force_refresh=force_refresh,
        max_age_hours=max_age_hours,
    )

    # The strategy object is loaded once by the caller (see
    # app.strategies.loader) and held for the whole walk, so every bar in this
    # report was judged by the same version of the rule.
    picks = simulate_strategy(bars_by_symbol, strategy, cost_bps)
    report = strategy_report(picks, strategy.name)
    report.update(
        {
            "symbol_count": len(symbols),
            "symbols_with_bars": len(bars_by_symbol),
            "lookback_days": lookback_days,
            "cost_bps": cost_bps,
        }
    )
    return report
