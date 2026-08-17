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

import math
import random
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


# --- What multiplier does a measured edge actually justify? -----------------
#
# formulas._CATALYST_BOOST rescales ranking *magnitude*, so a boost of m ranks
# a flagged name where an unflagged name with m-times the gap would sit. The
# multiplier is therefore an exchange rate between "has a catalyst" and "moved
# further", and the conversion factor is the slope of outcome against gap --
# not the size of the edge. Setting m proportional to the edge is a units
# error, and one worth guarding against in code: the naive rescaling of 1.15
# by 1.7/9.1 gives 1.03, while solving it properly gives 1.2-2.1.

# Gap bands, in %. Deliberately widening: gaps are roughly log-distributed, so
# equal-width bands would put almost everything in the first one.
_GAP_BANDS = (0.0, 1.0, 2.0, 3.0, 5.0, 8.0, 13.0, 21.0, float("inf"))
# Below this gap, bigger moves predict better outcomes; above it the relation
# inverts (the same exhaustion effect formulas._FADE_RISK_RVOL discounts), so
# a single slope fit across the whole range is meaningless -- and the extreme
# tail is far enough out to set an unweighted fit's *sign* by itself.
GAP_INVERSION_PCT = 8.0
# A band this thin can't carry a win rate worth fitting through.
_MIN_BAND_SAMPLE = 100


def outcome_by_gap(picks: list[dict], limit: float | None = None) -> list[dict]:
    """Win rate and mean alpha per gap band, for picks with no catalyst.

    The unflagged side alone, because it is the reference the flagged side is
    being priced against -- mixing them in would let the very effect being
    measured bend the yardstick.
    """
    bands = []
    for low, high in zip(_GAP_BANDS, _GAP_BANDS[1:]):
        if limit is not None and low >= limit:
            break
        rows = [p for p in picks if low <= p["entry_pct_change"] < high]
        if len(rows) < _MIN_BAND_SAMPLE:
            continue
        alphas = [r["alpha_vs_benchmark"] for r in rows if r.get("alpha_vs_benchmark") is not None]
        bands.append(
            {
                "low": low,
                "high": high,
                "mean_gap": mean(p["entry_pct_change"] for p in rows),
                "sample_size": len(rows),
                "win_rate": sum(1 for r in rows if r["pct_change_since_entry"] > 0) / len(rows) * 100,
                "mean_alpha": mean(alphas) if alphas else 0.0,
            }
        )
    return bands


def _slope_per_doubling(bands: list[dict], key: str) -> float:
    """Least-squares slope of `key` against log2(gap), weighted by band size.

    Weighted because the bands differ by two orders of magnitude in
    population; unweighted, a 114-pick tail band counts as much as a
    9,000-pick one.
    """
    xs = [math.log2(b["mean_gap"]) for b in bands if b["mean_gap"] > 0]
    if len(xs) < 3:
        return 0.0
    ys = [b[key] for b in bands if b["mean_gap"] > 0]
    ws = [float(b["sample_size"]) for b in bands if b["mean_gap"] > 0]
    total = sum(ws)
    mx = sum(w * x for w, x in zip(ws, xs)) / total
    my = sum(w * y for w, y in zip(ws, ys)) / total
    denom = sum(w * (x - mx) ** 2 for w, x in zip(ws, xs))
    if denom <= 0:
        return 0.0
    return sum(w * (x - mx) * (y - my) for w, x, y in zip(ws, xs, ys)) / denom


def _win_rate(rows: list[dict]) -> float | None:
    if not rows:
        return None
    return sum(1 for r in rows if r["pct_change_since_entry"] > 0) / len(rows) * 100


def _mean_alpha(rows: list[dict]) -> float | None:
    vals = [r["alpha_vs_benchmark"] for r in rows if r.get("alpha_vs_benchmark") is not None]
    return mean(vals) if vals else None


# Below this, the fitted slope is treated as flat: gap carries no outcome
# signal, so no finite multiplier expresses the edge and dividing by it would
# manufacture an enormous one out of nothing.
_FLAT_SLOPE_PP = 0.05


def _solve(picks: list[dict], limit: float) -> dict | None:
    flagged = [p for p in picks if p["has_catalyst"]]
    plain = [p for p in picks if not p["has_catalyst"]]
    if len(flagged) < 50 or len(plain) < 200:
        return None
    bands = outcome_by_gap(plain, limit)
    if len(bands) < 3:
        return None
    out = {}
    for name, slope_key, edge in (
        ("win_rate", "win_rate", (_win_rate(flagged) or 0) - (_win_rate(plain) or 0)),
        ("alpha", "mean_alpha", (_mean_alpha(flagged) or 0) - (_mean_alpha(plain) or 0)),
    ):
        slope = _slope_per_doubling(bands, slope_key)
        out[f"{name}_slope_pp_per_doubling"] = round(slope, 4)
        out[f"{name}_edge_pp"] = round(edge, 4)
        out[f"{name}_multiplier"] = (
            None if abs(slope) < _FLAT_SLOPE_PP else round(2 ** (edge / slope), 3)
        )
    out["bands"] = bands
    return out


def implied_multiplier(
    picks: list[dict],
    *,
    limit: float = GAP_INVERSION_PCT,
    bootstrap: int = 400,
    seed: int = 7,
) -> dict | None:
    """What formulas._CATALYST_BOOST should be, given these picks.

    Long side only -- the boost is a gainers-side adjustment, and "gap times
    m" only means anything for a move with a direction.

    Restricted to gaps below `limit` because the slope everything divides by
    only exists there (see GAP_INVERSION_PCT). The excluded count is returned
    so a caller can see how much of the sample the answer doesn't cover.

    The bootstrap resamples whole symbol-days, not picks. An intraday replay
    emits one pick per qualifying 5-minute bar, all carrying the same day's
    catalyst flag and near-identical outcomes, so resampling picks would treat
    ~11k correlated rows as independent draws and report an interval several
    times too tight -- which would turn "cannot distinguish 1.15 from 2.1"
    into a false claim of precision.
    """
    longs = [p for p in picks if p["entry_pct_change"] > 0]
    used = [p for p in longs if p["entry_pct_change"] < limit]
    result = _solve(used, limit)
    if result is None:
        return None

    by_day: dict[tuple, list[dict]] = defaultdict(list)
    for pick in used:
        by_day[(pick["symbol"], pick.get("trading_date"))].append(pick)

    result.update(
        {
            "gap_limit_pct": limit,
            "picks_used": len(used),
            "picks_excluded_above_limit": len(longs) - len(used),
            "symbol_days": len(by_day),
            "flagged_symbol_days": len({k for k, v in by_day.items() if v[0]["has_catalyst"]}),
        }
    )

    keys = list(by_day)
    if bootstrap and keys:
        rng = random.Random(seed)
        draws = []
        for _ in range(bootstrap):
            resampled = [p for k in rng.choices(keys, k=len(keys)) for p in by_day[k]]
            drawn = _solve(resampled, limit)
            if drawn and drawn["win_rate_multiplier"] is not None:
                draws.append(drawn["win_rate_multiplier"])
        if draws:
            draws.sort()
            result["bootstrap"] = {
                "draws": len(draws),
                "requested": bootstrap,
                "p05": round(draws[int(len(draws) * 0.05)], 3),
                "median": round(draws[len(draws) // 2], 3),
                "p95": round(draws[int(len(draws) * 0.95)], 3),
                "share_below_one_pct": round(
                    sum(1 for d in draws if d < 1.0) / len(draws) * 100, 1
                ),
            }
    return result
