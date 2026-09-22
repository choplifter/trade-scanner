"""Move Attribution: which SPY/QQQ moves were outsized, and what else was
happening when they were -- a macro release, a jump in the 10-year, a VIX
spike, a mega-cap's earnings, an options expiry.

It reports coincidence, not cause. A candle that sits on a CPI release
while yields jump 12 bp gets both listed; which one "did it" is left to the
reader. What the report can say with numbers is how much more often each
factor shows up on big moves than on ordinary ones (the lift), which is the
honest version of "CPI moves the market".

Two modes, because the question is asked at two scales:
- daily: close-to-close moves over years, each split into the overnight gap
  and the session, against the real 10-year and VIX closes;
- intraday: 5m or 15m candles over recent weeks, where a release shows up
  at its minute. The 10-year here is estimated from IEF, the 7-10 year
  Treasury ETF, whose minute history Alpaca has: yields move opposite to
  its price by about its duration (a 1 % IEF move is about 14 bp), which is
  why those figures are marked as approximate.

"Big" is measured against the recent past, not a fixed percentage: a 1 %
day means one thing when the last month's days ran 0.4 % and another when
they ran 1.5 %. Intraday candles are judged against the same part of the
session (premarket, regular, after-hours) over the last twenty sessions --
otherwise the 08:30 CPI candle, the most interesting candle of the day,
would always look small next to regular-hours ones.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from datetime import time as clock
from zoneinfo import ZoneInfo

from app.attribution.sources import Release, opex_label

NY = ZoneInfo("America/New_York")

# IEF's effective duration in years: a 1 % price move is ~1/7 % of yield.
IEF_DURATION = 7.0

# What counts as a sharp 10-year move, in bp: a day's typical move is ~7 bp
# and 1.5 of those is sharp, scaled by the square root of time to the
# candle with a 2 bp floor -- the same rule as the chart's 10Y markers
# (app.indicators.ten_year_moves).
def yield_threshold_bp(candle_minutes: float) -> float:
    return max(2.0, 1.5 * 7.0 * math.sqrt(candle_minutes / 390))


VIX_SPIKE_PCT = 10.0


@dataclass
class Candle:
    """The part of a bar the report needs, with its start in UTC."""

    at: datetime
    open: float
    close: float


@dataclass
class Row:
    """One candle (or day) of the report, big or not -- the ordinary ones
    are the base rate every lift is measured against."""

    at: datetime
    end: datetime
    ret_pct: float
    z: float | None
    session: str  # "day" | "pre" | "regular" | "post"
    gap_pct: float | None = None
    session_pct: float | None = None
    yield_bp: float | None = None
    yield_estimated: bool = False
    vix_pct: float | None = None
    releases: list[str] = field(default_factory=list)
    context: list[str] = field(default_factory=list)
    factors: set[str] = field(default_factory=set)
    big: bool = False
    # False before the first date any release source covers: there, "no
    # release" means "not known", and counting it as "none" would make
    # every release look rarer than it is.
    releases_known: bool = True


@dataclass
class FactorStat:
    name: str
    big_with: int
    big_total: int
    all_with: int
    all_total: int
    avg_abs_with: float | None
    avg_abs_without: float | None

    @property
    def lift(self) -> float | None:
        if not self.big_total or not self.all_with:
            return None
        return (self.big_with / self.big_total) / (self.all_with / self.all_total)


@dataclass
class Report:
    symbol: str
    mode: str
    candle_minutes: int
    rows: list[Row]
    stats: list[FactorStat]
    # Of the big moves with a sharp yield move, how many went the other way
    # from yields (price down while yields up, or the reverse).
    yield_opposite: tuple[int, int]
    notes: list[str]

    @property
    def big(self) -> list[Row]:
        return [r for r in self.rows if r.big]


# --- sizing moves ----------------------------------------------------------


class _Rolling:
    """Standard deviation over the last `size` values, in O(1) per step."""

    def __init__(self, size: int) -> None:
        self._values: deque[float] = deque()
        self._size = size
        self._sum = 0.0
        self._squares = 0.0

    def stdev(self) -> float | None:
        n = len(self._values)
        if n < max(5, self._size // 4):
            return None
        mean = self._sum / n
        var = max(0.0, self._squares / n - mean * mean) * n / (n - 1)
        return math.sqrt(var) if var > 0 else None

    def add(self, value: float) -> None:
        self._values.append(value)
        self._sum += value
        self._squares += value * value
        if len(self._values) > self._size:
            old = self._values.popleft()
            self._sum -= old
            self._squares -= old * old


def session_of(at: datetime) -> str:
    t = at.astimezone(NY).time()
    if t < clock(9, 30):
        return "pre"
    if t < clock(16, 0):
        return "regular"
    return "post"


def daily_rows(bars: list[Candle], k: float, window: int = 20) -> list[Row]:
    """Close-to-close, each against the previous `window` days' spread."""
    rows: list[Row] = []
    spread = _Rolling(window)
    for prev, bar in zip(bars, bars[1:]):
        ret = (bar.close / prev.close - 1) * 100
        sd = spread.stdev()
        z = ret / sd if sd else None
        rows.append(
            Row(
                at=bar.at,
                end=bar.at + timedelta(days=1),
                ret_pct=ret,
                z=z,
                session="day",
                gap_pct=(bar.open / prev.close - 1) * 100,
                session_pct=(bar.close / bar.open - 1) * 100,
                big=z is not None and abs(z) >= k,
            )
        )
        spread.add(ret)
    return rows


def intraday_rows(bars: list[Candle], minutes: int, k: float, sessions: int = 20) -> list[Row]:
    """Candle to candle, each against the same part of the session over the
    last `sessions` sessions (as a count of candles of that part)."""
    per_day = {"pre": 330 // minutes, "regular": 390 // minutes, "post": 240 // minutes}
    spread = {part: _Rolling(max(1, n) * sessions) for part, n in per_day.items()}
    rows: list[Row] = []
    for prev, bar in zip(bars, bars[1:]):
        # A candle following a gap (the first premarket candle after the
        # night, or after a missing bar) would carry hours of drift as if it
        # were one candle's move; it is left out, not sized.
        if bar.at - prev.at > timedelta(minutes=minutes * 3):
            continue
        part = session_of(bar.at)
        ret = (bar.close / prev.close - 1) * 100
        sd = spread[part].stdev()
        z = ret / sd if sd else None
        rows.append(
            Row(
                at=bar.at,
                end=bar.at + timedelta(minutes=minutes),
                ret_pct=ret,
                z=z,
                session=part,
                big=z is not None and abs(z) >= k,
            )
        )
        spread[part].add(ret)
    return rows


# --- what else happened ----------------------------------------------------


def _ny_day(at: datetime) -> date:
    return at.astimezone(NY).date()


def _previous(days: list[date], day: date) -> date | None:
    """The trading day before `day` in a sorted list of trading days."""
    lo, hi = 0, len(days)
    while lo < hi:
        mid = (lo + hi) // 2
        if days[mid] < day:
            lo = mid + 1
        else:
            hi = mid
    return days[lo - 1] if lo > 0 else None


def attribute(
    rows: list[Row],
    *,
    mode: str,
    candle_minutes: int,
    releases: list[Release],
    earnings: dict[date, list[str]],
    ten_year: dict[date, float] | None = None,
    vix: dict[date, float] | None = None,
    ief: dict[datetime, float] | None = None,
    releases_known_from: datetime | None = None,
) -> None:
    """Fill in every row's factors, in place.

    `ten_year`/`vix` are daily closes (daily mode); `ief` is IEF's close by
    candle start (intraday mode), from which the yield move is estimated.
    """
    trading_days = sorted({_ny_day(r.at) for r in rows})
    releases_by_day: dict[date, list[Release]] = {}
    for rel in releases:
        releases_by_day.setdefault(_ny_day(rel.at), []).append(rel)
    ief_times = sorted(ief) if ief else []
    # Each index against its own previous close, on its own calendar -- the
    # report's first row has no previous row, but the 10-year had a close.
    ten_year_days = sorted(ten_year) if ten_year else []
    vix_days = sorted(vix) if vix else []
    threshold = yield_threshold_bp(candle_minutes if mode == "intraday" else 390)

    for row in rows:
        day = _ny_day(row.at)
        prev_day = _previous(trading_days, day)
        row.releases_known = releases_known_from is None or row.at >= releases_known_from

        # Releases: in daily mode anything that day; intraday, the candle
        # the release minute falls in, and the rest of that day as context.
        todays = releases_by_day.get(day, [])
        if mode == "daily":
            row.releases = sorted({r.label for r in todays})
        else:
            row.releases = sorted({r.label for r in todays if row.at <= r.at < row.end})
            later = sorted({r.label for r in todays if r.label not in row.releases})
            if later:
                row.context.append(f"{' · '.join(later)} day")
        for label in row.releases:
            row.factors.add(label)
        if row.releases:
            row.factors.add("Any release")

        # Earnings: FMP has the day, not the hour, so the report day and the
        # one after are both the reaction.
        reporters = sorted(set(earnings.get(day, [])) | set(earnings.get(prev_day, []) if prev_day else []))
        if reporters:
            if mode == "daily":
                row.factors.add("Mega-cap earnings")
            row.context.append(f"Earnings: {', '.join(reporters)}")

        opex = opex_label(day)
        if opex:
            if mode == "daily":
                row.factors.add("Options expiry")
            row.context.append(opex)

        # The 10-year and the VIX.
        if mode == "daily" and ten_year and day in ten_year:
            before = _previous(ten_year_days, day)
            if before is not None:
                row.yield_bp = (ten_year[day] - ten_year[before]) * 100
        if mode == "daily" and vix and day in vix:
            before = _previous(vix_days, day)
            if before is not None and vix[before]:
                row.vix_pct = (vix[day] / vix[before] - 1) * 100
                if row.vix_pct >= VIX_SPIKE_PCT:
                    row.factors.add(f"VIX +{VIX_SPIKE_PCT:.0f}%")
        if mode == "intraday" and ief:
            prev_at = _previous_time(ief_times, row.at)
            if prev_at is not None and row.at in ief and row.at - prev_at <= timedelta(minutes=candle_minutes * 3):
                ief_ret = ief[row.at] / ief[prev_at] - 1
                row.yield_bp = -ief_ret / IEF_DURATION * 10_000
                row.yield_estimated = True
        if row.yield_bp is not None and abs(row.yield_bp) >= threshold:
            row.factors.add(f"10Y ±{threshold:.0f}bp+")


def _previous_time(times: list[datetime], at: datetime) -> datetime | None:
    lo, hi = 0, len(times)
    while lo < hi:
        mid = (lo + hi) // 2
        if times[mid] < at:
            lo = mid + 1
        else:
            hi = mid
    return times[lo - 1] if lo > 0 else None


RELEASE_FACTORS = frozenset({"CPI", "NFP", "GDP", "PCE", "FOMC", "Any release"})


def factor_stats(rows: list[Row]) -> list[FactorStat]:
    """Per factor: how often it sits on a big move against how often it
    sits on any candle, and the average absolute move with and without it.
    Release factors are measured only over the stretch where releases are
    known at all (see Row.releases_known)."""
    names = sorted({f for r in rows for f in r.factors})
    stats = []
    for name in names:
        pool = [r for r in rows if r.releases_known] if name in RELEASE_FACTORS else rows
        big = [r for r in pool if r.big]
        with_f = [r for r in pool if name in r.factors]
        without = [r for r in pool if name not in r.factors]
        stats.append(
            FactorStat(
                name=name,
                big_with=sum(1 for r in big if name in r.factors),
                big_total=len(big),
                all_with=len(with_f),
                all_total=len(pool),
                avg_abs_with=sum(abs(r.ret_pct) for r in with_f) / len(with_f) if with_f else None,
                avg_abs_without=sum(abs(r.ret_pct) for r in without) / len(without) if without else None,
            )
        )
    stats.sort(key=lambda s: (s.lift or 0), reverse=True)
    return stats


def yield_opposite(rows: list[Row], threshold_bp: float) -> tuple[int, int]:
    """Of the big moves with a sharp yield move: (price against yields, all)."""
    sharp = [r for r in rows if r.big and r.yield_bp is not None and abs(r.yield_bp) >= threshold_bp]
    opposite = sum(1 for r in sharp if (r.ret_pct > 0) != (r.yield_bp > 0))
    return opposite, len(sharp)


def to_utc(at: datetime) -> datetime:
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)
