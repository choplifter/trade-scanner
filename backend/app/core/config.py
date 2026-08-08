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

    universe_min_price: float = 1.0
    universe_max_price: float = 50.0
    universe_min_avg_volume: int = 300_000
    max_universe_size: int = 2000

    # How often to re-check Alpaca's movers screener for symbols that are
    # moving big today but never qualified for the trailing-volume-filtered
    # universe above (see fetch_movers_backstop in app.alpaca.universe).
    movers_backstop_interval: float = 300.0

    max_stream_symbols: int = 30

    cors_origins: list[str] = ["http://localhost:5173"]

    # Optional -- powers the "AI Trade Ideas" widget, which turns the current
    # scanner rows into short descriptive commentary via the Claude API.
    # Everything else in the app works without this set.
    anthropic_api_key: str = ""

    @property
    def has_credentials(self) -> bool:
        return bool(self.alpaca_api_key_id and self.alpaca_api_secret_key)

    @property
    def has_anthropic_credentials(self) -> bool:
        return bool(self.anthropic_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
