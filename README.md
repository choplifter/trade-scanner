# Stocks in Play — Trading Dashboard

A personal, locally-run "stocks in play" scanner + chart dashboard in the
spirit of trade-ideas.com, bearbulltrader.com, and warriortrading.com: a live
**screener** (table or treemap heatmap view) where you build your own filters
over ~30 fields and results stream in over a WebSocket, with gainers /
premarket gainers / losers / most-active plus Aziz- and Cameron-style scans
shipped as editable presets ranked by a catalyst-boost/fade-risk-aware
scoring formula; a click-to-chart candlestick widget with a session-anchored
VWAP overlay, EMA/premarket/weekly/monthly range indicators, news pinned on
the timeline, and company info/news; a drop-in **strategy engine** (ORB
family, VWAP rules) whose signals are switchable from the UI, drawn on the
chart, badged in the scanner and measured by the same backtest that would
trade them; a **trading panel** that runs against a local Simulation
book, the Alpaca paper account or — behind its own keys, an env switch and
a typed confirmation — the real-money account (risk-sized tickets,
brackets, live position management, and DAS Trader/Andrew Aziz-style
instant-fire hotkeys for entries, breakout orders, flatten, scale-out and
stop-to-breakeven — no confirm dialog); an **Options** widget (option chain
with click-to-pick legs, long calls/puts, vertical spreads and iron condors
sent as multi-leg orders, open spreads with P&L, one-click close and
underlying-price exit triggers); a per-user trading journal, watchlist,
history replay, live news feed and GEX plan; a dashboard-wide momentum
alarm, AI-generated trade-idea
annotations, a scanner-wide benchmark against SPY, a persistent scanner
match history with fade-risk analysis, one-click backtesting of whatever
screen you're looking at, CLI tools for re-validating the ranking formula
against live and historical data, and a Plotly Dash analytics app — powered
by Alpaca Markets' real-time consolidated (SIP) data feed, with
float/market cap/short interest/company info and gap-filling news layered
in from Financial Modeling Prep and FINRA.

## 1. Get Alpaca API credentials (required for live data)

1. Sign up free at https://alpaca.markets and create a **paper trading**
   account (no funding required — you only need it for API access, not to
   place trades).
2. In the Alpaca dashboard, generate an **API Key ID** and **Secret Key**.
3. Copy `backend/.env.example` to `backend/.env` and fill in
   `ALPACA_API_KEY_ID` / `ALPACA_API_SECRET_KEY`.
4. Set `SESSION_SECRET_KEY` (the app refuses to start without it —
   `python -c "import secrets; print(secrets.token_hex(32))"`) and create a
   login from `backend/`: `python -m scripts.create_user <username> "<display
   name>"`. Every route except login needs a signed-in user; there is no
   signup page. Watchlist, journal, Simulation book, replay session and
   options triggers are all per user.

Without valid Alpaca credentials the app still starts, but the universe stays
empty and scanners will show no rows.

### Optional keys

Everything below is optional — the app runs fine without any of it, just
with fewer annotations/columns.

- **`ANTHROPIC_API_KEY`** — powers the "AI Trade Ideas" widget (Claude picks
  and annotates the 3 most notable scanner setups). Get a key at
  https://console.anthropic.com → API Keys.
- **`TRADING_ENABLED=true`** — arms the trading panel's *write* paths
  (ticket, cancel, close, stop moves, partial sells, spreads). Ships off so
  merging the feature changes nothing until you opt in. The read side
  (account, positions, orders, balance curve, option chain) works
  regardless, and Simulation Mode's local book never needs it.
- **`ALPACA_LIVE_API_KEY_ID` / `ALPACA_LIVE_API_SECRET_KEY` +
  `TRADING_ALLOW_LIVE=true`** — the real-money account. The first key pair
  stays the paper *and* market-data account (`ALPACA_PAPER=true` remains
  mandatory for it); the live pair is only ever used by the trading client
  behind the `/api/trading/live/...` prefix. Keys alone arm nothing: without
  `TRADING_ALLOW_LIVE` every live write is refused, and with it every live
  order/cancel/close still has to be confirmed by typing `LIVE` in the
  dialog (sent as an `X-Live-Confirm` header the server checks). Live gets
  its own, smaller ceilings — `TRADING_LIVE_MAX_ORDER_QTY` (500),
  `TRADING_LIVE_MAX_ORDER_NOTIONAL` (5,000), `_NOTIONAL_PCT` (10) and
  `TRADING_LIVE_MAX_OPTION_CONTRACTS` (5).
- **`ALPACA_OPTIONS_FEED`** — `opra` (the paid options feed, real greeks,
  bid/ask and open interest) or `indicative`. Feeds the Options widget's
  chain and the GEX plan alike. `TRADING_MAX_OPTION_CONTRACTS` (20) caps
  spreads or contracts per order on the paper account.
- **`FMP_API_KEY`** — free key at https://site.financialmodelingprep.com.
  Fills in the **Float**, **Mkt Cap**, **Country**, and **Company** scanner
  columns (Alpaca doesn't expose any of these), plus company
  name/sector/industry/description/website in the symbol detail panel. Also
  required for **Short %**, since that's computed by combining FMP's float
  with FINRA's free public short-interest data — no separate key needed for
  FINRA itself. This same key also powers the **market-conditions traffic
  light** (real VIX index + economic calendar — Alpaca has no index-data
  endpoint at all).

## 2. Run the backend

```
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8000
```

Check `http://localhost:8000/api/meta/health` — `has_alpaca_credentials`
should be `true`, and `universe_size` should be a few hundred/thousand once
the startup universe build finishes (takes a few seconds).

The Plotly Dash analytics app is served by the same backend process at
`http://localhost:8000/analytics/` — no separate process to run.

## 3. Run the frontend

```
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. The dev server proxies `/api` and `/ws` to the
backend on port 8000, so both must be running.

## What's included

- **Live screener** (one widget — the scanner *is* the screener). Build your
  own filters over the fields in [Filter parameters](#filter-parameters)
  below, sort by any of them, and the results update on every poll tick over
  a WebSocket rather than needing a refresh. The universe is polled from
  Alpaca snapshots every 5s (regular hours) / 10s (premarket). A
  movers-screener backstop periodically pulls in today's runners that weren't
  in the trailing-volume-filtered universe to begin with, and keeps running
  even while the market's closed so a big mover from the last session isn't
  invisible over a weekend/holiday. Rows need at least $20M of today's own
  dollar volume to appear at all (`SCANNER_MIN_DOLLAR_VOLUME`), applied
  *before* your filters, so a thin name that clears the universe filters but
  hasn't traded much today doesn't clutter the list. Re-derived from $1M
  after the 2026-08-20 IEX→SIP feed switch quietly dropped the *effective*
  floor ~30x (`dollar_volume_backtest_report.py`, see Ranking validation
  CLI tools below, has the sweep behind the new number).
- **Presets, not hardcoded views.** **Top Gainers**, **Top Losers** and
  **Most Active** are ordinary screens you can open, read and edit — load one
  and its filters and sort appear in the filter bar. Editing it shows
  "Custom" instead of claiming you're still on the preset. **Most Active**
  ranks by **dollar** volume, direction-agnostic: weighting by price tracks
  where the money actually went, rather than ranking a $6 name above a $45 one
  on share count alone. **Premarket Gainers** is the one exception and stays a fixed
  view — it's the gap frozen at the 09:30 open, not a question about the
  current rows, so no filter expresses it and the filter bar hides itself
  there.
- **Ranking formula**: gap % magnitude is boosted 1.15x on the **gainers**
  and **losers** views when a genuine news catalyst is behind the move, and
  discounted 0.1x on both when there's no catalyst at all -- so a headline
  costs or earns real rank, not just a one-directional bonus (raised from an
  initial 0.9x the same day: a mild haircut can't outrank a big enough gap
  on its own -- observed live, a +95% no-news mover still outscored a +19%
  catalyst-backed one at 0.9x, since 10% off a 5x-larger number is still the
  larger number; 0.1x can actually flip that kind of matchup). Any view's
  magnitude is separately discounted 0.7x when RVOL exceeds 15x -- both
  tuned from this app's own scanner-history win-rate data
  (`app/scanners/formulas.py`'s `rank_score`). **Most-active** stays
  untouched by the catalyst signal (headline edge measured at -3.1pp there,
  actually negative) while gainers keeps its measured +9.1pp backing (see
  `_CATALYST_BOOST`). **Losers is a deliberate, not-yet-validated
  extension** past what was actually measured there (+3.1pp, statistically
  indistinguishable from zero, per view) -- shipped 2026-08-29 on request
  despite that, and flagged as such right next to the constant
  (`_NO_CATALYST_DISCOUNT`) so it's re-checked with
  `scripts/ranking_drift_report.py` once enough losers data accumulates,
  the same discipline the gainers number has already been through twice.
  The fade-risk discount is direction-agnostic and so applies everywhere. A
  roundup headline that just lists a symbol alongside a dozen others ("12
  Health Care Stocks Moving...") doesn't count as a catalyst, only a story
  actually about that symbol does (`is_roundup_headline` in
  `app/market_data/news.py`). **FADE RISK** and **⚡ MOMENTUM** badges on the
  symbol cell surface the RVOL and momentum-alarm flags directly, not just
  indirectly via rank order.
- **News from two feeds.** Alpaca's Benzinga feed is thin on exactly the small
  and mid caps this scanner surfaces — measured over 12 scanner symbols it had
  a story for 3, while FMP had one for all 12 — so FMP fills the gaps. Alpaca
  stays authoritative wherever it has an answer and FMP never overrides it,
  because `_CATALYST_BOOST`'s +9.1pp was calibrated on Alpaca headlines and
  pooling a second feed would change what "has a catalyst" means without
  changing the multiplier derived from it. Each appearance records
  `entry_headline_source` so the two can eventually be measured separately.

  FMP is not simply better: ~30% of its raw items are securities-litigation
  notices, which are *backwards* as a catalyst — they're published after a
  collapse, so counting one would mark past losers as catalyst-backed. Every
  FMP headline passes a filter that rejects those, 13F churn and opinion
  pieces, by publisher (Seeking Alpha, Zacks, GuruFocus and friends never
  publish a company's own announcement) as well as by headline pattern. It
  rejects ~83% of raw items while still covering every symbol Alpaca missed.
  Analyst upgrades and price targets are deliberately kept — an upgrade is a
  real reason a stock moved today. FMP also mis-tags occasionally: a Western
  Union story was observed returned under `IMXI`, which nothing here can
  catch, so treat an FMP-sourced catalyst as lower-confidence.

  "Recent" is anchored to the **last session that actually began**, not a flat
  window: 48h measured on a Monday reaches only to Saturday and hides all of
  Friday — the very session the scanner shows while closed. Note FMP's
  timestamps are US/Eastern despite carrying no zone; reading them as UTC
  shifts every story four hours and silently discards after-close earnings
  releases.
- Scanner columns: symbol (click to copy its unambiguous `EXCHANGE:SYMBOL`
  TradingView format), a 📰 flag when there's a recent news headline
  (hover for the headline; refreshed every 15 min for whatever's currently
  ranked, not fetched per poll tick), company name, last price, gap %,
  **30m %** (trailing-30-minute price change, refreshed every 2 min --
  distinct from gap %, which is since prior close, so a symbol that already
  ran earlier and has since gone flat reads differently from one still
  actively moving right now), volume, RVOL, **RVol 1h**, and (when
  `FMP_API_KEY` is set) float, market cap, short interest % of float,
  exchange, and country. Rows also carry **strategy signal badges**
  (`▲ ORB`, `▼ VWAP-ORB`, …) for the session's last setup per enabled rule
  — hover for entry/stop/target and the reason — plus SHORT OK when the
  broker lists the name as shortable. A **Table / Heatmap** toggle switches
  the same live feed to a treemap view (tile size = dollar volume, color =
  gap %, click-to-chart same as the table).

### Filter parameters

Every field below can be filtered and sorted on. The list is served by the
backend (`GET /api/screener/fields`, defined in `app/scanners/screener.py`)
and both the filter dropdown and the column picker are generated from it —
adding a field there makes it appear in the UI with no frontend change.

| Field | Label | Type | Operators |
|---|---|---|---|
| `symbol` | Symbol | text | `eq`, `ne`, `contains`, `in` |
| `exchange` | Exchange | text | `eq`, `ne`, `contains`, `in` |
| `last_price` | Price | currency | `gt`, `gte`, `lt`, `lte`, `between` |
| `pct_change` | Change % | percent | `gt`, `gte`, `lt`, `lte`, `between` |
| `gap_pct` | Gap % (overnight) | percent | `gt`, `gte`, `lt`, `lte`, `between` |
| `volume_today` | Volume | number | `gt`, `gte`, `lt`, `lte`, `between` |
| `avg_vol_20d` | Avg Volume (20d) | number | `gt`, `gte`, `lt`, `lte`, `between` |
| `rvol` | Rel Volume | number | `gt`, `gte`, `lt`, `lte`, `between` |
| `dollar_volume_today` | Dollar Volume | currency | `gt`, `gte`, `lt`, `lte`, `between` |
| `avg_dollar_volume_20d` | Avg $ Volume (20d) | currency | `gt`, `gte`, `lt`, `lte`, `between` |
| `day_high` | Day High | currency | `gt`, `gte`, `lt`, `lte`, `between` |
| `day_low` | Day Low | currency | `gt`, `gte`, `lt`, `lte`, `between` |
| `spread_pct` | Spread % | percent | `gt`, `gte`, `lt`, `lte`, `between` |
| `volume_1h` | Volume (1h) | number | `gt`, `gte`, `lt`, `lte`, `between` |
| `volume_surge` | Volume Surge (vs prior 1h) | number | `gt`, `gte`, `lt`, `lte`, `between` |
| `rvol_1h` | Rel Volume (1h) | number | `gt`, `gte`, `lt`, `lte`, `between` |
| `rvol_window` | Rel Volume (window) | number | `gt`, `gte`, `lt`, `lte`, `between` |
| `volume_concentration` | Volume Concentration | number | `gt`, `gte`, `lt`, `lte`, `between` |
| `is_green_candle` | Green Candle | boolean | `is_true`, `is_false` |
| `is_hod` | At High of Day | boolean | `is_true`, `is_false` |
| `is_lod` | At Low of Day | boolean | `is_true`, `is_false` |
| `is_fade_risk` | Fade Risk | boolean | `is_true`, `is_false` |
| `shortable` | Shortable | boolean | `is_true`, `is_false` |
| `is_stale` | Stale Price | boolean | `is_true`, `is_false` |
| `tape_coverage_pct` | Tape Coverage % | percent | `gt`, `gte`, `lt`, `lte`, `between` |
| `float_shares` | Float | number | `gt`, `gte`, `lt`, `lte`, `between` |
| `short_interest_pct` | Short % of Float | percent | `gt`, `gte`, `lt`, `lte`, `between` |
| `rank_score` | Rank Score | number | `gt`, `gte`, `lt`, `lte`, `between` |
| `has_news` | Has Headline | boolean | `is_true`, `is_false` |

**`avg_dollar_volume_20d` vs `dollar_volume_today`.** The former is the
stock's *typical* liquidity (20-day average shares × price) and is the right
illiquidity filter in premarket, when every symbol's "today" is still near
zero. The latter is what has actually traded so far today. **`has_news`**:
`true` is always real; `false` only means no headline is *known* — the cache
follows ranked symbols, so requiring a catalyst works, proving the absence
of one doesn't.

**`gap_pct` is not `pct_change`.** The former is the *overnight* gap — today's
open against yesterday's close — and stops changing once the session opens.
The latter is the current price against yesterday's close, so it absorbs
everything since. On the last session CAPR gapped **+80.81%** and closed
**+57.58%**; HTZ gapped **+3.83%** and closed **−5.11%**. "Gapped hard this
morning" and "is up a lot right now" are different screens.

**Operators.** `gt`/`gte`/`lt`/`lte` are the usual comparisons; `between`
takes two bounds and is order-insensitive (40 and 10 means the same range as
10 and 40); `in` takes a comma-separated list (`NASDAQ, NYSE`); `contains` is
a case-insensitive substring match. Booleans take no value.

**Two behaviours worth knowing.** Filters are **ANDed** — there is no OR, since
a flat filter list can't express grouping unambiguously. And a **missing value
never matches** a numeric or text filter: "float under 20M" excludes symbols
whose float is unknown rather than sweeping them in, so a filter on a sparse
field legitimately narrows hard.

**The volume fields are three different questions.** `rvol` is cumulative
volume since the open over a 20-day average — it only ever climbs, so a stock
that ran at 09:45 and died still reads high at 15:30. `volume_surge` is the
trailing hour over the hour before it; honest as a description but a trap as a
filter, because session volume is U-shaped and near the close it exceeds 1 for
most of the market at once. `rvol_1h` is the trailing hour over what that
*clock hour* normally trades (from the intraday volume profile), so "2x" means
twice the usual 15:00–16:00 volume. **Screen on `rvol_1h`, not
`volume_surge`.** Measured on real 2026-08-14 bars, the naive ratio called all
of AAPL (3.15), TSLA (2.11), NVDA (1.64), SMCI (1.67) and AMD (3.81)
"accelerating", while `rvol_1h` correctly read 0.28–0.95 — every one traded
*below* its normal final hour. Window length is
`SCANNER_VOLUME_SURGE_WINDOW_MINUTES` (default 60). All three are `null`
outside the regular session.

**Coverage caveat.** `float_shares` is universe-wide (one bulk FMP file), and
everything else in the table above is computed for every symbol on every poll.
Market cap, short interest, country, company name, recent headline and 30m %
are **not** filterable: they're only fetched for symbols already in a ranked
view or a live screen result (~150 of ~2000), so filtering on them would
silently return nothing for most of the universe. They still *display* on
whatever your screen returns — screened rows are enriched alongside ranked
ones. `volume_1h` / `volume_surge` / `rvol_1h` share that enrichment path, so
they populate for your screen's results rather than the whole universe.

**Built-in presets** (`GET /api/screener/presets`):

| Preset | Filters | Sort |
|---|---|---|
| Top Gainers | `pct_change > 0` | `rank_score` desc |
| Top Losers | `pct_change < 0` | `rank_score` asc |
| Most Active | none | `dollar_volume_today` desc |
| Volume Accelerating | `rvol_window > 2`, `pct_change > 0`, `is_green_candle` | `rvol_window` desc |
| Moderate Movers (3–8%) | `pct_change between 3 and 8`, `dollar_volume_today > 2M` | `dollar_volume_today` desc |
| Aziz: Stocks in Play | `gap_pct > 2`, `rvol > 2`, `avg_dollar_volume_20d > 10M`, `last_price > 10`, `has_news` | `rank_score` desc |
| Cameron: Momentum | `last_price between 2 and 20`, `pct_change > 10`, `float_shares < 20M`, `rvol > 5`, `has_news` | `pct_change` desc |
| Low Float Runners | `float_shares < 20M`, `pct_change > 5`, `rvol > 3` | `pct_change` desc |

The Aziz and Cameron presets are the two educators' published scans
translated to this app's fields — each preset's description (hover it, or
`GET /api/screener/presets`) documents the translation and its caveats,
including that `has_news` can only *require* a catalyst (the headline cache
follows ranked symbols, so its absence proves nothing) and that Cameron's
sub-$5 sweet spot needs `UNIVERSE_MIN_PRICE` lowered in `backend/.env`.

Screens are not persisted yet — presets are server-side, but a screen you
build yourself is lost on reload.

### Backtesting a screen

The scanner's **Backtest** button replays whatever filters are currently
active over history — one click from the screen you're looking at, with the
results inline. Clicking a pick loads that symbol's chart at the entry bar,
marked with an arrow.

Two resolutions, because they can reconstruct different things:

| | replays | lookback | holds until |
|---|---|---|---|
| **Daily** | one bar per session | up to 365d | 1–5 days forward |
| **Intraday (5m)** | every 5 minutes | capped at 45d | that session's close |

**Daily** can reconstruct most fields — anything derivable from an
OHLCV bar and its trailing window. **Intraday** adds `volume_1h`,
`volume_surge` and `rvol_1h`, which are rates measured *inside* a session and
so have nothing to reconstruct from at daily resolution. It rebuilds each
symbol's state as of every bar (volume so far, running high/low, the trailing
hour) and screens cross-sectionally per timestamp, so `sort_by` and `limit`
mean what they mean live.

**It refuses rather than silently degrading.** `apply_filters` deliberately
ignores unknown fields so a stale saved screen still runs live; inheriting
that here would drop half your criteria and hand back a plausible number for a
strategy you never described. Instead the run returns 422 naming the offending
fields — and if switching resolution would fix it, says so and offers a retry
rather than telling you to delete the filter your screen was built around.
`spread_pct` (needs historical NBBO quotes nothing fetches) and `is_stale` (a
live-feed freshness concept, meaningless for a past date) can't be replayed at
any resolution. `exchange` isn't replayable either, but only because the
backtest's row type doesn't carry it — a fixable omission rather than a real
limit, since a listing venue barely changes.

**`float_shares` and `short_interest_pct` are replayable but look-ahead.**
Neither has a historical series here — float moves with offerings and lockups,
and FINRA publishes short interest twice a month with a 2–4 week lag — so a
backtest applies *today's* values to past dates. That asks "how did stocks
that are low-float **today** behave last March", which isn't a question you
could have acted on. They're supported because refusing outright would make
the tool useless for exactly those setups, but any run using one returns
`look_ahead_fields` and the panel says so beside the result. Treat it as
exploratory, never as validation.

**Read alpha, not win rate.** A raw win rate near 50% is what a coin flip
looks like, and on a green day every long closes positive; only the
benchmark-relative figure says whether the screen contributed. Intraday runs
also report a **replication factor**: every qualifying bar is a pick, so one
surge contributes a dozen near-identical rows — a real run showed 9,332 picks
from 654 distinct symbol-days (14.3× per event), meaning the effective sample
is nearer the second number.

Survivorship bias applies throughout: today's universe is replayed against
past dates.
- **Momentum alarm** (React app only, off by default, long setups only):
  a dashboard-wide alert for a fast, still-confirming *upward* move -- 30m
  % at least a threshold (6% default, `ALARM_MOMENTUM_PCT_THRESHOLD`)
  *and* the latest 5-minute candle confirms it three ways: closed at/near
  its high (shaved top, near-zero upper wick, `app/market_data/candle_shape.py`),
  closed green (close > open), and price trading above the session VWAP
  (`app/market_data/vwap.py`) -- the standard day-trading reference for
  "buyers are still in control." Long side only on purpose: a green-candle-
  and-above-VWAP requirement doesn't have a sign-flipped short-side
  equivalent, so downward moves aren't alerted at all. Only regular-session
  (09:30-16:00 ET) candles can trigger, and the trailing 30-minute window
  never crosses a day boundary -- without both guards a "30-minute move"
  is really a session-boundary artifact: a thin after-hours print measured
  against the last regular-session close, or an overnight gap measured
  against the previous day. Same-day premarket *is* allowed as the
  reference price, so the opening range still works
  (`app/market_data/momentum.py`). Unlike a chart indicator (which only
  ever watches whatever symbol's chart happens to be open), this watches
  every ranked view continuously. Toggle in the header
  ("Alarms Off/On", state persisted across reloads); once on, a new
  trigger auto-opens a center overlay listing every currently active alarm
  (click one to load its chart), collapsing to a small count badge you can
  reopen manually. The threshold and shaved-top wick tolerance are
  starting heuristics, not yet backtested the way the catalyst/fade-risk
  multipliers were.
- One chart widget: click any symbol anywhere in the app to load it —
  candlestick chart (1m/5m/15m aggregated client-side, plus native
  1h/4h/D/W/M), volume pane, TradingView-style premarket/after-hours
  tinting on intraday views, and a VWAP line switchable between the
  session anchor (resets 09:30 ET) and the premarket anchor — on a gapper
  the two disagree about which side of the line price is on. Fed by
  Alpaca's live minute-bar stream, plus a company info + recent news panel
  (name/sector/industry/description from FMP, headlines from both feeds).
  Recent headlines are also **pinned on the timeline** as 📰 markers at the
  bar nearest their publish time; clicking a pinned bar scrolls the news
  panel to that story and highlights it. Every symbol is clickable, not
  just the main scanner table — the AI past-picks table, the scanner
  benchmark table, and the scanner match history leaderboards all load
  into the same chart. A **Levels** toggle overlays EMA 9/20 (sourced from
  1-minute bars regardless of the displayed timeframe), premarket/daily/
  weekly/monthly range lines, hourly structure levels, the opening-range
  box (5 or 15 min, following the Signals panel's switch), and the active
  strategies' stop/target lines with an entry arrow on the bar the last
  setup fired — a small pluggable indicator system
  (`backend/app/indicators/`): drop a new file in that directory exposing a
  `compute(ctx)` function and it shows up on the chart on the next
  request, no backend restart needed. When a paper position is open on the
  symbol on screen, its entry/stop/target render as solid price lines
  (blue/red/green, distinct from the dashed Levels lines) regardless of the
  Levels toggle — a position reflects real capital at risk, not a togglable
  overlay. Sourced from a `TradingContext` shared with the trading panel
  (`frontend/src/context/TradingContext.tsx`) rather than a second poll
  loop, so the chart doesn't remount or double-poll when the panel's
  positions/orders refresh. The order ticket's *own* current entry/stop/
  target — whatever's typed or auto-suggested right now, before anything is
  submitted — draw the same way but dashed and labeled "(draft)", so a
  hypothetical ticket line can never be mistaken for a real position's. Both
  can show at once for the same symbol (e.g. planning a scale-in next to
  the position already protecting it); the Levels checkboxes gate both.
  The forming candle updates **tick by tick** from Alpaca's trade stream
  (odd-lot/late/out-of-sequence prints excluded by condition code) rather
  than once per completed minute bar, with TradingView-like proportions
  (tight bar spacing, the price pane taking ~80% of the height). An
  **Auto** toggle in the chart header is TradingView's auto-scroll: on,
  every new candle snaps the view back to the right edge; off, the chart
  stays exactly where you dragged it — including into the empty space
  right of the newest bar — until you switch it back on. The choice is
  remembered.
- **Strategy scripts** (`backend/app/strategies/`): the same drop-in idea as
  the indicators above, but for trade setups rather than chart lines. A file
  exposes `NAME`, an optional `ENABLED` flag and an `evaluate(ctx)` that
  returns a `Signal` (entry, stop, target, side, stop trigger, scale-out) or
  `None`, and it is picked up by the live scanner, the chart *and* the
  backtest — one definition, so a rule cannot be measured one way and traded
  another. Stops and targets are *places* (under the opening range, under
  VWAP), and the R multiple the backtest reports is the same distance
  `trading/sizing.py::shares_for_risk` sizes a live position from.

  Shipped rules: **Opening Range Breakout** (far-end stop), **ORB Break**
  (wick stop), **ORB Retest**, **Premarket Range Breakout**, **VWAP Retest**
  (break–hold–retest), **VWAP Open Range Break** (the session VWAP *line*
  crossing out of the opening range — the box breaks it only when the whole
  session has, volume-weighted) and its **Line Stop** sibling. Each file's
  docstring carries its measured expectancy tables and, where a variant lost,
  the record of why — the measurement history is part of the rule.

  What sits *around* every rule, enforced once at the loader rather than in
  each file: a **VWAP-side gate** (a signal may only be long above the
  session VWAP, short below it), a **runtime on/off switch per strategy**
  (the scanner's **Signals** panel — persisted, applied to scanner markers,
  chart lines and full backtest runs alike; `--strategy <name>` at the CLI
  bypasses it so a parked rule can still be measured), a switchable
  **opening-range length** (5 min, Aziz's definition, or 15 min for names
  whose first minutes are too thin to make a box), and a switchable
  **measured-move fallback** (no mapped level above a breakout → aim at
  entry + 2R instead of declining, which is what a new-high day otherwise
  does to every break rule). Signals show as compact `▲ ORB`-style badges on
  scanner rows and as stop/target lines plus an entry arrow on the chart.

  Two deliberate differences from the indicator loader. Strategies are loaded
  once per run rather than re-executed per evaluation — a 120-symbol, 30-day
  backtest is ~200k evaluations, and a report has to describe one version of
  a rule anyway (backtests also pin the switch state at start, so a toggle
  clicked mid-run cannot change the rule mid-walk). And a file that fails to
  load is *reported*, not just logged: a missing chart line is visible,
  whereas a strategy that never loaded looks exactly like one that found no
  setups.

  Backtest a single rule with
  `python -m scripts.strategy_backtest_report --strategy <name> [--cost-bps 2]`,
  which reports expectancy in R alongside the percentage stats every other
  backtest here uses, plus the exit mix and the ambiguous-bar share.
- **Trading panel** (off by default): order entry and live position
  management against one of three accounts, switched by the header's
  **Simulation / Paper / Live** toggle and shown as a badge on every
  trading widget. **Simulation** is a fully local, per-user order book
  (`backend/app/trading/sim/`): the same pure ticket validation and sizing
  as the real path, exact-price full-quantity fills (no slippage or
  partials modelled), resting limit/stop/bracket legs filled by a
  background loop against live prices, and a **Reset** to start over — it
  needs no `TRADING_ENABLED` and works without Alpaca credentials except
  for pricing. **Paper** is the Alpaca paper account. **Live** is the
  real-money account: only selectable when live keys and
  `TRADING_ALLOW_LIVE` are set, entered through a modal that shows the
  account's equity and demands the word `LIVE`, never persisted across
  reloads, framed in red, with instant-fire buttons/hotkeys and chart
  stop/target dragging disabled and every action re-confirmed by typing
  `LIVE`. Write paths are gated in the service layer — `TRADING_ENABLED`
  for paper, plus the live switch and confirmation for live — with
  fat-finger ceilings (`TRADING_MAX_ORDER_QTY` / `_NOTIONAL` /
  `_NOTIONAL_PCT`, smaller `TRADING_LIVE_*` twins) checked before anything
  reaches the broker. Closed round trips are stored per account, so a
  paper reset never touches the live record. The ticket
  sizes from risk (risk % of equity or a fixed amount against your stop
  distance — the same arithmetic the strategy backtests report R in) or from
  a fixed quantity, previews server-side before submitting, and attaches
  take-profit/stop legs as a bracket or OTO. Entries can be market, limit,
  **stop** or **stop-limit** — the stop types are breakout entries that rest
  until price trades through the trigger, which is what a limit *above* the
  market is often mistaken for (a buy limit means "this price or lower", so
  it fills at once; the preview warns when a limit is marketable, and
  refuses a stop trigger on the wrong side of the market or a stop-loss
  that would sit above where the entry will actually fill). The ticket shows
  buying power, equity and any existing position on the symbol before you
  size anything, offers 25/50/75/100%-of-buying-power quick-size buttons in
  fixed-quantity mode, and — while a risk-mode preview is in flight — a
  synchronous local "≈ N sh" estimate plus a "Pricing…" indicator so the
  field isn't blank waiting on the round trip. The server's own order
  ceilings are shown once priced, and Risk % carries a client-side sanity
  guardrail (the backend enforces no upper bound — a value far outside your
  account's default blocks submit with a hint, catching a fat-fingered 50
  typed for 0.5). If the symbol already has a working stop/target, a
  dismissible banner says so before you place a second order on it.
  Stop/Target/Limit/Trigger auto-suggest once a symbol (and, for Limit/
  Trigger, an order type) is picked, so nothing sits blank waiting to be
  typed — Stop to 6% below/above the reference price (the minimum distance
  that clears this app's own risk-sizing-vs-notional-ceiling math at the
  default risk %, not a technical level), Target to a 2:1 reward:risk off
  whatever's actually in the Stop field, Limit to a **resting** 1% pullback
  (not a marketable price — that's what **Stop** is for; the preview
  already warns if a limit would fill immediately), and a stop-limit's cap
  to a nickel beyond its *own* trigger rather than the market, or the entry
  could never fill once triggered. Retyping a field by hand stops it from
  updating further. Ticket-building hotkeys: **B**/**S** for side,
  **1**-**4** for order type (market, limit, stop, stop-limit) and
  **Enter** to open the confirm dialog — suppressed while typing in a field
  or while that dialog is open, shown in each button's hover tooltip.

  **Instant-fire hotkeys** (DAS Trader/Andrew Aziz style — one keypress,
  no confirm dialog, mirrored as buttons in the panel) sit alongside that
  confirm-gated flow rather than replacing it; safe to skip the preview
  round trip entirely because `submitOrder` re-derives price/size and
  re-checks every ceiling above server-side regardless of whether
  `previewOrder` was ever called, so instant-fire hits the identical guards
  the confirm-gated path does. Entries: **Q**/**W**/**E** buy at 0.5%/1%/2%
  equity risk off the ticket's own Stop field (**Shift**+ for sell), sized
  and stop-loss-attached the same server-side arithmetic the confirm-gated
  risk mode uses — if Target also has a value it rides along as a bracket,
  same as it would from the confirm-gated flow. **T** fires a breakout
  entry: buy-stop at the day's high + $0.01 (fetched fresh, one round trip,
  the only instant-fire action that needs one — acceptable since it's a
  resting order, not an immediate fill), risk-sized the same way, stop-loss
  8% below the trigger (a percentage rather than DAS's flat $0.30: this
  app's universe spans $2-$100, where a fixed dollar offset is either
  negligible or a ceiling-blowing pittance depending which end of that
  range the symbol's on). Position/order management, same no-dialog style:
  **F** flattens the position on the selected symbol, **C** cancels every
  working order (no bulk-cancel endpoint exists on purpose, so this loops
  what's already on screen), **0** moves the stop to breakeven, **Shift+0**
  to breakeven plus a $0.05 buffer (direction-aware: above entry long,
  below it short), **X** scales the position out 50% at market via the same
  partial-close path **Sell…** below uses, exits re-armed for the
  remainder. All suppressed while typing in a field.
  The positions table joins each position to its working exits (including a bracket's stop parked in
  Alpaca's `held` status, which the naive "open orders" query hides — a
  position without a stop gets a loud **NO STOP** badge) and manages them in
  place: click the stop price to move it, **BE** sets it to your entry
  (refused with the reason if price is on the wrong side), **Sell…** does a
  partial close that re-arms the remainder's stop and take-profit as one OCO
  pair at their old prices — and says so loudly if the stop could not come
  back. Orders (working/filled/**trades**), an account equity curve and the
  account summary round out the tabs. The Filled and Trades views share a
  **Day / Week / Month / All** period (calendar periods in ET, not rolling
  windows), and the Trades summary recomputes for the period; on Week and
  Month a per-day breakdown (trades, W/L, P&L, running total) sits above
  the list, and clicking a day narrows the list to it. The Trades view
  pairs fills back into round trips — Alpaca has no closed-positions
  endpoint — and shows each
  closed position's entry, exit, P&L, % and **R** (P&L over the initial
  risk to the stop the entry was placed with, the unit the strategy
  backtests report expectancy in), with totals, win rate, profit factor and
  expectancy underneath. Round trips are persisted into
  `scanner_history.sqlite3` (`trades` table), so the record survives a
  paper-account reset and the broker's 500-order history cap.

- **Options widget** (Paper and Live; Simulation shows a "not available"
  banner because there is no simulated options book). Pick a symbol
  anywhere and the widget loads its expiries (next 60 days, with DTE) and
  the **option chain** for the selected one — calls left, puts right, OI /
  IV / delta / bid / mid / ask per side, in-the-money shading, a spot
  divider row the table scrolls to, greeks shown as "—" where Alpaca has
  none (0DTE). Seven strategies: **Long call / Long put** (one bought
  contract, options level 2), **Bull call / Bear put** (debit verticals),
  **Bull put / Bear call** (credit verticals) and **Iron condor** (level 3).
  An **Auto-pick** chooses the legs — the short leg at ~0.30 delta
  out-of-the-money (0.20 for a condor's wings, ~3% OTM without greeks),
  the long leg *Width* strikes further out, a debit spread's long at the
  money, an outright long at the money — and clicking a strike in the
  chain moves the matching leg while keeping the spread's shape (long
  below short for the bullish pair, above for the bearish). The ticket
  previews server-side with a 300 ms debounce: net mid and natural
  (bid/ask-crossing) price, the limit (prefilled with the mid, editable),
  max profit / max loss / breakevens / collateral per the standard
  defined-risk arithmetic (`backend/app/options/pricing.py`, e.g. credit
  vertical max loss = (width − credit) × 100), the account's options
  buying power and the per-order ceilings, plus warnings for same-day
  expiry (Alpaca force-closes 0DTE positions around 15:15 ET), missing
  greeks and wide markets. Spreads go out as Alpaca **multi-leg (MLEG)**
  day limit orders with the +debit/−credit signed limit, a long call/put
  as a plain option limit order; the confirm dialog lists every leg and, in
  Live, asks for `LIVE`. Contract symbols always come from the live chain
  (never composed by hand), so adjusted roots like `SPY1` and non-tradable
  contracts are handled by the data. **Open spreads** groups Alpaca's
  per-contract positions back into the spreads they were opened as
  (by underlying + expiry, classified by leg shape; a lone long contract
  is a long call/put, an unbalanced or lone short remainder is flagged
  **broken**) with net entry, mark and P&L, expandable legs, a **Close**
  modal that reverses every leg at the mid (limit editable; a single
  remaining leg closes with a plain order) and a **trigger** editor: arm
  *close below* and/or *close above* on the stock's price, and/or a bound
  on the position's own **premium** (its mark: the mid of closing the
  package per share — a long's stop or a credit spread's take-profit at
  *≤*, the reverse at *≥*), and a backend loop
  (`backend/app/options/monitor.py`, every 2 s during the regular session,
  one batched stock-price fetch and one batched option-snapshot fetch)
  closes the spread with a limit stepped toward the natural price so it
  fills rather than rests. Triggers are persisted in `scanner_history.sqlite3` per user and
  account, survive restarts, show their status (active / fired / failed /
  orphaned when the legs are gone) and can be cancelled. The ticket's
  strikes and an open spread's strikes/trigger bounds draw on the chart as
  levels. Clicking a leg in the ticket's summary, a leg under Open
  spreads, or a single option order in the Orders tab, loads the
  contract's own **premium chart** — its minute
  bars (1m/5m/15m) or native hourly/daily/weekly bars from Alpaca's option
  bars endpoint, labelled "SPY 4 Sep 765C · premium", with a button back to
  the underlying; no VWAP or levels on it (those belong to the stock's
  price axis) and no live stream (the premium chart refreshes on reload).
  A **contract ticket** sits under the header: contracts, **Buy** (to open,
  the long call/put path with its preview, limits and confirmation) and
  **Sell** (to close, only enabled for what is held -- nothing is ever
  written naked), each opening a dialog with the mid, natural price and an
  editable limit; the held quantity, entry and P&L, and any working order
  on the contract are shown beside it, and working orders draw as dashed
  lines at their limits with the held entry as a solid line. Below the
  ticket, for a held contract, a **premium trigger** editor arms *close if
  the premium is ≤ / ≥* (same trigger store and loop; the bounds draw as
  dashed stop/target-coloured lines on the premium chart). Both trigger
  editors only exist for something actually held: a resting, unfilled
  order shows neither -- there is nothing to close yet. While a
  contract is on the chart the Options widget follows it: its expiry is
  selected and the strike marked as a long call/put. Widget-local hotkeys (not in Live): `[` `]` expiry, `5`–`9`
  strategy (the two outright longs have none — `0`–`4` belong to the
  equity ticket), `+` `−` width. Market data for the chain always comes
  from the paper/market-data key, whichever account the order goes to.
- **AI Trade Ideas** (needs `ANTHROPIC_API_KEY`): Claude ranks up to 3 of
  today's movers, each **required** to have a genuine, stock-specific news
  catalyst — enforced server-side before the model ever sees a candidate
  (`is_roundup_headline`-filtered, same "genuine catalyst" definition used
  everywhere else in the app), not just asked for in the prompt, since an
  LLM instruction to strictly enforce "must have X" over a ranking task
  isn't reliably deterministic the way a Python filter is. On a quiet news
  day this can return fewer than 3 ideas, or none, rather than padding the
  list with a numbers-only setup. Among candidates that clear that bar,
  ranks first by how significant the news itself is (an earnings
  beat/FDA decision/M&A announcement outranks a routine press release),
  then by gap %, RVOL, dollar volume, HOD status, VWAP position,
  30-minute momentum, spread, multi-day context, float, and short interest
  — framed as descriptive scanner annotation, not investment advice. A
  past-picks performance table tracks how prior AI picks have actually
  moved since they were generated.
- **Scanner benchmark**: every symbol the scanner itself has flagged during
  this process's uptime (gainers/losers/most active — not just the 3 AI
  picks above) gets logged the moment it first appears, then tracked live against
  SPY from that instant. In-memory only, resets on restart — see **Scanner
  match history** below for the persistent version of this same check.
- **Scanner match history**: a SQLite-backed log of every scanner match,
  keyed per symbol per trading day, so it survives backend restarts and
  accumulates over weeks instead of resetting. Tracks performance at
  30-minute, 60-minute, and latest-known checkpoints vs. SPY, with
  win-rate/avg-return/avg-alpha per scanner view, best/worst leaderboards,
  and a fade-risk breakdown (does a bigger entry gap % or higher RVOL
  predict *worse* subsequent performance — i.e. "gap and crap" — rather
  than better?) bucketed by gap size and RVOL. Each entry also carries the
  most recent news headline (if any, Alpaca's news feed, 48h lookback) as
  of the moment it was first flagged, shown in a News column on the
  leaderboards — context for *why* it moved, fetched once per symbol per
  day rather than on every poll tick. Available as a widget in the React
  dashboard and a page in the Dash analytics app.
- **Ranking validation CLI tools** (`backend/scripts/`, run via
  `python -m scripts.<name>` from `backend/`): `ranking_drift_report.py`
  re-checks the catalyst-boost/fade-risk multipliers against fresh
  `scanner_history.sqlite3` data since they were deployed, reporting win
  rate/avg return per bucket next to the original baseline rather than a
  hardcoded pass/fail (flags buckets under a 30-sample floor as noisy
  instead of overstating a thin result). `backtest_report.py` goes further
  back by replaying months of historical **daily** bars through the same
  live ranking functions (`engine._rank_gainers`/`_rank_losers`/
  `_rank_most_active`, not a reimplementation) to reconstruct what would
  have ranked in each of the three views on past trading days -- a much
  bigger sample than however many days of live history have accumulated so
  far. Both share
  `app/scanners/bucket_analysis.py`'s bucketing so a live check and a
  historical one measure things identically. Also breaks down win rate by
  `is_shaved_top` (did the entry day's own candle close at/near its high?
  -- wick-shape analysis works on any OHLC bar, so this doesn't need
  minute data). The backtest is daily-bar-only: no catalyst backtesting
  (needs historical news, unbuilt), no float (FMP's bulk file is *today's*
  float, so applying it to past dates is look-ahead bias), no minute-
  resolution signals (time-of-day RVOL, the momentum alarm itself -- 30m %
  is inherently a minute-resolution concept), and it applies today's
  universe membership across the whole lookback window (survivorship bias)
  -- all stated up front in its own report output.
  `rvol_backtest_report.py` is the intraday counterpart, and the only tool
  that can re-derive `formulas._FADE_RISK_RVOL` for the *time-normalized*
  RVOL definition behind `SCANNER_RVOL_TIME_NORMALIZED`: it walks 5-minute
  bars and sweeps candidate thresholds under both definitions **side by
  side**, the raw column acting as a control whose answer is already known
  from live data. If the control doesn't reproduce, the candidate column
  isn't evidence of anything -- that check is the point of the tool, not a
  footnote. Prefer `--from-history` (every symbol that has actually been
  ranked) over the default top-N-by-dollar-volume selection: the most
  liquid names essentially never reach high RVOL, so the default produces
  zero of the events being measured. Reads win rate and *median* return
  rather than the mean, since entry-to-close returns on thin names ran
  from -66% to +459% in the first real run -- a range in which a mean
  describes its outliers rather than its population.
  `dollar_volume_backtest_report.py` re-derives `SCANNER_MIN_DOLLAR_VOLUME`
  itself the same disciplined way: it sweeps candidate floors through
  `simulate_from_bars` (so each floor reflects what would actually have
  ranked that day, not a post-hoc bucketing of one run's picks) and reports
  each view's win-rate **edge** over a same-floor base rate -- a random
  *tradable-at-that-floor* symbol-day -- to separate "the ranking got
  better" from "the floor just selected calmer names." That control mattered
  in practice: raising the floor barely moved the base rate (48.0% at $0 to
  49.2% at $50M across one real sweep) while `losers`' edge went from -1.6pp
  at the old $1M floor to positive past $5-10M, which is what motivated
  re-deriving the number in the first place (see above).
- **Analytics app** (`/analytics`, Plotly Dash): a resizable 4-panel scanner
  heatmap + table + symbol detail + AI trade ideas view, plus separate pages
  for the scanner benchmark, scanner match history, cross-symbol
  correlation/comparison, and seasonality.
- **Three layout modes** (React app), switched by the header's
  **Panels / Grid / Dock** toggle and remembered across reloads: **Panels**
  is the default nested-splitter layout (drag the splitters to resize fixed
  slots), **Dock** is a VS Code-style docking layout (`dockview-react`) where
  every widget is a tab you can drag into any group, split or
  maximise, and **Grid** makes every widget freely repositionable -- drag a widget
  by its header to move it, drag its bottom-right corner to resize, and
  neighbours compact out of the way (`react-grid-layout`, wrapped by
  `frontend/src/components/layout/DashboardGrid.tsx`). A **Reset** button
  restores the default arrangement, which deliberately mirrors the Panels
  layout so switching modes changes nothing until you actually drag. Grid
  cells are keyed by stable widget id rather than position, so moving a
  widget never remounts it -- the chart keeps its zoom and the analytics
  widgets don't restart their poll timers. Row height is derived from the
  measured viewport (12 rows) rather than fixed, so the default layout fills
  the window exactly; drag widgets past the bottom and the grid scrolls.
- Session badge (Premarket / Market Open / After Hours / Closed) in the
  header, computed from the NYSE trading calendar.
- **Market-conditions traffic light** (needs `FMP_API_KEY`): a red/yellow/
  green badge next to the session indicator (React header and Dash nav)
  combining the real CBOE VIX index level, today's high-impact global
  economic calendar events (CPI, GDP, rate decisions -- US/EU/UK/China/
  Japan), and scanner breadth (% of today's universe currently green) by
  "worst signal wins" -- red if any one factor is bad, yellow if any is
  borderline, green only if all three are calm. Hover for the specific
  reasons. Purely descriptive market weather, not a trade signal -- same
  non-advisory framing as AI Trade Ideas.

## Known limitations

- **Feed**: runs on Alpaca's consolidated **SIP** tape, which needs a paid
  Alpaca market-data subscription. Without one, set `ALPACA_DATA_FEED=iex` in
  `backend/.env` — the free single-exchange feed. Everything still works on
  it, but every volume *level* (today's volume, dollar volume, RVOL, volume
  surge) is then a small and symbol-dependent fraction of reality — measured
  2026-08-19, IEX saw 3.2% of AAPL's volume and 3.9% of F's — while ratios
  like gap % and VWAP survive nearly intact. The **Tape Coverage %** column
  reports this per row and should read ~100% on SIP. Note the volume-based
  universe filters (`UNIVERSE_MIN_AVG_VOLUME`) and `SCANNER_MIN_DOLLAR_VOLUME`
  mean very different things across the two feeds, so they need re-tuning if
  you switch. A feed can also return a stale/erroneous single-trade print for
  thin names; `resolve_last_price` (`app/scanners/formulas.py`) discards a
  print that falls outside 2x the day's own recorded high/low range.
- **Recent stock splits**: Alpaca's live snapshot endpoint has no
  split-adjustment option, so a symbol that split recently would show a
  nonsensical gap % (prev_close on the old share basis vs. a post-split
  price) without correction. `fetch_split_ratios` (`app/alpaca/universe.py`)
  tracks the last 7 days of splits, refreshed every 30 min, and
  `ScannerEngine._compute_rows` rescales `prev_close` whenever it's older
  than the split's ex_date -- correct however long the market's been
  closed, not just "today." The closed-market fallback
  (`app/scanners/latest_session.py`) gets the same correction for free via
  Alpaca's own `adjustment=SPLIT` param on the historical bars it reads.
- **Universe price floor**: `UNIVERSE_MIN_PRICE` defaults to **$5** (the
  SEC's own "penny stock" threshold) rather than $1 — sub-$5 names carry the
  thinnest liquidity, the widest spreads, and are the most prone to bad
  prints. Lower it in `backend/.env` if you want penny stocks back.
- **Dedicated RVOL scanner / new highs-lows**: not built -- RVOL is shown
  as a column on every scanner, not a ranking of its own.
- **RVOL is not time-of-day normalized by default**: `formulas.rvol` compares
  today's volume so far against a *full-day* 20-day average, so it understates
  RVOL all morning -- measured against SPY's own intraday curve, only ~4.6% of
  a typical day's volume is in by 09:35 and ~14% by 10:00, so a symbol trading
  at a completely normal pace reads ~0.05x rather than ~1x that early. A
  corrected denominator exists (`app/market_data/volume_profile.py`, enabled
  with `SCANNER_RVOL_TIME_NORMALIZED=true`) but ships **off**, because turning
  it on rescales RVOL by up to ~20x in the first minutes and the 15x fade-risk
  threshold plus the 25.6%/-10.38% baseline behind it were both calibrated
  against the un-normalized definition. Enabling it without re-deriving that
  threshold from fresh `scanner_history.sqlite3` data
  (`scripts/ranking_drift_report.py`) would flag most of the morning as fade
  risk. Also note there's no RVOL *floor* at all -- unlike scanners that
  require e.g. RVOL >= 5x as a hard gate, a big gap on unremarkable volume
  still ranks here. The 15x threshold itself was re-checked against
  post-SIP data with `rvol_backtest_report.py --from-history` and left
  unchanged: the raw-RVOL degradation shape is the same before and after
  the 2026-08-20 feed switch, so RVOL keeps its scale as expected -- see
  `formulas._FADE_RISK_RVOL`'s own comment for the run's numbers.
- **Float** comes from FMP's **bulk** shares-float file
  (`app/fundamentals/float_bulk.py`), so unlike market cap and company profile
  it's available for the *whole* universe rather than only for symbols already
  in a ranked view — ~8 requests covers ~23k symbols, which measured 98.5%
  coverage of the symbols this scanner has actually ranked to date. That's what
  makes float usable as a future *ranking* input rather than display-only, since
  ranking on float needs float before ranking. Requires an FMP plan that
  includes the endpoint (verified on **Starter**; the sibling `profile-bulk` is
  402-restricted there, which is why market cap/profile stay per-symbol).
  Each appearance's float is now recorded as `entry_float_shares` in
  `scanner_history.sqlite3`, so a low-float rule can be validated against real
  outcomes before being shipped — the same discipline the catalyst boost and
  fade-risk discount went through. **No float-based ranking factor exists yet**,
  deliberately: there's no accumulated history to validate one against. Two
  traps for whoever adds one — FMP reports `0` for "unknown" (treated as absent
  here, not as a real zero float), and float around a reverse split is
  unreliable, e.g. ELPW reads ~0.0M float days after a 45:1 reverse split, so a
  naive "lowest float ranks highest" rule would put it top of the board.
- **Market cap / short interest**: only fetched for symbols currently in
  a ranked scanner view (not the whole universe), so they're absent until a
  symbol actually appears there. Short interest is FINRA's own biweekly
  bulk file, which in practice runs noticeably behind FINRA's advertised
  publish schedule -- expect it to reflect a settlement date roughly 2-4
  weeks old, not this week's.
- **Options**: no simulated options book — the Options widget is Paper
  and Live only. Paper MLEG fills at the mid can rest unfilled for a
  while; the trigger loop compensates with a limit stepped toward the
  natural price, manual closes don't. Underlying-price triggers only fire
  during the regular session and only while the backend is running — they
  are not broker-side orders (Alpaca takes no stop orders on options).
  Greeks are missing for same-day expiries (Alpaca can't compute them),
  so the auto-pick falls back to a ~3% OTM strike and the chain shows "—".
  Alpaca force-closes 0DTE positions around 15:15 ET. And FINRA's
  pattern-day-trader rule (three day trades per five sessions under
  $25k equity on a margin account) applies to Alpaca accounts regardless
  of the holder's country — option round trips count.
- **Catalyst boost still pools two news feeds.** `entry_headline_source` is
  recorded per appearance, but nothing reads it yet — so FMP-sourced headlines
  currently feed a multiplier calibrated only on Alpaca ones. Splitting the
  drift report by feed is the next step before either is trusted.
- **Screener**: React app only — the Dash analytics app has no screening page
  (it briefly did; having two surfaces answer the same question differently,
  one live and one request-driven, was worse than having one). Screens you
  build aren't saved: presets are server-side and survive, a custom filter set
  is lost on reload. Filters are AND-only. And the fields that are filterable
  are limited to those computed for the whole universe every poll — see the
  coverage caveat under [Filter parameters](#filter-parameters).
- **`rvol_1h` calibration**: the field is correct in *relative* terms — it
  demonstrably separates a genuine late-session pickup from the U-shaped ramp
  every stock gets (see Filter parameters). Whether its absolute scale is
  right hasn't been confirmed on a live session yet: five large caps sampled
  on one August Friday all read below 1x, which is plausible for a light day
  but isn't proof. If it sits below 1 for nearly everything during an active
  session, the expected-volume denominator needs a look.
- **Momentum alarm**: React app only, not in the Dash analytics app (the
  center-overlay/toggle interaction pattern doesn't translate to Dash's
  page-reload-driven callbacks the same way; the Dash Backtest page does
  cover the underlying alert condition's historical win rate, see below).
  Both scanner tables do show the underlying ⚡ MOMENTUM badge regardless.
  The 6% threshold is calibrated, the 5% shaved-top wick tolerance is
  not. Widening the window from 15 to 30 minutes made an unchanged
  threshold fire about three times as often (5.0% went from 20 full alerts
  to 58), which forced a sweep over 80 symbols and 30 days: 5.0% gave n=59
  at 52.5% win / +0.64% avg, 6.0% gave n=33 at 54.5% / +1.07%, and 7.0%
  fell under the n>=30 floor. 6.0 is the highest setting still clearing
  that floor -- enough to prefer over 5.0, not enough to read the +1.07%
  as precise (see `alarm_momentum_pct_threshold` in `app/core/config.py`
  for the full table). The wick tolerance remains an unvalidated starting
  heuristic. A 180-day
  daily-bar backtest of `is_shaved_top`
  *on its own* (no 30m % gate, since that needs minute data -- see below)
  found no meaningful standalone edge at a 1-day horizon for either
  gainers or losers, despite large samples -- doesn't confirm or refute
  the *combined* 30m %-and-shape signal the live alarm actually checks,
  just that shape alone isn't doing much work by itself.
- **Backtest harness**: daily-bar resolution only -- can validate the gap%/
  RVOL-based parts of the ranking formula against months of history, plus
  `is_shaved_top` on its own, but not the catalyst boost (needs historical
  news, unbuilt) or the momentum alarm as a whole (30m % needs historical
  minute bars, unbuilt). A 180-day run found ~0 RVOL>15x events at daily
  resolution even across ~240 symbols -- that specific threshold is
  fundamentally an intraday phenomenon daily bars smooth away, so this
  tool can't validate it either; only live/intraday history can. The same
  limit applies to `SCANNER_RVOL_TIME_NORMALIZED`: a daily bar is a whole
  session, so the time-of-day session fraction is 1.0 and the normalized
  RVOL definition is *identical* to the raw one here -- no amount of
  daily-bar lookback can re-derive `_FADE_RISK_RVOL` for it. That one is
  answered by `rvol_backtest_report.py` instead (see above).
  And `most_active` is replayed but doesn't read like the other two views:
  `--max-symbols` selects the universe's top N *by dollar volume*, so
  it ranks by dollar volume over a set already sorted by dollar volume and
  its picks repeat far more than the other views'. Measured at the CLI
  defaults (180 days, 300 symbols): `most_active` drew on 62.6% of the
  symbol pool with its top 10 names holding 19.9% of picks and 16 names
  present on >=90% of trading days, against ~100% of the pool, a 7.6-7.9%
  top-10 share and no such name for gainers/losers. Concentrated rather
  than degenerate -- treat its sample as a narrower, repeat-heavy cohort,
  and expect it to tighten further at smaller `--max-symbols`.
- **Scanner benchmark / match history**: entries are still recorded from
  closed-market fallback data, but the actual price/SPY comparison columns
  only populate once live polling resumes (they need a live SPY price and a
  live price for the flagged symbol) -- expect "—" for those specifically
  outside market hours, same as float/market cap. The live **scanner
  benchmark** log itself isn't persisted across backend restarts, same as
  the AI trade-ideas performance table -- use **scanner match history**
  (SQLite-backed) for the persistent version. Match history's fade-risk
  buckets are still statistically thin in the first few days of a fresh
  install; treat early numbers as directional, not conclusive. News
  headlines are also best-effort -- expect "—" for a fair share of entries,
  since not every mover has a story behind it within the 48h lookback.
- Outside premarket/regular market hours, scanners fall back to the most
  recently completed session's real data instead of polling live (labeled
  "LAST SESSION" in the UI) -- they only show empty rows if `backend/.env`
  doesn't have valid Alpaca credentials.

## Project layout

```
backend/           FastAPI app: Alpaca integration, scanner engine, VWAP, WebSockets,
                    fundamentals (FMP + FINRA), AI trade ideas, Plotly Dash analytics app
backend/app/trading/   Equity ticket, sizing, guards (paper/live), trade + journal stores,
                        sim/ = the local Simulation Mode book and fill loop
backend/app/options/   OCC parser, chain fetch/cache, spread ticket + pricing, MLEG builder,
                        position grouping, underlying-price trigger store + monitor
backend/scripts/   Standalone CLI tools (ranking drift report, backtest) -- run via
                    `python -m scripts.<name>` from backend/, not part of the running app
frontend/          Vite + React + TypeScript dashboard, lightweight-charts for candles
```

See `backend/app/` and `frontend/src/` for the module breakdown — each file
has a short docstring/comment explaining its role.
