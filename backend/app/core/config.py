from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    alpaca_api_key_id: str = ""
    alpaca_api_secret_key: str = ""
    alpaca_paper: bool = True

    # Never hardcode a feed elsewhere in the app -- every Alpaca data call must
    # read this value, so upgrading to a paid SIP subscription later is a
    # one-line config change instead of a code change.
    alpaca_data_feed: Literal["iex", "sip"] = "iex"

    scanner_poll_interval_regular: float = 5.0
    scanner_poll_interval_premarket: float = 10.0

    # A row is marked ScannerRow.is_stale once this long has passed since
    # the feed last actually confirmed its price via a trade or daily bar
    # -- see app.scanners.formulas.is_stale. The engine recomputes every
    # row on every poll tick regardless of whether the feed reported
    # anything new, so without this a thin/illiquid name the feed has
    # stopped seeing prints for keeps the same price/pct_change
    # indefinitely and looks exactly as current as a symbol still printing
    # every tick. Deliberately just a warning flag, not an exclusion from
    # ranking -- premarket liquidity is thin enough market-wide that most
    # of the universe can legitimately go several minutes between prints,
    # so dropping stale rows from views entirely was observed emptying
    # them down to a handful even though most of that universe was still
    # genuinely live, just quiet. 10 minutes catches a feed-coverage gap
    # well before it can look current for tens of minutes, without being
    # so tight it flags normal quiet stretches as if something's wrong.
    scanner_stale_row_seconds: float = 600.0

    # $5 is the SEC's own definition of a "penny stock" -- below it, thin
    # liquidity and wide spreads make for erratic/manipulable prints (see
    # the bad-tick guard in app.scanners.formulas.resolve_last_price) and
    # trading that's closer to gambling than a real edge-driven setup.
    universe_min_price: float = 5.0
    universe_max_price: float = 50.0
    universe_min_avg_volume: int = 300_000
    max_universe_size: int = 2000

    # Minimum *today's* dollar volume (price x shares actually traded so
    # far today, not a trailing average) a row needs to appear in a ranked
    # scanner view -- distinct from universe_min_avg_volume, which gates
    # which symbols get polled at all based on trailing 20-day history. A
    # symbol can clear that trailing bar and still be quiet so far today
    # (e.g. early premarket); this keeps the "$ Vol" column in both
    # frontends' scanner tables from showing rows that technically qualify
    # but haven't actually traded much yet.
    scanner_min_dollar_volume: float = 1_000_000.0

    # Divide RVOL's denominator by the share of a typical day's volume normally
    # done by this time of day, instead of comparing today's partial volume
    # against a full-day average (see app.market_data.volume_profile and
    # formulas.rvol). Strictly the more correct measure -- raw RVOL understates
    # by ~20x at 09:35 -- but OFF by default because it rescales RVOL rather
    # than just correcting it, and formulas._FADE_RISK_RVOL (15x) plus the
    # 25.6%/-10.38% baseline behind it were both calibrated against the
    # un-normalized definition. Measured against SPY's real curve, the
    # denominator shrinks ~21x at 09:35, ~7x at 10:00, ~2.3x at noon and ~1x by
    # the close, so turning this on without re-deriving that threshold would
    # flag most of the morning as fade risk. Re-derive it from fresh
    # scanner_history data (scripts/ranking_drift_report.py) after enabling.
    scanner_rvol_time_normalized: bool = False

    # How often to re-check Alpaca's movers screener for symbols that are
    # moving big today but never qualified for the trailing-volume-filtered
    # universe above (see fetch_movers_backstop in app.alpaca.universe).
    movers_backstop_interval: float = 300.0

    # How often to re-check for same-day stock splits (see fetch_split_ratios
    # in app.alpaca.universe) -- corrects prev_close for the one day a split
    # takes effect, since Alpaca's live snapshot endpoint has no
    # split-adjustment option. Splits are a per-day fact set once the market
    # opens, so this doesn't need to be frequent -- 30 min balances catching
    # one that posts intraday against not hammering the endpoint.
    split_ratio_refresh_interval: float = 1800.0

    # How often a symbol's news headline is refreshed while it stays in a
    # ranked scanner view -- see app.market_data.news_cache.NewsCache. Kept
    # well above the poll interval so live scanning doesn't hammer the news
    # endpoint on every 5-10s tick for the same ~150 ranked symbols.
    scanner_news_refresh_interval: float = 900.0

    # How often a symbol's trailing-15-minute momentum (pct_change_last_15m)
    # is refreshed while it stays in a ranked scanner view -- see
    # app.scanners.momentum_cache.MomentumCache. Much tighter than the news
    # refresh above: the whole point of this field is to catch a symbol
    # accelerating *right now*, so refreshing only every 15 minutes (one
    # sample per window) would defeat the purpose. Still well above the
    # 5-10s poll interval so scanning doesn't fetch minute bars for ~150
    # symbols on every tick.
    scanner_momentum_refresh_interval: float = 120.0

    # Absolute-value threshold for "suspicious" 15-minute momentum used by
    # the momentum alarm (see app.scanners.formulas.is_momentum_alert) -- a
    # fixed % rather than normalized against each symbol's own typical daily
    # range. Simpler to ship first and revisit once there's real trigger
    # data to check the false-positive rate against, same spirit as
    # formulas.rvol's own time-of-day-normalization TODO and how the
    # catalyst/fade-risk multipliers were tuned from scanner_history.sqlite3
    # rather than guessed at upfront.
    alarm_momentum_pct_threshold: float = 5.0

    # How often the red/yellow/green market-conditions readout (VIX,
    # today's high-impact global economic events, scanner breadth) is
    # refreshed -- see app.market_data.market_conditions. None of these
    # signals need poll-tick freshness, so this is deliberately slow.
    market_conditions_refresh_interval: float = 1800.0

    # Persistent SQLite log of scanner appearances + periodic follow-up price
    # snapshots (see app.scanners.history_store), so "which scanner matches
    # performed best" survives restarts -- unlike ScannerBenchmarkTracker,
    # which is in-memory-only. Snapshot interval trades off DB growth against
    # how finely performance can be checkpointed after a symbol is flagged.
    scanner_history_db_path: str = "scanner_history.sqlite3"
    scanner_history_snapshot_interval: float = 900.0

    max_stream_symbols: int = 30

    cors_origins: list[str] = ["http://localhost:5173"]

    # Optional -- powers the "AI Trade Ideas" widget, which turns the current
    # scanner rows into short descriptive commentary via the Claude API.
    # Everything else in the app works without this set.
    anthropic_api_key: str = ""

    # Optional -- float + market cap that Alpaca doesn't provide at all,
    # via Financial Modeling Prep's free tier. Short interest is derived
    # from this same float plus FINRA's free public bulk data (no key
    # needed for that part -- see app.fundamentals.finra_short_interest),
    # so this one key drives all three fundamentals fields. Only fetched
    # for symbols that actually appear in a ranked scanner view (a couple
    # dozen at a time, cached per fundamentals_refresh_interval) so FMP's
    # free-tier daily request cap isn't at risk even though the full scan
    # universe can be thousands of symbols.
    fmp_api_key: str = ""
    fundamentals_refresh_interval: float = 21_600.0

    @property
    def has_credentials(self) -> bool:
        return bool(self.alpaca_api_key_id and self.alpaca_api_secret_key)

    @property
    def has_anthropic_credentials(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def has_fmp_credentials(self) -> bool:
        return bool(self.fmp_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
