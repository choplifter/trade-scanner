"""Separating the sector part of a move from the stock's own.

The question this answers is "is this name up because its sector is up, or is
it running on its own". That is a decomposition, not a count -- so the output
is three numbers per symbol that add back up to the move:

    market_pct        = spy_pct                   the whole tape
    sector_excess_pct = sector_pct - spy_pct      what the sector did beyond it
    stock_specific    = stock_pct  - sector_pct   what this name did beyond its sector
                        ----------------------
    sum               = stock_pct                 exactly, by construction

`stock_specific` is arithmetically the same operation as
benchmark_tracker.compute_performance's alpha_vs_benchmark, just measured
against the sector instead of the broad market. The three inputs are all the
same quantity -- day change against the previous close, from snapshots taken
on the same poll -- and that equivalence is what makes subtracting them
legitimate at all.

Deliberately free of any ScannerEngine import, the same way benchmark_tracker
is and for the same reason: the engine needs the ETF list to poll, the Dash
page needs the arithmetic, and a module that knows neither can be imported by
both without a cycle.

A second, independent signal lives here too -- sector *breadth*, i.e. how
over-represented a sector is among the movers. It is deliberately not folded
into the per-symbol verdict, because the informative case is exactly the one
where the two disagree: a name that looks independent against a flat sector
ETF while its sector is visibly crowded. One label would destroy that.
"""

import logging

logger = logging.getLogger(__name__)

# FMP's sector strings (Morningstar-style) -> the SPDR sector ETF that stands
# in for that sector's day. Verified against live data: "Financial Services",
# "Healthcare", "Technology", "Industrials", "Basic Materials" and
# "Consumer Defensive" all came back exactly like this.
SECTOR_ETFS: dict[str, str] = {
    "Basic Materials": "XLB",
    "Communication Services": "XLC",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Energy": "XLE",
    "Financial Services": "XLF",
    "Healthcare": "XLV",
    "Industrials": "XLI",
    "Real Estate": "XLRE",
    "Technology": "XLK",
    "Utilities": "XLU",
}

# The same sectors under GICS wording. FMP is the only source today, but a
# vendor renaming "Healthcare" to "Health Care" would otherwise tip an entire
# sector into Unknown -- and a silent whole-sector outage is the kind of thing
# nobody notices until the numbers have been wrong for a week.
_SECTOR_ALIASES: dict[str, str] = {
    "Financials": "XLF",
    "Health Care": "XLV",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Materials": "XLB",
    "Information Technology": "XLK",
    "Telecommunication Services": "XLC",
}

# Label for rows whose sector is unknown -- missing profile, a 429'd fetch, or
# a sector string we have no ETF for. Never merged into a real sector.
UNKNOWN_SECTOR = "Unknown"

# Below this, a sector ETF's day is indistinguishable from noise, so nothing
# is attributed to it.
SECTOR_NOISE_PCT = 0.25

# A sector ETF day above this is a genuine sector event: SPY's own daily sigma
# is roughly 1%, so a whole sector moving that far is not drift.
SECTOR_STRONG_PCT = 1.0

# Share of the move attributable to the sector. Both are judgement calls, not
# measurements -- unlike the fade-risk RVOL threshold, which came out of this
# app's own win-rate history, there is nothing here to calibrate against yet:
# sector is not recorded in scanner_history, so these cannot be validated
# against forward returns until it is. Stated plainly so nobody mistakes them
# for findings.
SECTOR_DRIVEN_SHARE = 0.50
TAILWIND_SHARE = 0.15

VERDICT_UNKNOWN = "unknown"
VERDICT_AGAINST = "against sector"
VERDICT_DRIVEN = "sector-driven"
VERDICT_TAILWIND = "sector tailwind"
VERDICT_INDEPENDENT = "independent"

# Most sector-driven to most idiosyncratic, unknown last. Clients colour and
# sort off this number rather than matching the label text.
VERDICT_RANK = {
    VERDICT_DRIVEN: 0,
    VERDICT_TAILWIND: 1,
    VERDICT_INDEPENDENT: 2,
    VERDICT_AGAINST: 3,
    VERDICT_UNKNOWN: 4,
}

_LOOKUP = {
    name.strip().casefold(): etf
    for name, etf in {**SECTOR_ETFS, **_SECTOR_ALIASES}.items()
}

# One log line per distinct unrecognised sector string, so a vendor wording
# change surfaces somewhere instead of vanishing into Unknown.
_unmapped_seen: set[str] = set()


def etf_for_sector(sector: str | None) -> str | None:
    """The ETF standing in for a sector, or None if we have none.

    None is a real answer here and must not be turned into 0.0 downstream --
    see attribute().
    """
    if not sector or not sector.strip():
        return None
    etf = _LOOKUP.get(sector.strip().casefold())
    if etf is None and sector not in _unmapped_seen:
        _unmapped_seen.add(sector)
        logger.info("No sector ETF mapped for %r -- rows will show as %s", sector, UNKNOWN_SECTOR)
    return etf


def attribute(
    stock_pct: float,
    sector: str | None,
    etf_pct_change: dict[str, float],
    market_pct: float | None,
) -> dict:
    """Split one symbol's day into market, sector and stock-specific parts.

    Every attributed field is None when the sector or its ETF price is
    unavailable. Zero would be a much worse default: a sector contribution of
    0.0 reads on the page as "entirely stock-specific", which is a claim about
    the market that missing data does not support.
    """
    etf = etf_for_sector(sector)
    sector_pct = etf_pct_change.get(etf) if etf is not None else None

    if etf is None or sector_pct is None or market_pct is None:
        return {
            "sector": sector if etf is not None else UNKNOWN_SECTOR,
            "etf": etf,
            "sector_pct": sector_pct,
            "market_pct": market_pct,
            "sector_excess_pct": None,
            "stock_specific_pct": None,
            "sector_share": None,
            "verdict": VERDICT_UNKNOWN,
        }

    return {
        "sector": sector,
        "etf": etf,
        "sector_pct": sector_pct,
        "market_pct": market_pct,
        "sector_excess_pct": sector_pct - market_pct,
        "stock_specific_pct": stock_pct - sector_pct,
        "sector_share": sector_share(stock_pct, sector_pct),
        "verdict": verdict_for(stock_pct, sector_pct),
    }


def sector_share(stock_pct: float, sector_pct: float) -> float | None:
    """How much of the move the sector accounts for, as a fraction.

    Only meaningful for a move that actually happened, in the same direction:
    the ratio explodes as stock_pct approaches zero, and flips sign when the
    sector falls while the stock rises, where a negative "share" would read as
    though the sector had contributed backwards. Those are None and 0.0
    respectively rather than a number that lies. Capped at 1.0 so a sector
    that outran the stock does not report 340%.
    """
    if stock_pct <= 0:
        return None
    if sector_pct <= 0:
        return 0.0
    return min(sector_pct / stock_pct, 1.0)


def verdict_for(stock_pct: float, sector_pct: float) -> str:
    """The reading aid. The numbers beside it in the table are the evidence.

    Claims nothing for a name that is not actually up: "how much of this gain
    came from the sector" has no meaning for a stock that fell.
    """
    if stock_pct <= 0:
        return VERDICT_UNKNOWN
    if sector_pct < -SECTOR_NOISE_PCT:
        # The sector fell and the name rose anyway -- the strongest evidence
        # available here that something specific to it is driving the move.
        return VERDICT_AGAINST

    share = sector_share(stock_pct, sector_pct) or 0.0
    if share >= SECTOR_DRIVEN_SHARE:
        return VERDICT_DRIVEN
    if share >= TAILWIND_SHARE or (sector_pct >= SECTOR_STRONG_PCT and share > 0):
        return VERDICT_TAILWIND
    return VERDICT_INDEPENDENT


def sector_rollup(attributed: list[dict], ranked_sectors: list[str | None]) -> list[dict]:
    """Per-sector summary of one view, with breadth.

    `attributed` is the view being analysed, its rows already through
    attribute() and each carrying a `pct_change`. `ranked_sectors` is the
    sector of *every* currently ranked symbol across all views, which is the
    baseline breadth is measured against -- without it "14 of 50" cannot be
    read at all: fourteen healthcare names is unremarkable if healthcare is
    30% of everything ranked, and striking if it is 6%.

    One view at a time, never pooled. bucket_analysis carries the same warning
    for the same reason: a "win" -- and equally a sector average -- means
    opposite things for gainers and losers.
    """
    baseline = _share_by_sector(ranked_sectors)
    total = len(attributed)

    grouped: dict[str, list[dict]] = {}
    for row in attributed:
        grouped.setdefault(row["sector"] or UNKNOWN_SECTOR, []).append(row)

    rows = []
    for sector, members in grouped.items():
        share = len(members) / total if total else 0.0
        base = baseline.get(sector, 0.0)
        rows.append(
            {
                "sector": sector,
                "etf": members[0]["etf"],
                "sector_pct": members[0]["sector_pct"],
                "count": len(members),
                "share": share,
                # How over-represented this sector is among the movers. None
                # rather than infinity when the baseline is zero: a sector
                # appearing nowhere else in the ranked set has no meaningful
                # ratio, only a count.
                "concentration": (share / base) if base > 0 else None,
                "avg_pct": _mean(m["pct_change"] for m in members),
                "avg_stock_specific_pct": _mean(m["stock_specific_pct"] for m in members),
                "verdicts": _verdict_mix(members),
            }
        )
    rows.sort(key=lambda r: (-r["count"], r["sector"]))
    return rows


def _share_by_sector(sectors: list[str | None]) -> dict[str, float]:
    total = len(sectors)
    if not total:
        return {}
    counts: dict[str, int] = {}
    for sector in sectors:
        label = sector if sector and etf_for_sector(sector) else UNKNOWN_SECTOR
        counts[label] = counts.get(label, 0) + 1
    return {label: n / total for label, n in counts.items()}


def _verdict_mix(members: list[dict]) -> dict[str, int]:
    mix: dict[str, int] = {}
    for member in members:
        mix[member["verdict"]] = mix.get(member["verdict"], 0) + 1
    return mix


def _mean(values) -> float | None:
    collected = [v for v in values if v is not None]
    if not collected:
        return None
    return sum(collected) / len(collected)
