"""Expected share of a typical day's volume traded by a given time of day.

`formulas.rvol` divides today's cumulative volume by `avg_vol_20d`, which is a
*full-day* average (see app.alpaca.universe -- it's the mean of daily bar
volumes). That makes raw RVOL systematically understate early in the session: a
symbol trading at a perfectly ordinary pace at 09:35 has only ~1-2% of its
typical day's volume in, so it reads ~0.02x rather than ~1x. Every
RVOL-dependent behaviour inherits that bias -- the fade-risk discount, the FADE
RISK badge, and `entry_rvol` as recorded into scanner_history.sqlite3 (so the
historical validation is computed on skewed inputs too).

This module supplies the missing denominator: the fraction of a full day's
volume normally complete by time t, so RVOL can be read as "volume so far vs.
volume *normally* in by now".

The curve is built from one liquid reference symbol (SPY by default) rather than
per-symbol. A per-symbol curve would be more precise, but it needs 20+ days of
minute bars for every symbol in the universe on every rebuild -- hundreds of
symbols' worth -- against a market-wide intraday volume shape (the well-known
U: heavy open, quiet midday, heavy close) that individual liquid symbols track
closely. One symbol's curve, refreshed daily, buys most of the accuracy for a
fraction of the data.
"""

import logging
from collections import defaultdict
from datetime import datetime, time, timezone

from app.alpaca.client import AlpacaClients
from app.market_data.bars import get_5m_bars_multi
from app.services.market_clock import ET

logger = logging.getLogger(__name__)

DEFAULT_REFERENCE_SYMBOL = "SPY"
# Calendar days of 5-minute bars to average the curve over.
DEFAULT_LOOKBACK_DAYS = 30
# The shape is stable week to week, so once a day is plenty.
DEFAULT_REFRESH_INTERVAL_SECONDS = 86_400.0

# Floor on the returned fraction. Without it, a fraction near zero (the first
# seconds of premarket, or a malformed curve) would divide volume by ~nothing
# and report a meaningless five-figure RVOL. 0.5% of a typical day is about
# where premarket volume sits for a normal name, so this doubles as the
# premarket default when the curve has no extended-hours buckets.
MIN_FRACTION = 0.005


def build_fraction_curve(bars: list) -> list[tuple[time, float]]:
    """[(bucket_start_et, cumulative_fraction_of_day)] ascending by time.

    Each bar contributes to its own ET time-of-day bucket. Per day we take the
    running cumulative volume at each bucket over that day's *total*, then
    average those per-bucket fractions across days -- rather than summing raw
    volume across days first, which would let one unusually heavy session
    dominate the shape.
    """
    volume_by_day: dict[object, dict[time, float]] = defaultdict(lambda: defaultdict(float))
    for bar in bars:
        stamp = bar.timestamp
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        stamp_et = stamp.astimezone(ET)
        volume_by_day[stamp_et.date()][stamp_et.time().replace(second=0, microsecond=0)] += bar.volume

    fractions_by_bucket: dict[time, list[float]] = defaultdict(list)
    for buckets in volume_by_day.values():
        day_total = sum(buckets.values())
        if day_total <= 0:
            continue
        running = 0.0
        for bucket in sorted(buckets):
            running += buckets[bucket]
            fractions_by_bucket[bucket].append(running / day_total)

    curve = [
        (bucket, sum(values) / len(values))
        for bucket, values in sorted(fractions_by_bucket.items())
        if values
    ]
    # Averaging per-bucket independently can leave tiny non-monotonic dips when
    # different days have different bucket coverage (a holiday half-day, a
    # symbol that didn't trade in some bucket). A cumulative curve must never
    # decrease, so clamp it upward as we go.
    monotonic: list[tuple[time, float]] = []
    highest = 0.0
    for bucket, fraction in curve:
        highest = max(highest, fraction)
        monotonic.append((bucket, min(1.0, highest)))
    return monotonic


def fraction_at(curve: list[tuple[time, float]], now: datetime) -> float:
    """Fraction of a typical day's volume normally complete by `now` (ET).

    Returns 1.0 for an empty curve, which makes RVOL fall back to exactly the
    un-normalized full-day behaviour rather than producing something wrong --
    the safe default whenever the curve hasn't been built yet.
    """
    if not curve:
        return 1.0
    now_et = now.astimezone(ET).time()
    fraction = None
    for bucket, value in curve:
        if bucket > now_et:
            break
        fraction = value
    if fraction is None:
        # Before the curve's first bucket -- early premarket.
        return MIN_FRACTION
    return max(MIN_FRACTION, min(1.0, fraction))


class VolumeProfileCache:
    """Lazily-built, slowly-refreshed intraday volume curve.

    Same shape as the other engine-side caches (ensure_fresh + a getter). A
    failed build is not an error worth surfacing: `fraction_at` returns 1.0 and
    RVOL simply behaves as it did before this existed.
    """

    def __init__(
        self,
        clients: AlpacaClients,
        reference_symbol: str = DEFAULT_REFERENCE_SYMBOL,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
        refresh_interval: float = DEFAULT_REFRESH_INTERVAL_SECONDS,
    ) -> None:
        self._clients = clients
        self._reference_symbol = reference_symbol
        self._lookback_days = lookback_days
        self._refresh_interval = refresh_interval
        self._curve: list[tuple[time, float]] = []
        self._built_at: float = -float("inf")

    @property
    def curve(self) -> list[tuple[time, float]]:
        return self._curve

    async def ensure_fresh(self, now_monotonic: float | None = None) -> None:
        import time as _time

        now = now_monotonic if now_monotonic is not None else _time.monotonic()
        if now - self._built_at < self._refresh_interval:
            return
        # Set before awaiting so a slow/failing build doesn't get retried on
        # every poll tick.
        self._built_at = now
        try:
            bars_by_symbol = await get_5m_bars_multi(
                self._clients, [self._reference_symbol], lookback_days=self._lookback_days
            )
        except Exception:
            logger.exception("Intraday volume profile fetch failed for %s", self._reference_symbol)
            return

        bars = bars_by_symbol.get(self._reference_symbol) or []
        curve = build_fraction_curve(bars)
        if not curve:
            logger.warning(
                "Intraday volume profile came back empty for %s -- RVOL stays un-normalized",
                self._reference_symbol,
            )
            return
        self._curve = curve
        logger.info(
            "Intraday volume profile built from %s: %d buckets over ~%d calendar days",
            self._reference_symbol,
            len(curve),
            self._lookback_days,
        )

    def fraction_at(self, now: datetime) -> float:
        return fraction_at(self._curve, now)
