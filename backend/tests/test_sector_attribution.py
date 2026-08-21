"""Splitting a move into its market, sector and stock-specific parts.

Two things here are load-bearing and easy to break silently. The three parts
must add back up to the move exactly -- that identity is the whole design, and
without it the stacked bar on the page is decorative rather than true. And a
missing sector must produce None, never 0.0: a zero sector contribution reads
as "entirely stock-specific", which is a claim about the market that absent
data does not support.
"""

import pytest

from app.scanners import sector_attribution as sa


def _etfs(**overrides) -> dict[str, float]:
    prices = {"XLV": 0.4, "XLF": 0.2, "XLK": 1.9, "XLE": -1.2, "SPY": 0.5}
    prices.update(overrides)
    return prices


# --- the mapping ---------------------------------------------------------


def test_every_fmp_sector_maps_to_an_etf():
    for sector in sa.SECTOR_ETFS:
        assert sa.etf_for_sector(sector) is not None, sector


def test_the_eleven_sectors_map_to_eleven_distinct_etfs():
    assert len(set(sa.SECTOR_ETFS.values())) == len(sa.SECTOR_ETFS) == 11


def test_gics_wording_resolves_to_the_same_etf():
    """FMP is the only source today, but a vendor renaming "Healthcare" to
    "Health Care" would otherwise tip a whole sector into Unknown without
    anything failing."""
    assert sa.etf_for_sector("Health Care") == sa.etf_for_sector("Healthcare")
    assert sa.etf_for_sector("Financials") == sa.etf_for_sector("Financial Services")
    assert sa.etf_for_sector("Information Technology") == sa.etf_for_sector("Technology")


def test_lookup_ignores_case_and_padding():
    assert sa.etf_for_sector("  hEaLtHcArE ") == "XLV"


def test_an_absent_sector_has_no_etf():
    for value in (None, "", "   ", "Cryptocurrency Mining"):
        assert sa.etf_for_sector(value) is None, value


# --- the decomposition ---------------------------------------------------


def test_the_three_parts_add_up_to_the_move():
    """The identity the whole page rests on."""
    result = sa.attribute(10.0, "Healthcare", _etfs(), market_pct=0.5)

    assert result["market_pct"] == 0.5
    assert result["sector_excess_pct"] == pytest.approx(-0.1)
    assert result["stock_specific_pct"] == pytest.approx(9.6)
    total = (
        result["market_pct"] + result["sector_excess_pct"] + result["stock_specific_pct"]
    )
    assert total == pytest.approx(10.0)


def test_the_identity_holds_when_the_sector_fell():
    result = sa.attribute(8.0, "Energy", _etfs(), market_pct=0.1)
    total = (
        result["market_pct"] + result["sector_excess_pct"] + result["stock_specific_pct"]
    )

    assert total == pytest.approx(8.0)


def test_stock_specific_is_alpha_against_the_sector():
    """Same operation as benchmark_tracker's alpha_vs_benchmark, measured
    against a different reference."""
    result = sa.attribute(12.0, "Technology", _etfs(), market_pct=0.5)

    assert result["stock_specific_pct"] == pytest.approx(12.0 - 1.9)


# --- missing data must be None, never zero -------------------------------


def test_an_unknown_sector_attributes_nothing():
    """The specific bug this guards: 0.0 would render as "entirely
    stock-specific", asserting something the data does not say."""
    result = sa.attribute(10.0, None, _etfs(), market_pct=0.5)

    assert result["sector"] == sa.UNKNOWN_SECTOR
    assert result["verdict"] == sa.VERDICT_UNKNOWN
    for field in ("sector_pct", "sector_excess_pct", "stock_specific_pct", "sector_share"):
        assert result[field] is None, field


def test_a_known_sector_with_no_etf_price_attributes_nothing():
    """First tick after startup: the mapping resolves, the price has not
    arrived yet."""
    result = sa.attribute(10.0, "Healthcare", {"SPY": 0.5}, market_pct=0.5)

    assert result["etf"] == "XLV"
    assert result["sector_pct"] is None
    assert result["stock_specific_pct"] is None
    assert result["verdict"] == sa.VERDICT_UNKNOWN


def test_a_missing_market_price_attributes_nothing():
    result = sa.attribute(10.0, "Healthcare", _etfs(), market_pct=None)

    assert result["stock_specific_pct"] is None
    assert result["verdict"] == sa.VERDICT_UNKNOWN


# --- the verdict ---------------------------------------------------------


def test_a_flat_sector_leaves_the_move_independent():
    assert sa.verdict_for(8.0, 0.05) == sa.VERDICT_INDEPENDENT


def test_a_falling_sector_is_the_strongest_idiosyncratic_evidence():
    assert sa.verdict_for(8.0, -1.2) == sa.VERDICT_AGAINST


def test_a_sector_barely_down_is_only_noise_not_evidence():
    """Just inside the noise band -- not enough to call it a move against the
    sector."""
    assert sa.verdict_for(8.0, -sa.SECTOR_NOISE_PCT) == sa.VERDICT_INDEPENDENT


def test_half_the_move_is_sector_driven():
    assert sa.verdict_for(4.0, 2.0) == sa.VERDICT_DRIVEN


def test_the_share_boundaries_are_inclusive():
    """Sized so only the share clause can fire: at a 5% move the tailwind
    share is a 0.75% sector day, comfortably under SECTOR_STRONG_PCT."""
    move = 5.0

    assert sa.verdict_for(move, move * sa.SECTOR_DRIVEN_SHARE) == sa.VERDICT_DRIVEN
    assert sa.verdict_for(move, move * sa.TAILWIND_SHARE) == sa.VERDICT_TAILWIND
    assert sa.verdict_for(move, move * sa.TAILWIND_SHARE - 0.01) == sa.VERDICT_INDEPENDENT


def test_a_strong_sector_outvotes_a_small_share():
    """The two clauses overlap on purpose. At a 10% move, even a share below
    the tailwind threshold means the sector itself moved 1.4% -- a real sector
    day, and calling that independent would be wrong however small its share
    of a large move."""
    below_share = 10.0 * sa.TAILWIND_SHARE - 0.1

    assert below_share / 10.0 < sa.TAILWIND_SHARE
    assert below_share >= sa.SECTOR_STRONG_PCT
    assert sa.verdict_for(10.0, below_share) == sa.VERDICT_TAILWIND


def test_a_strong_sector_day_is_a_tailwind_even_behind_a_big_mover():
    """A 40% move is arithmetically never "sector-driven", but a sector up
    more than a percent is still a real wind at its back."""
    verdict = sa.verdict_for(40.0, sa.SECTOR_STRONG_PCT)

    assert verdict == sa.VERDICT_TAILWIND


def test_a_stock_that_did_not_rise_gets_no_verdict():
    """"How much of this gain came from the sector" has no meaning for a name
    that fell."""
    assert sa.verdict_for(0.0, 1.0) == sa.VERDICT_UNKNOWN
    assert sa.verdict_for(-5.0, 1.0) == sa.VERDICT_UNKNOWN


# --- the share -----------------------------------------------------------


def test_share_is_none_for_a_move_that_did_not_happen():
    """Guards the division: the ratio explodes as the move approaches zero."""
    assert sa.sector_share(0.0, 1.0) is None
    assert sa.sector_share(-3.0, 1.0) is None


def test_a_falling_sector_contributed_nothing_rather_than_negatively():
    assert sa.sector_share(8.0, -1.0) == 0.0


def test_share_is_capped_at_the_whole_move():
    """A sector that outran the stock should not report 340%."""
    assert sa.sector_share(1.0, 3.4) == 1.0


# --- the rollup ----------------------------------------------------------


def _row(symbol, sector, pct, etfs=None):
    result = sa.attribute(pct, sector, etfs if etfs is not None else _etfs(), market_pct=0.5)
    result.update({"symbol": symbol, "pct_change": pct})
    return result


def test_rollup_groups_by_sector_and_counts():
    rows = [_row("A", "Healthcare", 10.0), _row("B", "Healthcare", 20.0), _row("C", "Technology", 5.0)]

    rollup = {r["sector"]: r for r in sa.sector_rollup(rows, ["Healthcare"] * 3 + ["Technology"])}

    assert rollup["Healthcare"]["count"] == 2
    assert rollup["Healthcare"]["avg_pct"] == pytest.approx(15.0)
    assert rollup["Technology"]["count"] == 1


def test_concentration_compares_against_every_ranked_name():
    """Fourteen healthcare gainers is unremarkable if healthcare is 30% of
    everything ranked and striking if it is 6% -- the ratio is what makes the
    count readable at all."""
    rows = [_row("A", "Healthcare", 10.0), _row("B", "Healthcare", 12.0)]
    # Healthcare is all of this view but only a quarter of the ranked set.
    ranked = ["Healthcare", "Technology", "Energy", "Industrials"]

    rollup = sa.sector_rollup(rows, ranked)[0]

    assert rollup["share"] == pytest.approx(1.0)
    assert rollup["concentration"] == pytest.approx(4.0)


def test_concentration_is_none_rather_than_infinite_without_a_baseline():
    rows = [_row("A", "Healthcare", 10.0)]

    rollup = sa.sector_rollup(rows, [])[0]

    assert rollup["concentration"] is None


def test_unknown_sector_rows_are_counted_but_not_attributed():
    """Never silently dropped -- a gainer whose profile has not landed is
    still one of today's gainers."""
    rows = [_row("A", "Healthcare", 10.0), _row("B", None, 30.0)]

    rollup = {r["sector"]: r for r in sa.sector_rollup(rows, ["Healthcare", None])}

    assert rollup[sa.UNKNOWN_SECTOR]["count"] == 1
    assert rollup[sa.UNKNOWN_SECTOR]["avg_pct"] == pytest.approx(30.0)
    assert rollup[sa.UNKNOWN_SECTOR]["avg_stock_specific_pct"] is None
    assert rollup[sa.UNKNOWN_SECTOR]["verdicts"] == {sa.VERDICT_UNKNOWN: 1}


def test_rollup_of_nothing_is_empty_rather_than_an_error():
    assert sa.sector_rollup([], []) == []


def test_every_verdict_has_a_rank_for_the_client_to_sort_on():
    verdicts = {
        sa.VERDICT_UNKNOWN,
        sa.VERDICT_AGAINST,
        sa.VERDICT_DRIVEN,
        sa.VERDICT_TAILWIND,
        sa.VERDICT_INDEPENDENT,
    }

    assert verdicts == set(sa.VERDICT_RANK)
