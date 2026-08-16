"""Does the scanner's ranking actually predict anything, and how high can
its win rate honestly go?

Every other analysis tool in this app measures the scanner against itself:
which bucket did better than which other bucket. That can't answer the two
questions that decide whether any of it is worth acting on --

  1. Does a ranked pick beat a *random* pick from the same universe? If
     gainers win 48.7% and every symbol-day in the sample wins 49%, the
     ranking is sorting noise and no amount of bucket tuning changes that.
  2. Does an apparently good rule survive on data it wasn't chosen from?
     With ~19k picks and dozens of candidate conditions, the best in-sample
     rule is close to guaranteed to look good in-sample whether or not any
     edge exists. The only informative number is what it then does
     out-of-sample.

Both are deliberately hostile tests. A tool that can only confirm the
scanner works isn't a validation tool.

On win rate as a target: it is gameable and shouldn't be optimized on its
own. Win rate and payoff ratio trade off directly -- a tighter exit raises
the first and lowers the second -- so expectancy() reports both, and the
condition search reports median return next to win rate. A 70% win rate
built from wins half the size of its losses is worse than a 45% one that
isn't.
"""

from collections import defaultdict
from itertools import product
from statistics import mean, median

from app.scanners import bucket_analysis

# A condition needs this many in-sample picks before it's even considered.
# Higher than bucket_analysis.MIN_SAMPLE_SIZE on purpose: that floor is for
# reading one pre-chosen bucket, whereas this search evaluates hundreds of
# conditions and keeps the best, which needs a good deal more evidence per
# condition to mean the same thing.
MIN_CONDITION_SAMPLE = 100
# Share of trading dates held out of the search entirely.
DEFAULT_OOS_FRACTION = 0.30


def expectancy(picks: list[dict], key: str = "pct_change_since_entry") -> dict | None:
    """Win rate *and* what a win and a loss are actually worth.

    Win rate alone can't distinguish a tradeable edge from a bad one: it
    says how often, never how much. avg_win/avg_loss is the other half, and
    the median is there because these returns are fat-tailed enough
    (measured -66% to +459% on thin names) that a mean describes outliers.
    """
    values = [p[key] for p in picks if p.get(key) is not None]
    if not values:
        return None
    wins = [v for v in values if v > 0]
    losses = [v for v in values if v <= 0]
    avg_win = mean(wins) if wins else 0.0
    avg_loss = mean(losses) if losses else 0.0
    return {
        "sample_size": len(values),
        "win_rate": round(len(wins) / len(values) * 100, 1),
        "median": round(median(values), 2),
        "mean": round(mean(values), 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        # How many times bigger the average win is than the average loss.
        # With a 50% win rate anything below 1.0 loses money.
        "payoff_ratio": round(avg_win / abs(avg_loss), 2) if avg_loss else None,
    }


def base_rate(
    bars_by_symbol: dict[str, list],
    horizon_days: int = 1,
    min_dollar_volume: float = 0.0,
) -> dict | None:
    """The win rate of a random symbol-day from the same universe -- the
    number the scanner has to beat to be worth running.

    `min_dollar_volume` applies the same tradability filter the ranking
    applies (`engine._tradable`), and passing it matters more than it
    looks. Without it this counts symbol-days the scanner would never have
    ranked at all -- thin, illiquid names whose fat upside tails flatter the
    comparison population and make the scanner look worse than random for a
    reason that has nothing to do with the scanner. The comparison is only
    fair between two populations that were both actually tradable.
    """
    outcomes = []
    for bars in bars_by_symbol.values():
        for i in range(len(bars) - horizon_days):
            entry = bars[i].close
            if entry <= 0 or bars[i].volume <= 0:
                continue
            if bars[i].volume * entry < min_dollar_volume:
                continue
            outcomes.append(
                {"pct_change_since_entry": (bars[i + horizon_days].close - entry) / entry * 100}
            )
    return expectancy(outcomes)


def split_by_date(picks: list[dict], oos_fraction: float = DEFAULT_OOS_FRACTION) -> tuple[list, list]:
    """Chronological in-sample/out-of-sample split -- never random.

    A random split would leak: picks from the same trading day are strongly
    correlated (one market-wide move drives all of them), so the same day
    landing on both sides lets a rule be "tested" on what it was fitted to.
    Splitting on the date boundary also matches how the rule would actually
    be used -- fitted on the past, applied to the future.
    """
    dates = sorted({p["trading_date"] for p in picks})
    if not dates:
        return [], []
    boundary = dates[int(len(dates) * (1 - oos_fraction))]
    return (
        [p for p in picks if p["trading_date"] < boundary],
        [p for p in picks if p["trading_date"] >= boundary],
    )


def _gap_band(pick: dict) -> str:
    gap = abs(pick["entry_pct_change"])
    return "<5%" if gap < 5 else "5-15%" if gap < 15 else "15-30%" if gap < 30 else "30%+"


def _rvol_band(pick: dict) -> str:
    rvol = pick["entry_rvol"]
    return "<1x" if rvol < 1 else "1-2x" if rvol < 2 else "2-5x" if rvol < 5 else "5x+"


def _dollar_band(pick: dict) -> str:
    dollars = pick["entry_dollar_volume"]
    return "lo$" if dollars < 5e6 else "mid$" if dollars < 5e7 else "hi$"

# Each condition dimension, as {label: predicate}. "*" means unconstrained,
# which is what lets the search consider both broad and narrow rules in one
# pass rather than only the fully-specified corners.
_DIMENSIONS = {
    "gap": (_gap_band, ["<5%", "5-15%", "15-30%", "30%+"]),
    "rvol": (_rvol_band, ["<1x", "1-2x", "2-5x", "5x+"]),
    "dollar": (_dollar_band, ["lo$", "mid$", "hi$"]),
}


def _matches(pick: dict, view: str, bands: dict, shaved) -> bool:
    if pick["view"] != view:
        return False
    for name, wanted in bands.items():
        if wanted != "*" and _DIMENSIONS[name][0](pick) != wanted:
            return False
    return shaved == "*" or pick["is_shaved_top"] is shaved


def search_conditions(
    in_sample: list[dict],
    out_of_sample: list[dict],
    min_sample: int = MIN_CONDITION_SAMPLE,
) -> list[dict]:
    """Rank simple conditions by in-sample win rate, then report what each
    one did out-of-sample.

    The out-of-sample column is the entire point. Reading only the left-hand
    column is how a backtest gets talked into a rule that doesn't work: the
    top of a few-hundred-condition search is where the noise collects, so a
    high in-sample win rate is the *expected* result of searching, not
    evidence about the rule.

    Conditions are deliberately coarse -- view, gap band, RVOL band, dollar
    band, shaved top, each optionally unconstrained. Finer bands would push
    in-sample win rates higher and mean less.
    """
    results = []
    band_options = {name: [*labels, "*"] for name, (_, labels) in _DIMENSIONS.items()}
    for view in bucket_analysis.VIEWS:
        for gap, rvol, dollar, shaved in product(
            band_options["gap"], band_options["rvol"], band_options["dollar"], [True, False, "*"]
        ):
            bands = {"gap": gap, "rvol": rvol, "dollar": dollar}
            selected = [p for p in in_sample if _matches(p, view, bands, shaved)]
            if len(selected) < min_sample:
                continue
            held_out = [p for p in out_of_sample if _matches(p, view, bands, shaved)]
            results.append(
                {
                    "label": f"{view} gap={gap} rvol={rvol} {dollar} shaved={shaved}",
                    "in_sample": expectancy(selected),
                    "out_of_sample": expectancy(held_out),
                }
            )
    results.sort(key=lambda r: r["in_sample"]["win_rate"], reverse=True)
    return results


def overfitting_gauge(results: list[dict], top_n: int = 25) -> dict | None:
    """How far the best in-sample rules fall when applied to unseen dates.

    A mean drop near zero means the search found something structural. A
    large positive drop means it found the shape of its own sample -- and
    puts a number on how much of any "improvement" from condition-picking
    is real, which is the honest answer to a win-rate target.
    """
    drops = [
        r["in_sample"]["win_rate"] - r["out_of_sample"]["win_rate"]
        for r in results[:top_n]
        if r["out_of_sample"] and r["out_of_sample"]["sample_size"] >= bucket_analysis.MIN_SAMPLE_SIZE
    ]
    if not drops:
        return None
    return {
        "rules_compared": len(drops),
        "mean_win_rate_drop_pp": round(mean(drops), 1),
        "median_win_rate_drop_pp": round(median(drops), 1),
        "rules_that_held_up": sum(1 for d in drops if d <= 0),
    }


def per_view_expectancy(picks: list[dict]) -> list[dict]:
    """expectancy() per view, plus the same on alpha where it's available."""
    rows = []
    for view_name in bucket_analysis.VIEWS:
        view_picks = [p for p in picks if p["view"] == view_name]
        if not view_picks:
            continue
        rows.append(
            {
                "view": view_name,
                "raw": expectancy(view_picks),
                "alpha": expectancy(view_picks, key="alpha_vs_benchmark"),
            }
        )
    return rows


def picks_by_horizon_summary(picks_by_horizon: dict[int, list[dict]]) -> list[dict]:
    """Per (horizon, view) expectancy -- does holding longer change anything?

    Worth checking before concluding the scanner has no edge: a signal can be
    real but slower than the 1-day horizon everything else here defaults to.
    """
    summary = []
    for horizon in sorted(picks_by_horizon):
        by_view = defaultdict(list)
        for pick in picks_by_horizon[horizon]:
            by_view[pick["view"]].append(pick)
        for view_name in bucket_analysis.VIEWS:
            if view_name in by_view:
                summary.append(
                    {"horizon_days": horizon, "view": view_name, **(expectancy(by_view[view_name]) or {})}
                )
    return summary
