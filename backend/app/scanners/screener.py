"""User-defined screening over the live scanner rows -- filter on any
field, sort on any field, instead of choosing between three fixed views.

Why this exists. "gainers", "losers" and "most_active" are three hardcoded
questions: each one bakes in its own filter (pct_change > 0, < 0, none),
its own sort, and a top-50 cut, in Python (see engine._rank_gainers and
friends). Wanting "under $10, RVOL over 3, up more than 5%, not stale" --
an ordinary screener question -- meant editing source. This module turns
the question itself into data: a list of filters plus a sort, which a UI
can build and a user can change.

The field registry below is the load-bearing part. Both front ends read
FIELDS from the API and build their filter controls and column pickers
from it, so adding a screenable field here makes it appear in both UIs
with no UI change at all. Nothing about the available fields, their types,
or which operators apply to them is written down in the React app or the
Dash app.

Deliberately v1: only fields computed for the *whole* universe on every
poll are screenable. Market cap, short interest, country, recent headline
and momentum are attached only to symbols already in a ranked
view (see engine._attach_fundamentals/_attach_news/_attach_momentum),
because fetching them for all ~2000 universe symbols costs far more API
calls than the ranked ~150 do. Screening on a field that is None for 93%
of the universe would silently return "no matches" rather than an error,
which is worse than not offering it. Float is the exception and is
supplied as a derived field -- FundamentalsCache holds it universe-wide
from one bulk file (see fundamentals.float_bulk), even though the row
object only carries it for ranked symbols.
"""

from dataclasses import dataclass, field as dataclass_field
from typing import Any, Callable

from pydantic import BaseModel

from app.scanners.schemas import ScannerRow

NUMBER = "number"
PERCENT = "percent"
CURRENCY = "currency"
BOOLEAN = "boolean"
TEXT = "text"

# Which operators make sense for which field type. Sent to the clients so a
# UI never offers "greater than" on a boolean or "is true" on a price.
OPERATORS_BY_TYPE: dict[str, tuple[str, ...]] = {
    NUMBER: ("gt", "gte", "lt", "lte", "between"),
    PERCENT: ("gt", "gte", "lt", "lte", "between"),
    CURRENCY: ("gt", "gte", "lt", "lte", "between"),
    BOOLEAN: ("is_true", "is_false"),
    TEXT: ("eq", "ne", "contains", "in"),
}


@dataclass(frozen=True)
class FieldSpec:
    """One screenable column: how to read it, how to label it, and what can
    be asked of it.

    `derived` marks a field that isn't on ScannerRow and has to be supplied
    per screen run (see run_screen's `derived` argument) -- the escape hatch
    for values that live outside the row object, like universe-wide float or
    a rank score that needs the news cache to compute.
    """

    name: str
    label: str
    type: str
    derived: bool = False
    # Sort-only fields still appear in a column picker but never in the
    # filter builder -- used where a value is meaningful to order by but
    # awkward to type a threshold for.
    sortable_only: bool = False

    @property
    def operators(self) -> tuple[str, ...]:
        return () if self.sortable_only else OPERATORS_BY_TYPE[self.type]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "type": self.type,
            "operators": list(self.operators),
            "derived": self.derived,
        }


FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("symbol", "Symbol", TEXT),
    FieldSpec("exchange", "Exchange", TEXT),
    FieldSpec("last_price", "Price", CURRENCY),
    FieldSpec("pct_change", "Change %", PERCENT),
    # The overnight gap specifically -- see ScannerRow.gap_pct for why this
    # is not the same question as Change %.
    FieldSpec("gap_pct", "Gap % (overnight)", PERCENT),
    FieldSpec("volume_today", "Volume", NUMBER),
    FieldSpec("avg_vol_20d", "Avg Volume (20d)", NUMBER),
    FieldSpec("rvol", "Rel Volume", NUMBER),
    FieldSpec("dollar_volume_today", "Dollar Volume", CURRENCY),
    FieldSpec("day_high", "Day High", CURRENCY),
    FieldSpec("day_low", "Day Low", CURRENCY),
    FieldSpec("spread_pct", "Spread %", PERCENT),
    # Volume *rate* fields -- see app.market_data.volume_surge. rvol above is
    # cumulative since the open and only climbs; these describe right now.
    # Populated for ranked and screened rows during the regular session only.
    FieldSpec("volume_1h", "Volume (1h)", NUMBER),
    FieldSpec("volume_surge", "Volume Surge (vs prior 1h)", NUMBER),
    FieldSpec("rvol_1h", "Rel Volume (1h)", NUMBER),
    # The same measure as rvol_1h but at the screen's own window rather than
    # the global one -- derived, because it has to be recomputed per screen.
    # No window in the label: it is whatever the screen asked for, and the
    # response's meta.window_minutes carries the number so a column header
    # can say "RVol (30m)" without the registry pretending to know.
    FieldSpec("rvol_window", "Rel Volume (window)", NUMBER, derived=True),
    # rvol_window over rvol -- what share of today's volume landed in the
    # window, against the share that normally would. Ranks identically
    # (rho = 1.0000, measured) to "window volume / today's volume" while
    # staying comparable across times of day, which the raw share is not:
    # measured medians run 52.6% at 11:00 down to 13.7% at 15:00, so a
    # fixed threshold on the raw share would select the clock.
    FieldSpec("volume_concentration", "Volume Concentration", NUMBER, derived=True),
    # Did the most recent 5-minute bar close up. Exists so the long bias
    # that used to be hardcoded inside the volume maths can be stated as a
    # filter instead -- see volume_surge.is_green.
    FieldSpec("is_green_candle", "Green Candle", BOOLEAN, derived=True),
    FieldSpec("is_hod", "At High of Day", BOOLEAN),
    FieldSpec("is_lod", "At Low of Day", BOOLEAN),
    FieldSpec("is_fade_risk", "Fade Risk", BOOLEAN),
    # Universe-wide from the asset record rather than a per-symbol fetch, so
    # it satisfies the module docstring's rule about what may be screenable.
    FieldSpec("shortable", "Shortable", BOOLEAN),
    FieldSpec("is_stale", "Stale Price", BOOLEAN),
    # How much of the consolidated tape our own feed saw today. Low values
    # mean every volume *level* on the row is a small fraction of reality.
    # See FundamentalsCache.tape_coverage_pct.
    FieldSpec("tape_coverage_pct", "Tape Coverage %", PERCENT),
    # Universe-wide from the bulk shares-float file rather than the row --
    # see the module docstring.
    FieldSpec("float_shares", "Float", NUMBER, derived=True),
    # Also universe-wide, and for the same reason float is: FundamentalsCache
    # holds FINRA's bulk short-interest file for every symbol, so dividing it
    # by the bulk float gives a screenable percentage without widening any
    # per-symbol fetch. row.short_interest_pct exists too but is only filled
    # in for already-ranked symbols, so the derived map is the one that works
    # across the whole universe.
    FieldSpec("short_interest_pct", "Short % of Float", PERCENT, derived=True),
    # The live ranking magnitude, so a screen can still order by exactly
    # what the fixed views ordered by. Derived because it needs the news
    # cache (catalyst boost) that only the engine holds.
    FieldSpec("rank_score", "Rank Score", NUMBER, derived=True),
)

FIELDS_BY_NAME: dict[str, FieldSpec] = {f.name: f for f in FIELDS}


class Filter(BaseModel):
    """One condition. `value` is a second bound's partner for "between",
    a list for "in", a string for text ops, and a number otherwise;
    booleans carry no value at all.
    """

    field: str
    op: str
    value: Any = None
    value2: Any = None


class Screen(BaseModel):
    """One screen. `window_minutes` is the odd one out: the other three
    describe how rows are chosen and ordered, this one changes what a
    column *means*.

    It sets the trailing window behind rvol_window (and, through it,
    volume_concentration) -- "3x normal for the last 30 minutes" instead of
    for the last hour. A property of the screen rather than of a filter,
    for the same reason limit and sort_by are: a screen has one time
    context, and two filters disagreeing about it would leave the displayed
    column ambiguous. None means "use the global default"
    (settings.scanner_volume_surge_window_minutes), which is what the fixed
    views run on.
    """

    filters: list[Filter] = []
    sort_by: str = "pct_change"
    descending: bool = True
    limit: int = 100
    window_minutes: int | None = None


@dataclass
class ScreenResult:
    rows: list[ScannerRow]
    # How many rows matched before `limit` truncated them -- a UI needs to
    # say "showing 100 of 412", and without this a full page of results is
    # indistinguishable from exactly-a-full-page of results.
    total_matched: int
    values: dict[str, dict[str, float | None]] = dataclass_field(default_factory=dict)


def _compare(value: Any, op: str, target: Any, target2: Any = None) -> bool:
    """A single condition against a single value.

    A missing value never matches a numeric or text condition. That's the
    conservative reading: "float under 20M" should not include symbols whose
    float is unknown, or a screen would quietly fill with rows that were
    never actually checked.
    """
    if op == "is_true":
        return bool(value)
    if op == "is_false":
        return not bool(value)
    if value is None:
        return False

    if op in ("eq", "ne", "contains", "in"):
        text = str(value).upper()
        if op == "eq":
            return text == str(target).upper()
        if op == "ne":
            return text != str(target).upper()
        if op == "contains":
            return str(target).upper() in text
        return text in {str(t).upper() for t in (target or [])}

    try:
        number = float(value)
        bound = float(target)
    except (TypeError, ValueError):
        return False

    if op == "gt":
        return number > bound
    if op == "gte":
        return number >= bound
    if op == "lt":
        return number < bound
    if op == "lte":
        return number <= bound
    if op == "between":
        try:
            upper = float(target2)
        except (TypeError, ValueError):
            return False
        # Order-insensitive, so a UI doesn't have to police which box is
        # which -- "between 50 and 10" means the same range as 10 to 50.
        return min(bound, upper) <= number <= max(bound, upper)
    return False


def _value_of(row: ScannerRow, name: str, derived: dict[str, dict[str, Any]]) -> Any:
    spec = FIELDS_BY_NAME.get(name)
    if spec is None:
        return None
    if spec.derived:
        return derived.get(name, {}).get(row.symbol)
    return getattr(row, name, None)


def apply_filters(
    rows, filters: list[Filter], derived: dict[str, dict[str, Any]] | None = None
) -> list[ScannerRow]:
    """Every filter must pass -- conditions are ANDed.

    No OR support in v1 on purpose: an AND-only list is what a flat filter
    builder can express unambiguously, and OR needs grouping/precedence in
    both the model and the UI to mean anything. Unknown field names are
    ignored rather than raising, so a saved screen referring to a field that
    has since been removed still runs instead of breaking.
    """
    derived = derived or {}
    known = [f for f in filters if f.field in FIELDS_BY_NAME]
    return [
        row
        for row in rows
        if all(
            _compare(_value_of(row, f.field, derived), f.op, f.value, f.value2)
            for f in known
        )
    ]


def sort_rows(
    rows: list[ScannerRow],
    sort_by: str,
    descending: bool = True,
    derived: dict[str, dict[str, Any]] | None = None,
) -> list[ScannerRow]:
    """Rows with no value for the sort field always sink to the bottom,
    whichever direction the sort runs -- the same convention the Dash
    tables already use (see _sorted_rows in the backtest page). A None
    shouldn't win a "highest first" sort by being unorderable.
    """
    derived = derived or {}
    if sort_by not in FIELDS_BY_NAME:
        sort_by = "pct_change"

    with_value, without_value = [], []
    for row in rows:
        target = with_value if _value_of(row, sort_by, derived) is not None else without_value
        target.append(row)

    def key(row):
        value = _value_of(row, sort_by, derived)
        # Text fields order as text, everything else numerically. The two
        # never mix within one field, so one key function covers both.
        return value.upper() if isinstance(value, str) else float(value)

    with_value.sort(key=key, reverse=descending)
    return with_value + without_value


def run_screen(
    rows, screen: Screen, derived: dict[str, dict[str, Any]] | None = None
) -> ScreenResult:
    """Filter, sort, then cut to `limit`."""
    derived = derived or {}
    matched = apply_filters(rows, screen.filters, derived)
    ordered = sort_rows(matched, screen.sort_by, screen.descending, derived)
    limited = ordered[: max(1, screen.limit)]
    # Only the derived values for rows actually returned -- a client needs
    # them to render those columns, and shipping the whole universe's float
    # map on every screen would dwarf the rows themselves.
    symbols = {r.symbol for r in limited}
    trimmed = {
        name: {s: v for s, v in values.items() if s in symbols}
        for name, values in derived.items()
    }
    return ScreenResult(rows=limited, total_matched=len(matched), values=trimmed)


# The three fixed views, expressed as ordinary screens. Keeping them as data
# rather than code is the point: a user can open one, see exactly what it
# asks, and change it. `min_dollar_volume` is applied by the caller (it comes
# from settings, not from a preset), matching engine._tradable.
PRESETS: dict[str, dict] = {
    "gainers": {
        "label": "Top Gainers",
        "description": "Up on the day, ordered by the live ranking score (gap %, catalyst boost, fade-risk discount).",
        "screen": Screen(
            filters=[Filter(field="pct_change", op="gt", value=0)],
            sort_by="rank_score",
            descending=True,
            limit=50,
        ),
    },
    "losers": {
        "label": "Top Losers",
        "description": "Down on the day, ordered by the live ranking score. No catalyst boost -- see formulas._CATALYST_BOOST.",
        "screen": Screen(
            filters=[Filter(field="pct_change", op="lt", value=0)],
            sort_by="rank_score",
            descending=False,
            limit=50,
        ),
    },
    "most_active": {
        "label": "Most Active",
        "description": "Ordered by dollar volume traded today -- direction-agnostic, so no change filter.",
        "screen": Screen(filters=[], sort_by="dollar_volume_today", descending=True, limit=50),
    },
    "late_volume_surge": {
        "label": "Volume Accelerating",
        "description": (
            "Trading at 2x+ the volume normal for this time of day and still green -- catches a "
            "name picking up late, which cumulative RVOL cannot see. Deliberately filters on "
            "rvol_window, not volume_surge: session volume is U-shaped, so near the close the raw "
            "hour-over-hour ratio is above 1 for most of the market. The green-candle condition "
            "is a filter here rather than a silent rule inside the volume maths -- edit it away "
            "and this screen will happily show you the names being dumped on heavy volume."
        ),
        "screen": Screen(
            filters=[
                Filter(field="rvol_window", op="gt", value=2),
                Filter(field="pct_change", op="gt", value=0),
                Filter(field="is_green_candle", op="is_true"),
            ],
            sort_by="rvol_window",
            descending=True,
            limit=50,
        ),
    },
    "moderate_movers": {
        "label": "Moderate Movers (3-8%)",
        "description": (
            "Up 3-8% on the day -- the only band that survived an out-of-sample test. "
            "Measured intraday (enter on a 5-minute bar, exit at the close) it won 52.0% "
            "in-sample and 58.7% out-of-sample at +0.31% alpha, the one result here that got "
            "better on unseen days rather than falling apart. Below 3% there is nothing to "
            "trade; above 8% it inverts hard (34.3% out-of-sample at 8-13%). "
            "An upper bound is the unusual part of this screen and the part that carries the "
            "finding -- 'up a lot' is not a stronger version of this, it is the failure case. "
            "Sorted by dollar volume rather than by move size: nothing ordered outcomes within "
            "the band out-of-sample, so the sort optimises for what is left, which is fill "
            "quality. The edge is small enough that spread can eat it -- consider adding a "
            "spread_pct filter before trading it."
        ),
        "screen": Screen(
            filters=[Filter(field="pct_change", op="between", value=3, value2=8),
                     Filter(field="dollar_volume_today", op="gt", value=2_000_000)],
            sort_by="dollar_volume_today",
            descending=True,
            limit=50,
        ),
    },
    "low_float_runners": {
        "label": "Low Float Runners",
        "description": "Under 20M float, up 5%+, trading at least 3x normal volume -- the setup float was made screenable for.",
        "screen": Screen(
            filters=[
                Filter(field="float_shares", op="lt", value=20_000_000),
                Filter(field="pct_change", op="gt", value=5),
                Filter(field="rvol", op="gt", value=3),
            ],
            sort_by="pct_change",
            descending=True,
            limit=50,
        ),
    },
}


def preset_list() -> list[dict]:
    return [
        {"name": name, "label": p["label"], "description": p["description"], "screen": p["screen"].model_dump()}
        for name, p in PRESETS.items()
    ]


def fields_payload() -> list[dict]:
    return [f.to_dict() for f in FIELDS]
