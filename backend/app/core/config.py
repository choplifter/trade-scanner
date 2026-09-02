from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    alpaca_api_key_id: str = ""
    alpaca_api_secret_key: str = ""
    alpaca_paper: bool = True

    # Signs the session cookie (Starlette's SessionMiddleware) that every
    # login relies on. Empty by default so a fresh clone fails loudly at
    # startup (see main.py's lifespan) instead of silently signing sessions
    # with a guessable default -- generate one with e.g.
    # `python -c "import secrets; print(secrets.token_hex(32))"`.
    session_secret_key: str = ""

    # Master switch for the trading panel's *write* paths. Off by default and
    # deliberately separate from alpaca_paper: shipping order placement should
    # not change how anyone's dashboard behaves until they opt in, and the two
    # switches then fail independently. Read paths (account, positions, order
    # history) ignore this -- looking at an account harms nothing.
    trading_enabled: bool = False

    # Pre-flight ceilings, checked before an order reaches the broker. These
    # are fat-finger guards, not a risk policy: a mistyped quantity is the
    # failure they exist for, and refusing locally gives a clearer message
    # than a broker rejection would.
    #
    # The percentage is the one that matters, because a notional ceiling has
    # to scale with the account to mean anything. Sizing from a stop makes
    # notional = risk x (entry / stop-distance), so 1% of a 100k account with
    # a 5%-away stop is already a 20k position -- a fixed 5k ceiling (the
    # first value here) blocked every realistic trade while looking like a
    # safety feature. 25% of equity permits normal sizing and still refuses
    # an order that would be a quarter of the account in one name.
    trading_max_order_notional_pct: float = 25.0
    # Absolute backstop, applied as well as the percentage. Independent on
    # purpose: it still bounds the damage if equity is ever misreported.
    trading_max_order_notional: float = 25_000.0
    trading_max_order_qty: int = 10_000

    # Default share of equity risked per trade when sizing from a stop, used
    # to prefill the ticket. 1% is the common day-trading convention.
    trading_default_risk_pct: float = 1.0

    # --- Live account ------------------------------------------------------
    # A second key pair for the real-money account. The primary pair above
    # stays the paper account and is also what every market-data client
    # uses; only the TradingClient is chosen per request (see
    # AlpacaClients.trading_for and app.trading.guards). Keys alone arm
    # nothing: trading_allow_live must be on too, and every live write has
    # to carry the typed confirmation the UI collects.
    alpaca_live_api_key_id: str = ""
    alpaca_live_api_secret_key: str = ""
    trading_allow_live: bool = False
    # Live ceilings, deliberately far below the paper ones: a fat-finger on
    # paper costs nothing, on the real account it costs exactly the ceiling.
    trading_live_max_order_notional_pct: float = 10.0
    trading_live_max_order_notional: float = 5_000.0
    trading_live_max_order_qty: int = 500
    trading_live_max_option_contracts: int = 5

    # --- Options -----------------------------------------------------------
    # "opra" needs the paid options data subscription; "indicative" is the
    # free, delayed fallback. Applies to the chain picker and to GEX.
    alpaca_options_feed: Literal["opra", "indicative"] = "opra"
    # Spreads (multi-leg contracts) per order on the paper account.
    trading_max_option_contracts: int = 20
    # The underlying-price trigger loop (app.options.monitor): how often it
    # re-checks armed stops/targets, and how far past the mid, toward the
    # natural price, a fired close is priced so it actually fills.
    trading_options_trigger_check_interval: float = 2.0
    trading_options_trigger_slippage: float = 0.05

    # --- Live account ------------------------------------------------------
    # A second key pair for the real-money account. The primary pair above
    # stays the paper account and is also what every market-data client
    # uses; only the TradingClient is chosen per request (see
    # AlpacaClients.trading_for and app.trading.guards). Keys alone arm
    # nothing: trading_allow_live must be on too, and every live write has
    # to carry the typed confirmation the UI collects.
    alpaca_live_api_key_id: str = ""
    alpaca_live_api_secret_key: str = ""
    trading_allow_live: bool = False
    # Live ceilings, deliberately far below the paper ones: a fat-finger on
    # paper costs nothing, on the real account it costs exactly the ceiling.
    trading_live_max_order_notional_pct: float = 10.0
    trading_live_max_order_notional: float = 5_000.0
    trading_live_max_order_qty: int = 500
    trading_live_max_option_contracts: int = 5

    # --- Options -----------------------------------------------------------
    # "opra" needs the paid options data subscription; "indicative" is the
    # free, delayed fallback. Applies to the chain picker and to GEX.
    alpaca_options_feed: Literal["opra", "indicative"] = "opra"
    # Spreads (multi-leg contracts) per order on the paper account.
    trading_max_option_contracts: int = 20
    # The underlying-price trigger loop (app.options.monitor): how often it
    # re-checks armed stops/targets, and how far past the mid, toward the
    # natural price, a fired close is priced so it actually fills.
    trading_options_trigger_check_interval: float = 2.0
    trading_options_trigger_slippage: float = 0.05

    # --- Live account ------------------------------------------------------
    # A second key pair for the real-money account. The primary pair above
    # stays the paper account and is also what every market-data client
    # uses; only the TradingClient is chosen per request (see
    # AlpacaClients.trading_for and app.trading.guards). Keys alone arm
    # nothing: trading_allow_live must be on too, and every live write has
    # to carry the typed confirmation the UI collects.
    alpaca_live_api_key_id: str = ""
    alpaca_live_api_secret_key: str = ""
    trading_allow_live: bool = False
    # Live ceilings, deliberately far below the paper ones: a fat-finger on
    # paper costs nothing, on the real account it costs exactly the ceiling.
    trading_live_max_order_notional_pct: float = 10.0
    trading_live_max_order_notional: float = 5_000.0
    trading_live_max_order_qty: int = 500
    trading_live_max_option_contracts: int = 5

    # --- Options -----------------------------------------------------------
    # "opra" needs the paid options data subscription; "indicative" is the
    # free, delayed fallback. Applies to the chain picker and to GEX.
    alpaca_options_feed: Literal["opra", "indicative"] = "opra"
    # Spreads (multi-leg contracts) per order on the paper account.
    trading_max_option_contracts: int = 20
    # The underlying-price trigger loop (app.options.monitor): how often it
    # re-checks armed stops/targets, and how far past the mid, toward the
    # natural price, a fired close is priced so it actually fills.
    trading_options_trigger_check_interval: float = 2.0
    trading_options_trigger_slippage: float = 0.05

    # Simulation Mode: a fully local, broker-free order book (app.trading.sim)
    # that fills against real live prices without ever touching alpaca_clients
    # .trading -- so it needs neither trading_enabled nor alpaca_paper to be
    # on, and works even without Alpaca credentials configured at all (only
    # its price-dependent calls degrade without them). Starting cash sized to
    # the US PDT minimum rather than a round $100k, so sizing/risk % behavior
    # in practice mode resembles a realistic smaller account.
    trading_sim_starting_cash: float = 25_000.0
    # Flat multiplier applied to cash to report simulated buying_power --
    # 1.0 (default) means no margin, matching a real cash account. Set to
    # e.g. 4.0 to approximate Alpaca's day-trade margin buying power. Only
    # widens buying_power itself: the equity-based order ceilings
    # (trading_max_order_notional_pct/_notional) are untouched, so this
    # can't be used to size past the existing fat-finger guards.
    trading_sim_margin_multiplier: float = 1.0
    # How often the background fill loop (app.trading.sim.loop) re-checks
    # working sim orders against a fresh live price. Its own setting, not a
    # reuse of scanner_poll_interval_regular, because fill correctness here
    # has nothing to do with how the scanner is tuned.
    trading_sim_fill_check_interval: float = 5.0

    # History-replay (app.replay): how often the pacing loop checks whether
    # any playing user's clock is due to advance. Real advance cadence is
    # speed-adjusted per user against replay_bar_seconds below, so this only
    # bounds the loop's own polling granularity -- keep it well under the
    # fastest supported speed's per-bar interval.
    replay_pacing_check_interval: float = 1.0
    # Wall-clock seconds per 5-minute bar at speed=1x (300s -> real-time,
    # i.e. one bar every five real minutes). Defaults far faster than
    # real-time since the point of replay is compressing a session into a
    # practice-length sitting, not literally re-living it minute for minute.
    replay_bar_seconds: float = 3.0

    # Never hardcode a feed elsewhere in the app -- every Alpaca data call must
    # read this value. That held up: moving to the paid SIP subscription on
    # 2026-08-20 was a one-line .env change, no code touched.
    #
    # Stays "iex" as the *default* deliberately, even though this deployment
    # runs "sip": iex is the feed a fresh clone with free credentials can
    # actually use, and sip 403s without a paid market-data subscription. The
    # difference is not cosmetic -- iex reports only trades routed through one
    # exchange, so every volume level (volume_today, dollar_volume_today,
    # rvol, volume_surge) is a small, symbol-dependent fraction of the real
    # tape, while ratios like gap % and VWAP survive it nearly intact. See
    # FundamentalsCache.tape_coverage_pct for the measured spread.
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
    #
    # Was $1M, unchanged since before the 2026-08-20 IEX->SIP feed switch --
    # on IEX that was ~$30M+ effective (dollar_volume_today read only the
    # ~3% of the tape IEX itself saw), so moving to SIP without touching
    # this number silently dropped the real floor by ~30x.
    #
    # Re-derived 2026-08-29 with scripts/dollar_volume_backtest_report.py
    # --from-history (1810 previously-ranked symbols, 180 calendar days,
    # 1-trading-day-forward outcome), sweeping the floor and reading each
    # view's `edge` -- win_rate minus the SAME floor's base rate (a random
    # tradable-at-that-floor symbol-day), so the reading isolates the
    # ranking's own contribution from "a higher floor just selects calmer
    # names" (the base rate itself barely moved across the whole sweep,
    # 48.0% at $0 to 49.2% at $50M, so that confound wasn't actually
    # present here -- but it needed checking, not assuming). At $1M: losers
    # edge -1.6pp, gainers edge -5.1pp. Both improve as the floor rises;
    # losers crosses positive around $5-10M and peaks near $30M (+0.9pp);
    # gainers improves monotonically but stays negative everywhere tested,
    # -2.5pp even at $50M (a separate finding worth its own investigation --
    # today's biggest % gainer underperformed a random pick at every floor
    # in this 180-day window). most_active is flat throughout, as expected:
    # it's already ranked by the same dollar volume this floor filters on.
    # $20M chosen as a conservative pick inside the region both views
    # improved in, short of chasing the single $30M peak point: losers'
    # distinct-symbol count was already declining with the floor (1409 at
    # $1M, 1251 at $20M, 1171 at $30M, 1034 at $50M) and its edge dropped
    # back to +0.1pp by $50M, so treating the $30M peak (+0.9pp) as the
    # exact right answer risked reading noise in a shrinking sample.
    scanner_min_dollar_volume: float = 20_000_000.0

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
    # flag most of the morning as fade risk.
    #
    # That threshold has since been re-derived intraday --
    # scripts/rvol_backtest_report.py, which sweeps both definitions side by
    # side over 5-minute bars (ranking_drift_report.py, the pointer that used
    # to be here, cannot do it: it only sees whatever definition was live when
    # the appearance was recorded). Measured over 613 previously-ranked
    # symbols, 21 trading days, entry-to-session-close (2026-08-16, before the
    # 2026-08-20 IEX->SIP feed switch):
    #
    #   raw >=15x   -> n=85, win 41.2%, median -0.47%  (today's live setting)
    #   norm >=50x  -> n=86, win 43.0%, median -1.90%
    #   norm >=75x  -> n=64, win 32.8%, median -3.67%
    #
    # 50x picks out almost exactly the same population 15x does today (86 vs
    # 85 entries, 71 vs 71 distinct symbols) and sits where win rate and
    # median return start degrading, so ~50x looked like the candidate
    # replacement and 50-75x the defensible range.
    #
    # Re-run 2026-08-29, entirely under the SIP feed and against a larger
    # population (1810 symbols, 22 trading days): raw >=15x -> n=172, win
    # 40.1%, median -3.1%; norm >=15x -> n=378, win 39.9%, median -1.86%;
    # norm >=50x -> n=138, win 37.7%, median -7.15%. Both runs' raw-column
    # control disagrees with the 25.6%/-10.38% live baseline in the same
    # direction and by roughly the same amount (see formulas._FADE_RISK_RVOL
    # for the read on why), and per the script's own rule an unvalidated
    # control means no threshold should be taken from either run -- so the
    # ~50x normalized candidate above is not confirmed by this second run,
    # just not contradicted either (50x's win rate moved from 43.0% to 37.7%
    # between runs, a bigger swing than the raw column's). Still OFF: the
    # feed switch didn't change the picture, but the underlying control
    # problem is unresolved, so there still isn't a normalized threshold this
    # data actually supports shipping.
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

    # How often to re-check for active merger corporate actions (see
    # fetch_merger_actions in app.alpaca.universe) -- same cadence
    # rationale as split_ratio_refresh_interval above, a merger situation
    # doesn't change status minute to minute.
    merger_refresh_interval: float = 1800.0
    # How far back to look for a merger announcement still worth flagging
    # -- much wider than fetch_split_ratios' 7-day window, since a merger
    # can stay live for months between announcement and completion rather
    # than resolving the next session.
    merger_lookback_days: int = 90

    # How often a symbol's news headline is refreshed while it stays in a
    # ranked scanner view -- see app.market_data.news_cache.NewsCache. Kept
    # well above the poll interval so live scanning doesn't hammer the news
    # endpoint on every 5-10s tick for the same ~150 ranked symbols.
    scanner_news_refresh_interval: float = 900.0

    # How often the live cross-symbol News Feed widget's tracker re-polls
    # Alpaca News for symbols currently in a ranked view -- see
    # app.market_data.news_feed.NewsFeedTracker. Deliberately its own,
    # much faster cadence than scanner_news_refresh_interval above (that
    # cache only needs "the latest headline," this needs to actually
    # notice a new article arriving) -- a real, accepted increase in API
    # calls over the same ~150 symbols, chosen for a genuinely live feed.
    news_feed_refresh_interval: float = 60.0
    # How many recent items the feed keeps in memory (not persisted --
    # see NewsFeedTracker's own docstring).
    news_feed_ring_buffer_size: int = 200

    # How often a symbol's trailing-window momentum (momentum_pct)
    # is refreshed while it stays in a ranked scanner view -- see
    # app.scanners.momentum_cache.MomentumCache. Much tighter than the news
    # refresh above: the whole point of this field is to catch a symbol
    # accelerating *right now*, so refreshing only once per window (one
    # sample per window) would defeat the purpose. Still well above the
    # 5-10s poll interval so scanning doesn't fetch minute bars for ~150
    # symbols on every tick.
    scanner_momentum_refresh_interval: float = 120.0

    # Trailing window for the volume-acceleration fields (volume_1h,
    # volume_surge, rvol_1h -- see app.market_data.volume_surge). 60 minutes
    # is long enough that a single block print doesn't dominate the reading,
    # and short enough that it still describes "right now" rather than the
    # whole afternoon. Computed from the 5-minute bars MomentumCache already
    # fetches, so changing this costs nothing extra as long as it stays
    # within the one session those bars cover.
    scanner_volume_surge_window_minutes: int = 60

    # Absolute-value threshold for "suspicious" momentum over
    # momentum.MOMENTUM_WINDOW, used by the alarm (see
    # app.scanners.formulas.is_momentum_alert) -- a fixed % rather than
    # normalized against each symbol's own typical daily range.
    #
    # This was shipped at 5.0 as a placeholder, explicitly to be revisited
    # "once there's real trigger data to check the false-positive rate
    # against". That data now exists. Swept over 80 symbols and 30 days at a
    # 30-minute forward horizon (scripts.momentum_param_sweep), full-alert
    # results by threshold:
    #
    #     3.0%  n=176  43.2% win  +0.14% avg
    #     4.0%  n= 97  45.4% win  +0.35% avg
    #     5.0%  n= 59  52.5% win  +0.64% avg
    #     6.0%  n= 33  54.5% win  +1.07% avg   <- best clearing n>=30
    #     7.0%  n= 25                          -- under the sample floor
    #
    # Monotonic: a higher bar keeps fewer, better alerts. 6.0 is the highest
    # setting that still clears bucket_analysis.MIN_SAMPLE_SIZE, and n=33 is
    # only just over it -- good enough to prefer over 5.0, not enough to read
    # the +1.07% as precise.
    #
    # Widening MOMENTUM_WINDOW from 15 to 30 minutes is what forced the
    # revisit: measured on the same bars, an unchanged threshold fires about
    # three times as often over the longer window (5.0% went from 20 full
    # alerts to 58), because price has twice as long to travel the same
    # distance. Raising the bar keeps the alarm about as selective as it was.
    alarm_momentum_pct_threshold: float = 6.0

    # How often the red/yellow/green market-conditions readout (VIX,
    # today's high-impact global economic events, scanner breadth) is
    # refreshed -- see app.market_data.market_conditions. None of these
    # signals need poll-tick freshness, so this is deliberately slow.
    market_conditions_refresh_interval: float = 1800.0

    # Matches the frontend's own poll rate (useGexLevels.ts's REFRESH_MS) --
    # see app.market_data.gamma_exposure. Open interest itself only updates
    # once a day, but spot price and greeks move all session, so this stays
    # faster than market_conditions_refresh_interval rather than sharing it.
    gex_refresh_interval: float = 300.0

    # Persistent SQLite log of scanner appearances + periodic follow-up price
    # snapshots (see app.scanners.history_store), so "which scanner matches
    # performed best" survives restarts -- unlike ScannerBenchmarkTracker,
    # which is in-memory-only. Snapshot interval trades off DB growth against
    # how finely performance can be checkpointed after a symbol is flagged.
    scanner_history_db_path: str = "scanner_history.sqlite3"
    scanner_history_snapshot_interval: float = 900.0

    # Concurrent live chart subscriptions. 30 is the free plan's websocket
    # ceiling, kept as the default for the same reason alpaca_data_feed
    # defaults to "iex"; a paid subscription lifts it and .env raises this.
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
    def has_live_credentials(self) -> bool:
        return bool(self.alpaca_live_api_key_id and self.alpaca_live_api_secret_key)

    @property
    def has_live_credentials(self) -> bool:
        return bool(self.alpaca_live_api_key_id and self.alpaca_live_api_secret_key)

    @property
    def has_live_credentials(self) -> bool:
        return bool(self.alpaca_live_api_key_id and self.alpaca_live_api_secret_key)

    @property
    def has_anthropic_credentials(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def has_fmp_credentials(self) -> bool:
        return bool(self.fmp_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
