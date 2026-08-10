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

    # $5 is the SEC's own definition of a "penny stock" -- below it, thin
    # liquidity and wide spreads make for erratic/manipulable prints (see
    # the bad-tick guard in app.scanners.formulas.resolve_last_price) and
    # trading that's closer to gambling than a real edge-driven setup.
    universe_min_price: float = 5.0
    universe_max_price: float = 50.0
    universe_min_avg_volume: int = 300_000
    max_universe_size: int = 2000

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
