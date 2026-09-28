"""Cutting backtest picks by how the futures behind an ETF were positioned
-- the one slice that has to be built backwards in time or not at all.

The Commitments of Traders report is weekly and late by design: it counts
positions held on a Tuesday and is published the Friday after at 15:30 ET.
Two consequences run through everything here.

First, a pick on day D may only see a report that was public before D. The
naive join -- "the week whose report_date is nearest" -- hands a Tuesday
reading to the Wednesday and Thursday that followed it, three days before
anyone could read it. That is not a small distortion: those are exactly
the days on which the positioning being measured was still being built.

Second, the percentile has to be computed from the weeks up to that report
and no further. app.market_data.cot.reading ranks the latest week against
three years *around* it, which is right for a line on a live ticket and
wrong here: ranking a 2024 week against 2025's history is the backtest
sin of knowing how the range turned out.

So the band at a date is rebuilt from the weeks available at that date,
percentile and all. The cost is one pass per pick date; the alternative is
a result that looks better than the strategy was.

Only the ETFs in cot.CONTRACTS have a contract at all. Picks on everything
else are gathered into their own bucket rather than dropped, so the
buckets still add up to the sample -- the same rule the VIX bands follow
(see strategy_slices).
"""

from __future__ import annotations

from datetime import date, timedelta

from app.market_data.cot import CONTRACTS, HISTORY_YEARS, Week, percentile_of
from app.scanners import bucket_analysis

# Tuesday's positions, published the Friday after at 15:30 ET. A daily
# backtest enters at a session's own price, so a report released during
# Friday's session is treated as readable from the next day on -- never
# on the Friday itself, which would hand an entry a number published
# after part of the session had already traded.
REPORT_TO_RELEASE_DAYS = 3


def public_from(report_date: date) -> date:
    """The first date a report may be used by a backtest."""
    return report_date + timedelta(days=REPORT_TO_RELEASE_DAYS + 1)


# Where the speculative side sits in its own three-year range. The edges
# are deciles because that is where "crowded" is claimed to live; the
# middle is one wide band on purpose, since a rule that only pays between
# the 30th and 70th percentile is a rule that pays in ordinary weeks and
# the label should say so.
PERCENTILE_BANDS: tuple[tuple[str, float, float], ...] = (
    ("bottom decile -- crowded short", 0.0, 0.1),
    ("10-30th", 0.1, 0.3),
    ("30-70th ordinary", 0.3, 0.7),
    ("70-90th", 0.7, 0.9),
    ("top decile -- crowded long", 0.9, 1.0001),
)

NO_CONTRACT = "no COT contract"
NOT_YET_KNOWN = "COT not yet published"


def week_known_on(weeks: list[Week], day: date) -> Week | None:
    """The most recent report that was public on `day`.

    `weeks` is oldest first (cot.parse_weeks guarantees it). None when the
    history does not reach back that far -- which is a real answer for the
    first weeks of any window, not a reason to reach forward.
    """
    usable = [w for w in weeks if public_from(w.report_date) <= day]
    return usable[-1] if usable else None


def band_on(weeks: list[Week], day: date) -> str | None:
    """The band a pick on `day` falls into, ranked only against the weeks
    that had been published by then.

    The window is the same trailing three years the live ticket line uses
    (cot.HISTORY_YEARS), not everything on hand: an expanding window would
    rank the first pick of a run against one year and the last against
    four, so a band would mean something different at each end of the very
    table it is being read across.

    None when no report was public yet, or when fewer than a year of weeks
    stood behind it -- percentile_of refuses to rank on less, and a band
    invented from eight readings would be the most confident row in the
    table.
    """
    week = week_known_on(weeks, day)
    if week is None:
        return None
    share = week.spec_net_pct_oi
    if share is None:
        return None
    window_start = week.report_date - timedelta(days=365 * HISTORY_YEARS)
    history = [
        w.spec_net_pct_oi
        for w in weeks
        if window_start < w.report_date <= week.report_date and w.spec_net_pct_oi is not None
    ]
    pct = percentile_of(share, history)
    if pct is None:
        return None
    for label, low, high in PERCENTILE_BANDS:
        if low <= pct < high:
            return label
    return None


def _pick_date(pick: dict) -> date:
    return date.fromisoformat(pick["trading_date"])


def by_cot_band(picks: list[dict], weeks_by_symbol: dict[str, list[Week]]) -> list[dict]:
    """Every band in order, plus the two kinds of pick that have no band:
    a symbol the CFTC does not cover, and a date no report had reached yet.

    Both are kept visible. "Most of the sample is not covered" is the first
    thing a reader of this table needs to know, and a row count that does
    not add up to the run is how that gets hidden.
    """
    banded: dict[str, list[dict]] = {label: [] for label, _lo, _hi in PERCENTILE_BANDS}
    no_contract: list[dict] = []
    unpublished: list[dict] = []

    for pick in picks:
        weeks = weeks_by_symbol.get(pick["symbol"].upper())
        if not weeks:
            no_contract.append(pick)
            continue
        label = band_on(weeks, _pick_date(pick))
        if label is None:
            unpublished.append(pick)
            continue
        banded[label].append(pick)

    rows = [
        {"bucket": label, **bucket_analysis.bucket_stats(banded[label])}
        for label, _lo, _hi in PERCENTILE_BANDS
    ]
    for label, group in ((NOT_YET_KNOWN, unpublished), (NO_CONTRACT, no_contract)):
        if group:
            rows.append({"bucket": label, **bucket_analysis.bucket_stats(group)})
    return rows


def covered_symbols(picks: list[dict]) -> list[str]:
    """The symbols in this run that have a contract at all -- what to go
    and fetch, rather than all ten every time."""
    present = {p["symbol"].upper() for p in picks}
    return sorted(present & set(CONTRACTS))
