"""Persistent (SQLite) log of scanner appearances and periodic follow-up
price snapshots, so "which scanner matches performed best" can be answered
across restarts and over weeks -- unlike ScannerBenchmarkTracker (see
benchmark_tracker.py), which is deliberately in-memory-only, capped at 300
entries, and keyed by "first time ever seen since this process started"
rather than per trading day.

All I/O here is synchronous stdlib sqlite3, always called via
asyncio.to_thread from ScannerEngine -- same pattern already used for
alpaca-py's blocking client calls (see engine.py's _poll_once). A fresh
connection is opened per call rather than held open for the process
lifetime: call volume is a handful of times per poll tick (every 5-15s), so
the ~1ms connect overhead is negligible next to the safety of not having to
reason about a single sqlite3.Connection being shared across threads.
"""

import asyncio
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.market_data.news import is_roundup_headline
from app.scanners import bucket_analysis
from app.services.market_clock import ET

_SCHEMA = """
CREATE TABLE IF NOT EXISTS appearances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    view TEXT NOT NULL,
    trading_date TEXT NOT NULL,
    entry_price REAL NOT NULL,
    entry_pct_change REAL NOT NULL,
    entry_rvol REAL NOT NULL,
    benchmark_entry_price REAL,
    entry_headline TEXT,
    first_seen_at TEXT NOT NULL,
    UNIQUE(symbol, view, trading_date)
);
CREATE INDEX IF NOT EXISTS idx_appearances_trading_date ON appearances(trading_date);

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    appearance_id INTEGER NOT NULL REFERENCES appearances(id),
    price REAL NOT NULL,
    benchmark_price REAL,
    minutes_since_entry REAL NOT NULL,
    snapshot_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_appearance_id ON snapshots(appearance_id);
"""

# Migrates a pre-existing DB from before entry_headline existed -- the
# CREATE TABLE above is a no-op against an already-created table, so this
# is what actually adds the column for an install that predates it. A no-op
# on a fresh DB, since the CREATE TABLE already includes the column. SQLite's
# ALTER TABLE has no ADD COLUMN IF NOT EXISTS, so the existence check has to
# happen in Python via PRAGMA table_info instead.
_COLUMN_MIGRATIONS = [
    ("appearances", "entry_headline", "TEXT"),
]

# Target checkpoints for the aggregated performance view -- "latest" isn't a
# fixed offset, it's whatever the most recent snapshot for that appearance
# happens to be (approximates end-of-day for appearances the poll loop has
# been tracking all session, without needing to detect the actual trading-day
# close boundary per appearance).
_HORIZON_MINUTES = {"30m": 30.0, "60m": 60.0}
_LEADERBOARD_SIZE = 15

# Baseline correlations the catalyst-boost/fade-risk ranking change
# (commit 1ce30a2, "Boost catalyst headlines and flag RVOL fade-risk in
# scanner ranking") was based on -- a one-off analysis of 1447 historical
# picks in this same table, on 2026-08-12. See compute_ranking_drift: this
# is a comparison point for re-checking whether fresh data since deploy
# still supports the same direction/magnitude, not a certainty that should
# be assumed to hold forever.
_DEPLOY_DATE = "2026-08-12"
_BASELINE_CATALYST_WIN_RATE_DELTA_PP = 8.5
_BASELINE_FADE_RISK_WIN_RATE = 25.6
_BASELINE_FADE_RISK_AVG_RETURN = -10.38
# Must match formulas._FADE_RISK_RVOL -- duplicated rather than imported,
# same as bucket_analysis.RVOL_BUCKETS' own ">15x" boundary.
_FADE_RISK_RVOL_THRESHOLD = 15.0


@dataclass
class NewAppearance:
    symbol: str
    view: str
    entry_price: float
    entry_pct_change: float
    entry_rvol: float
    benchmark_entry_price: float | None
    entry_headline: str | None = None


class ScannerHistoryStore:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_schema_sync(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            for table, column, col_type in _COLUMN_MIGRATIONS:
                existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
                if column not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")

    async def init_schema(self) -> None:
        await asyncio.to_thread(self._init_schema_sync)

    def _record_appearances_sync(self, entries: list[NewAppearance], now: datetime) -> None:
        trading_date = now.astimezone(ET).date().isoformat()
        first_seen_at = now.isoformat()
        with self._connect() as conn:
            conn.executemany(
                """INSERT OR IGNORE INTO appearances
                   (symbol, view, trading_date, entry_price, entry_pct_change,
                    entry_rvol, benchmark_entry_price, entry_headline, first_seen_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        e.symbol,
                        e.view,
                        trading_date,
                        e.entry_price,
                        e.entry_pct_change,
                        e.entry_rvol,
                        e.benchmark_entry_price,
                        e.entry_headline,
                        first_seen_at,
                    )
                    for e in entries
                ],
            )

    async def record_appearances(self, entries: list[NewAppearance]) -> None:
        """INSERT OR IGNORE on (symbol, view, trading_date) does the
        de-dup -- no in-memory "seen today" cache needed, unlike
        ScannerBenchmarkTracker.record_if_new.
        """
        if not entries:
            return
        await asyncio.to_thread(self._record_appearances_sync, entries, datetime.now(timezone.utc))

    def _existing_keys_for_date_sync(self, trading_date: str) -> set[tuple[str, str]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT symbol, view FROM appearances WHERE trading_date = ?", (trading_date,)
            ).fetchall()
        return {(symbol, view) for symbol, view in rows}

    async def existing_keys_for_date(self, trading_date: str) -> set[tuple[str, str]]:
        """(symbol, view) pairs already recorded for `trading_date` (an ET
        date, ISO format) -- lets a caller cheaply figure out which
        candidates in a batch are genuinely new before doing more expensive
        work (e.g. a news fetch) for them, rather than doing that work for
        every ranked symbol on every poll tick regardless of whether
        record_appearances would just ignore it as a duplicate.
        """
        return await asyncio.to_thread(self._existing_keys_for_date_sync, trading_date)

    def _write_snapshots_sync(
        self, prices: dict[str, float], benchmark_price: float | None, now: datetime, lookback_days: int
    ) -> int:
        cutoff_date = (now.astimezone(ET) - timedelta(days=lookback_days)).date().isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, symbol, first_seen_at FROM appearances WHERE trading_date >= ?",
                (cutoff_date,),
            ).fetchall()

            to_insert = []
            for appearance_id, symbol, first_seen_at_str in rows:
                price = prices.get(symbol)
                if price is None:
                    continue
                first_seen_at = datetime.fromisoformat(first_seen_at_str)
                minutes_since = (now - first_seen_at).total_seconds() / 60
                to_insert.append((appearance_id, price, benchmark_price, minutes_since, now.isoformat()))

            if to_insert:
                conn.executemany(
                    """INSERT INTO snapshots
                       (appearance_id, price, benchmark_price, minutes_since_entry, snapshot_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    to_insert,
                )
            return len(to_insert)

    async def write_snapshots(
        self, prices: dict[str, float], benchmark_price: float | None, lookback_days: int = 2
    ) -> int:
        """One follow-up price check per symbol that has an open appearance
        within `lookback_days` trading days, using whatever's currently
        live in ScannerEngine.rows -- see engine.py's
        _write_periodic_snapshots for the caller/cadence.
        """
        return await asyncio.to_thread(
            self._write_snapshots_sync, prices, benchmark_price, datetime.now(timezone.utc), lookback_days
        )

    @staticmethod
    def _closest_snapshot(
        snaps: list[tuple[float, float, float | None]], target_minutes: float
    ) -> tuple[float, float, float | None]:
        return min(snaps, key=lambda s: abs(s[0] - target_minutes))

    def _compute_performance_sync(self, days: int, view: str | None) -> dict:
        cutoff_date = (datetime.now(ET) - timedelta(days=days)).date().isoformat()
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            query = "SELECT * FROM appearances WHERE trading_date >= ?"
            params: list = [cutoff_date]
            if view:
                query += " AND view = ?"
                params.append(view)
            appearances = conn.execute(query, params).fetchall()

            if not appearances:
                return {"summary": [], "leaderboard_best": [], "leaderboard_worst": []}

            appearance_ids = [a["id"] for a in appearances]
            placeholders = ",".join("?" * len(appearance_ids))
            snap_rows = conn.execute(
                f"SELECT appearance_id, minutes_since_entry, price, benchmark_price "
                f"FROM snapshots WHERE appearance_id IN ({placeholders}) "
                f"ORDER BY minutes_since_entry",
                appearance_ids,
            ).fetchall()

        snaps_by_appearance: dict[int, list[tuple[float, float, float | None]]] = {}
        for row in snap_rows:
            snaps_by_appearance.setdefault(row["appearance_id"], []).append(
                (row["minutes_since_entry"], row["price"], row["benchmark_price"])
            )

        picks_by_horizon: dict[str, list[dict]] = {name: [] for name in (*_HORIZON_MINUTES, "latest")}
        for appearance in appearances:
            snaps = snaps_by_appearance.get(appearance["id"])
            if not snaps:
                continue

            entry_price = appearance["entry_price"]
            benchmark_entry_price = appearance["benchmark_entry_price"]

            checkpoints = {
                name: self._closest_snapshot(snaps, minutes) for name, minutes in _HORIZON_MINUTES.items()
            }
            checkpoints["latest"] = max(snaps, key=lambda s: s[0])

            for horizon_name, (minutes_since, price, benchmark_price) in checkpoints.items():
                pct_change = (price - entry_price) / entry_price * 100 if entry_price else None
                benchmark_pct_change = (
                    (benchmark_price - benchmark_entry_price) / benchmark_entry_price * 100
                    if benchmark_price is not None and benchmark_entry_price
                    else None
                )
                alpha = (
                    pct_change - benchmark_pct_change
                    if pct_change is not None and benchmark_pct_change is not None
                    else None
                )
                picks_by_horizon[horizon_name].append(
                    {
                        "symbol": appearance["symbol"],
                        "view": appearance["view"],
                        "trading_date": appearance["trading_date"],
                        "first_seen_at": appearance["first_seen_at"],
                        "entry_price": round(entry_price, 2),
                        "entry_pct_change": round(appearance["entry_pct_change"], 2),
                        "entry_rvol": round(appearance["entry_rvol"], 2),
                        "entry_headline": appearance["entry_headline"],
                        "minutes_since_entry": round(minutes_since, 1),
                        "current_price": round(price, 2),
                        "pct_change_since_entry": round(pct_change, 2) if pct_change is not None else None,
                        "benchmark_pct_change_since_entry": (
                            round(benchmark_pct_change, 2) if benchmark_pct_change is not None else None
                        ),
                        "alpha_vs_benchmark": round(alpha, 2) if alpha is not None else None,
                    }
                )

        summary = []
        for horizon_name, picks in picks_by_horizon.items():
            for view_name in ("gainers", "losers", "most_active"):
                view_picks = [p for p in picks if p["view"] == view_name]
                if not view_picks:
                    continue
                with_return = [p for p in view_picks if p["pct_change_since_entry"] is not None]
                with_alpha = [p for p in view_picks if p["alpha_vs_benchmark"] is not None]
                wins = sum(1 for p in with_return if p["pct_change_since_entry"] > 0)
                summary.append(
                    {
                        "horizon": horizon_name,
                        "view": view_name,
                        "sample_size": len(view_picks),
                        "win_rate": round(wins / len(with_return) * 100, 1) if with_return else None,
                        "avg_return": (
                            round(sum(p["pct_change_since_entry"] for p in with_return) / len(with_return), 2)
                            if with_return
                            else None
                        ),
                        "avg_alpha": (
                            round(sum(p["alpha_vs_benchmark"] for p in with_alpha) / len(with_alpha), 2)
                            if with_alpha
                            else None
                        ),
                    }
                )

        latest_picks = [p for p in picks_by_horizon["latest"] if p["alpha_vs_benchmark"] is not None]
        leaderboard_best = sorted(latest_picks, key=lambda p: p["alpha_vs_benchmark"], reverse=True)[
            :_LEADERBOARD_SIZE
        ]
        leaderboard_worst = sorted(latest_picks, key=lambda p: p["alpha_vs_benchmark"])[:_LEADERBOARD_SIZE]

        latest_with_return = [p for p in picks_by_horizon["latest"] if p["pct_change_since_entry"] is not None]
        gap_buckets = bucket_analysis.bucket_breakdown(
            latest_with_return, lambda p: abs(p["entry_pct_change"]), bucket_analysis.GAP_BUCKETS
        )
        rvol_buckets = bucket_analysis.bucket_breakdown(
            latest_with_return, lambda p: p["entry_rvol"], bucket_analysis.RVOL_BUCKETS
        )

        return {
            "summary": summary,
            "leaderboard_best": leaderboard_best,
            "leaderboard_worst": leaderboard_worst,
            "gap_buckets": gap_buckets,
            "rvol_buckets": rvol_buckets,
        }

    async def compute_performance(self, days: int = 7, view: str | None = None) -> dict:
        return await asyncio.to_thread(self._compute_performance_sync, days, view)

    @staticmethod
    def _catalyst_drift(picks: list[dict]) -> dict:
        with_headline = [p for p in picks if p["has_headline"]]
        without_headline = [p for p in picks if not p["has_headline"]]
        with_stats = bucket_analysis.bucket_stats(with_headline)
        without_stats = bucket_analysis.bucket_stats(without_headline)
        delta_pp = (
            round(with_stats["win_rate"] - without_stats["win_rate"], 1)
            if with_stats["win_rate"] is not None and without_stats["win_rate"] is not None
            else None
        )
        return {
            "with_headline": with_stats,
            "without_headline": without_stats,
            "win_rate_delta_pp": delta_pp,
            "baseline_win_rate_delta_pp": _BASELINE_CATALYST_WIN_RATE_DELTA_PP,
            "sufficient_sample": (
                len(with_headline) >= bucket_analysis.MIN_SAMPLE_SIZE
                and len(without_headline) >= bucket_analysis.MIN_SAMPLE_SIZE
            ),
        }

    @staticmethod
    def _fade_risk_drift(picks: list[dict]) -> dict:
        above = [p for p in picks if p["entry_rvol"] > _FADE_RISK_RVOL_THRESHOLD]
        at_or_below = [p for p in picks if p["entry_rvol"] <= _FADE_RISK_RVOL_THRESHOLD]
        return {
            "rvol_above_threshold": bucket_analysis.bucket_stats(above),
            "rvol_at_or_below_threshold": bucket_analysis.bucket_stats(at_or_below),
            "threshold": _FADE_RISK_RVOL_THRESHOLD,
            "baseline_win_rate": _BASELINE_FADE_RISK_WIN_RATE,
            "baseline_avg_return": _BASELINE_FADE_RISK_AVG_RETURN,
            "sufficient_sample": len(above) >= bucket_analysis.MIN_SAMPLE_SIZE,
        }

    def _compute_ranking_drift_sync(self, since_date: str) -> dict:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            appearances = conn.execute(
                "SELECT * FROM appearances WHERE trading_date >= ?", (since_date,)
            ).fetchall()

            if not appearances:
                return {"since": since_date, "sample_size": 0, "catalyst": None, "fade_risk": None}

            appearance_ids = [a["id"] for a in appearances]
            placeholders = ",".join("?" * len(appearance_ids))
            snap_rows = conn.execute(
                f"SELECT appearance_id, minutes_since_entry, price "
                f"FROM snapshots WHERE appearance_id IN ({placeholders}) "
                f"ORDER BY minutes_since_entry",
                appearance_ids,
            ).fetchall()

        snaps_by_appearance: dict[int, list[tuple[float, float]]] = {}
        for row in snap_rows:
            snaps_by_appearance.setdefault(row["appearance_id"], []).append(
                (row["minutes_since_entry"], row["price"])
            )

        picks = []
        for appearance in appearances:
            snaps = snaps_by_appearance.get(appearance["id"])
            if not snaps:
                continue
            entry_price = appearance["entry_price"]
            if not entry_price:
                continue
            _, latest_price = max(snaps, key=lambda s: s[0])
            entry_headline = appearance["entry_headline"]
            picks.append(
                {
                    # A Benzinga movers-roundup mention doesn't count as a
                    # catalyst here either -- see
                    # app.market_data.news.is_roundup_headline and
                    # app.scanners.engine._has_headline, which this mirrors
                    # so the drift report measures the same thing ranking
                    # actually rewards.
                    "has_headline": bool(entry_headline) and not is_roundup_headline(entry_headline),
                    "entry_rvol": appearance["entry_rvol"],
                    "pct_change_since_entry": (latest_price - entry_price) / entry_price * 100,
                }
            )

        return {
            "since": since_date,
            "sample_size": len(picks),
            "catalyst": self._catalyst_drift(picks),
            "fade_risk": self._fade_risk_drift(picks),
        }

    async def compute_ranking_drift(self, since_date: str = _DEPLOY_DATE) -> dict:
        """Re-checks the catalyst-boost/fade-risk ranking multipliers in
        formulas.py (_CATALYST_BOOST, _FADE_RISK_RVOL/_FADE_RISK_DISCOUNT)
        against fresh appearances since `since_date` (default: the date
        that ranking change deployed), comparing current win rate/avg
        return by catalyst presence and by RVOL bucket against the one-off
        historical analysis those multipliers were originally based on.

        Uses each appearance's *latest* recorded snapshot (approximates
        end-of-day) -- same "win = positive return" convention as
        _compute_performance_sync. Reports the raw
        numbers rather than a single pass/fail verdict: whether a given gap
        from baseline counts as "drift" worth acting on is a judgment call,
        not something to hardcode a threshold for.
        """
        return await asyncio.to_thread(self._compute_ranking_drift_sync, since_date)
