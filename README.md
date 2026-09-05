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
  and annotates the 3 most notable scanner setups) and the Options widget's
  **Idea** tab (Claude proposes option structures on the selected symbol's
  chain). Get a key at https://console.anthropic.com → API Keys.
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
  endpoint at all) and the **next-earnings date** the Options widget's Idea
  tab reasons over (whether a structure would be held through the report).
  Without it that field is simply absent, which is never read as "no
  earnings coming".

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

- **Scanner pause** (admins, the `⏸ / ⏵` button in the scanner header;
  `POST /api/scanners/pause`, persisted): stops market-wide polling, the
  backstop movers and the history snapshots while you trade one symbol,
  so the API budget and the database go to the charts. Market
  conditions, GEX and the news feed keep running; the views hold their
  last rows and every login sees the paused state.
- **Journal stats** (Trading Journal → Stats): for one underlying (SPY by
  default), where the P&L came from -- entry time of day in ET in
  half-hour windows around the open, calls vs puts, days to expiry at
  entry, weekday -- with trade count, win rate, total and average P&L,
  computed from the journal's own closed trades.
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
  bar nearest their publish time; clicking a pinned bar opens the story
  (headline link, source, age, summary) in a popover on the chart itself
  and scrolls the news panel to it. Every symbol is clickable, not
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

- **News Feed widget**: the newest articles across the whole market, live
  from Alpaca's news websocket (wildcard-subscribed; a once-a-minute
  market-wide poll backs it up and seeds the list after a restart -- the
  header shows `● live` or `○ poll`). One row per article with the symbols
  it names as chips (click to select, drag onto a chart); symbols
  currently ranked in a fixed scanner view are highlighted, and the
  **All / Ranked** toggle narrows the feed to those. Untagged market-wide
  stories show a dash. Not persisted: a restart starts the feed over.

- **Options widget** (full guide: [Options](#options) below. Paper, Live
  and Simulation -- in Simulation mode the dashboard's own options book
  fills the orders, live or at the replayed moment of a history replay).
  Pick a symbol
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
  price axis). It is live like the stock chart: the backend subscribes the
  contract on Alpaca's option websocket (OPRA) and streams its trades,
  which shape the forming candle tick by tick, and its quotes, shown as
  bid / ask in the header; closed candles come from a 5 s re-fetch of the
  newest option bars (Alpaca streams no option bars), higher timeframes
  re-fetch every 30 s.
  The premium chart draws its own levels (Levels menu, on by default):
  live **bid / ask** lines, the **session** high, low and previous close of
  the premium, **entry ±** lines at +50% / +100% / −50% of a held
  contract's entry, the **intrinsic value** from the underlying's live
  price (only when above zero -- the gap to the candle is the time value),
  an **expected move** pair (where the premium lands on a one-sigma move
  of the underlying over the shorter of the rest of today and the time to
  expiry, from the snapshot's IV, delta and gamma; the labels carry the
  underlying prices), **theta** levels (the premium in an hour and at the
  close if the underlying stands still, from the snapshot's daily theta), **EMA 9/20** of the premium on the displayed bars, and a session
  **VWAP** of the premium computed from the contract's bars.
  (On a cheap contract the axis can still show values below zero: the
  bottom fifth of the pane is reserved for volume, and the library scales
  that margin linearly.)
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
- **Settings** (the header's ⚙ button; closed until clicked, remembered
  in the browser under `app:settings`): **Appearance** -- a chart colour
  scheme from five presets (Classic, TradingView, Monochrome with hollow
  rising candles, Colour-blind blue/orange, Muted) or **Custom** -- eight
  colour pickers for up/down candle bodies, wicks and borders, volume and
  the tables' positive/negative text, with "start from" any preset (for a
  TradingView user's own scheme) -- applied at once to
  candles, wicks, volume, position and order lines, the GEX/spread/premium
  levels, the balance curve, the risk chart's areas and the tables' up/down
  colours; filled or hollow rising candles; Light / Dark / System (System
  follows the OS live); premarket/after-hours shading on or off.
  **Chart** -- the defaults a chart starts with: timeframe, candles or
  line, auto-scroll, VWAP anchor (the buttons in a chart still change only
  that chart). **Display** -- number format: the browser's, point
  (1,234.56) or comma (1.234,56), for money and quantities and the chart
  axes; price inputs keep the point. Time zone: every clock in the app --
  chart axis and crosshair, fills, alarms, last prints, the replay clock,
  the journal's entry windows, the risk chart's time slider -- in the
  browser's zone or New York's (`frontend/src/utils/time.ts`; the zone
  name is shown where a table of times needs it). Display only: session
  boundaries, the trading windows and the journal's buckets are market
  concepts and stay computed in New York time, their labels are converted.
  **Hotkeys** -- every shortcut in one
  list. "Reset to defaults" puts everything back. The risk chart's height
  is dragged at its bottom edge, the Options ticket's width (and with it
  the risk chart's) at the splitter between the chain and the ticket
  (double-click resets it); both are remembered here too.
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
- **GEX Plan** (needs Alpaca credentials): net dealer gamma exposure for
  the **currently selected symbol** -- regime (dealers net long or short
  gamma), the approximate gamma-flip strike, and the call/put walls, with a
  plain-language playbook of what that regime conventionally tends to mean.
  The same walls draw as levels on the main chart and feed a Net GEX badge
  in its header.
  Readings used to exist only for a hardcoded five (SPY, QQQ, TSLA, NVDA,
  PLTR) that a background loop precomputed every 300 s; every other symbol
  simply had no GEX. **Any optionable symbol works now.** A bigger fixed
  list was never the answer -- one reading paginates the contract listing
  *and* pulls the chain snapshots, which does not scale to a scanner
  universe on a five-minute loop -- so the loop stopped being the only way
  in: those five stay warm, and everything else is computed the first time
  somebody looks at it and then cached for the same 300 s the loop used
  (`app/market_data/gex_cache.py`, TTL plus one lock per symbol so a burst
  of widgets on one ticker costs one fetch). A symbol nobody has looked at
  before therefore takes a couple of seconds on first view, which the
  widget says rather than showing an empty state; a symbol with no usable
  chain is left alone for a minute before being tried again.
  Every reading carries **what it rests on** -- the number of strikes with
  usable greeks and the total open interest across them -- and the widget
  marks a thin one as such. Deliberately no minimum-liquidity threshold:
  on an illiquid name a "gamma wall" really can be a handful of contracts,
  and reporting the sample beside the number is honest in a way that
  silently suppressing it below some invented cutoff is not.
  **The nearest expiry on its own, and the expected move.** The 45-day
  profile is the month's positioning and moves slowly; intraday the
  contracts expiring today or tomorrow carry many times the gamma per
  contract and their walls shift during the day. Every reading therefore
  also carries the nearest expiry's own profile (`near`: today's while it
  trades, after the close or on a weekend the next listed one, tagged with
  its days) -- its call wall, put wall and flip draw on the chart as
  dashed "0DTE …" / "3d …" levels ("Near GEX" in the Levels checklist).
  Alpaca computes no greeks for a contract expiring today
  (Black-Scholes divides by time to expiry), so for 0DTE the gamma is
  **solved from each contract's own quote** with the same solver the
  replayed chain uses (`app/options/payoff.py`), and the reading says so
  (`source: "solved"`). Open interest is last night's, so positions opened
  today are not in it -- a busy 0DTE strike is understated, not invented.
  The **expected move** (`expected_move`) is read off the at-the-money
  straddle to that expiry: call mid plus put mid at the strike nearest
  spot. Under Black-Scholes the ATM straddle's price *is* the expected
  absolute move (E|X| of a normal is σ·√(2/π), which is exactly what the
  straddle prices), so the band drawn on the chart is spot ± straddle
  ("EM 0DTE ±", "EM 3d ±"; "EM band" in the Levels checklist), and
  `one_sigma` = straddle × √(π/2) ≈ 1.25×
  is the 68 % band, shown in the chart badge's tooltip and the GEX Plan.
  Symmetric by construction; skew is ignored, as every straddle-based
  expected move ignores it. The 45-day aggregate itself is unchanged by
  all this: it still leaves today's expiry out, as it always has.
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
  Right-clicking a Dock tab opens a small menu: **Open in new window**
  adds a second instance of that widget as a new tab beside the original
  (a second chart is *pinned* to its own symbol -- typed into its header
  or dropped from a scanner/watchlist row -- and ignores the scanner's
  selection; copies of other widgets follow it), plus **Float** and
  **Close**. Copies are saved with the layout, pinned symbol included.
  Every symbol cell in the app is a drag source -- scanner rows and heatmap
  tiles, watchlist rows, news-feed symbols, positions/orders/trades in the
  trading panel, and option contracts in the chain, the spread ticket's
  legs and open spreads -- and both the main chart and any chart copy
  accept the drop (the Options widget too): a stock loads its chart, an
  option contract its premium chart.
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

## Broker login per user

Every login trades on **its own Alpaca account**. The keys in
`backend/.env` (`ALPACA_API_KEY_ID` / `ALPACA_LIVE_*`) belong to the
**admin** -- the first account ever created, or one made with
`python -m scripts.create_user <name> "<display>" --admin`. Everyone else
sees "No Alpaca account connected" on the Trading and Options widgets (a
typed 503, `broker_not_connected`) until they enter their own key pair in
**Settings → Broker**, and can use Simulation mode meanwhile.

- **Settings → Broker** has one card per account, Paper and Live. A pair
  is verified against Alpaca once (`get_account`) and stored encrypted;
  the card shows the key id's last characters, the account number, its
  status and options level, and whether the keys are the user's own or the
  operator's from `.env`. Connecting a live pair asks for the typed `LIVE`
  like every real-money action; `TRADING_ALLOW_LIVE` and `TRADING_ENABLED`
  stay global switches the operator controls.
- **What is per user:** account, positions, orders, fills, closed round
  trips (the `trades` table carries `user_id`; rows from before the split
  read as the admin's), options spreads, option orders and triggers (the
  trigger loop closes each trigger on its owner's account, and parks it
  when the owner has no keys for that account). The trading widget's badge
  shows the connected keys (`PAPER · …ABCD`).
- **What stays the operator's:** all market data -- bars, streams, chains,
  snapshots, news, screener, the universe, GEX -- runs on the operator's
  key pair: the **first admin's stored paper pair** from Settings → Broker
  when there is one, else the `.env` keys. Chosen once at startup (the
  data clients and websocket streams live for the process), so after
  rotating keys in the dialog the Broker tab says "restart the backend"
  for market data; trading uses the new keys at once. With stored admin
  keys the `.env` pair is optional. Contract lists via the trading
  endpoint (`get_option_contracts`, assets) use the same pair; they are
  reference data.
- **Storage:** `user_broker_keys` in the same sqlite file, the secret
  encrypted with Fernet under a key derived (HKDF) from
  `BROKER_ENCRYPTION_KEY`, or from `SESSION_SECRET_KEY` when that is not
  set. Rotating either makes the stored secrets unreadable: users re-enter
  them. The API never returns a secret. Users are still created by the
  operator (`scripts/create_user.py`); there is no self-registration.

## Options

Everything the dashboard does with options, in one place. The short
version lives in the Options-widget bullet above; this is the long one.

### Accounts, approval levels and what runs where

- Options trade on the **Paper** and **Live** accounts through Alpaca, and
  in **Simulation** mode through the dashboard's own simulated options
  book (see *Simulation & Replay: options* below) -- live prices, or the
  replayed moment during a history replay. The same widget, ticket, chain
  and triggers on all three; only where the fill happens differs.
- Alpaca approves options per account in **levels**: level 2 buys calls
  and puts outright, level 3 is needed for every spread here. The widget
  header shows the account's level (`L3`), the ticket refuses a strategy
  the account is not approved for and says which level it needs. Paper
  accounts come with level 3; a live account has whatever Alpaca granted.
- Market data (chain, snapshots, streams) always comes from the paper /
  market-data key pair, whichever account an order goes to. The
  `ALPACA_OPTIONS_FEED` decides the quality: `opra` is the paid
  consolidated options feed with real bid/ask, greeks and open interest;
  `indicative` is Alpaca's free approximation.
- Live orders need the same three things as live equity orders: the live
  key pair, `TRADING_ALLOW_LIVE=true`, and the word `LIVE` typed in every
  confirm dialog. Instant-fire hotkeys and the widget's own hotkeys are off
  in Live.

### The widget

Header: the mode badge, the **Chain | Open spreads** tabs (the count of
open positions in brackets), and on the right the account's options
buying power, level and feed. Pick a symbol anywhere (scanner, watchlist,
news feed, a dropped row) and the widget loads that underlying.

The **expiry strip** lists every expiration within the next 60 days with
its days-to-expiry (`0d`, `1d`, `5d` …) and, on hover, the number of
contracts. The first expiry with at least one day left is preselected, so a
0DTE is a deliberate click. Contracts are fetched once per underlying and
cached for five minutes; the selected expiry's quotes refresh every 15 s
(server-side cache of 15 s, so two viewers share one fetch).

Hotkeys inside the widget (not in Live): `[` / `]` previous / next expiry,
`5`–`9` the five spread strategies in button order, `+` / `−` width. The
two outright longs have no key because `0`–`4` belong to the equity
ticket.

### Reading the chain

One row per strike, calls on the left, puts on the right, strikes ±10%
around the spot. Columns, from the outside in:

| Column | Meaning |
|---|---|
| **OI** | Open interest: contracts outstanding. Liquidity and where the crowd is positioned; `61.0k` style shorthand. |
| **IV** | Implied volatility, annualised, backed out of the contract's own price. Higher IV = dearer premium; the smile across strikes is visible top to bottom. |
| **Δ** | Delta: how much the premium moves per $1 of the underlying, and roughly the probability of expiring in the money. Calls 0 to 1, puts 0 to −1. |
| **Bid / Mid / Ask** | The market. Mid is what the ticket prices from; the bid/ask width is the cost of getting in and out. |

Shaded cells are **in the money** (calls below spot, puts above); the
grey **spot** divider row marks the current price and the table scrolls to
it when the chain loads. A "—" means Alpaca returned no value: no greeks
or IV for contracts expiring today (its Black-Scholes cannot divide by a
zero time to expiry), and no market for an untraded strike. A hover shows
the OCC symbol of the contract (`SPY260904C00765000`: root, `yymmdd`,
`C`/`P`, strike × 1000).

Cells of the kind the current strategy trades are clickable and carry a
pointer; the other side is shown but inert (an iron condor picks both).
The selected legs are outlined: **green = long** (bought), **red = short**
(sold). Any cell can also be **dragged onto a chart** to open that
contract's premium chart (see below).

### Strategies

All defined-risk. Every spread uses one expiry; the ticket sends a spread
as one multi-leg (MLEG) order, a long call/put as a plain option order.
Money figures below are per share; multiply by 100 per contract and by
the quantity. `W` is the distance between the strikes, `D` a debit paid,
`C` a credit received.

| Strategy | Legs | Direction | Max profit | Max loss | Breakeven | Collateral | Level |
|---|---|---|---|---|---|---|---|
| **Long call** | buy 1 call | debit | unlimited | premium | strike + premium | premium | 2 |
| **Long put** | buy 1 put | debit | strike − premium | premium | strike − premium | premium | 2 |
| **Bull call** | buy lower call, sell higher call | debit `D` | `W − D` | `D` | long strike + `D` | `D` | 3 |
| **Bear put** | buy higher put, sell lower put | debit `D` | `W − D` | `D` | long strike − `D` | `D` | 3 |
| **Bull put** | sell higher put, buy lower put | credit `C` | `C` | `W − C` | short strike − `C` | `W − C` | 3 |
| **Bear call** | sell lower call, buy higher call | credit `C` | `C` | `W − C` | short strike + `C` | `W − C` | 3 |
| **Iron condor** | bull put + bear call, same expiry | credit `C` | `C` | `max(put W, call W) − C` | put short − `C` and call short + `C` | `max W − C` | 3 |
| **Straddle** | buy put + buy call, one strike | debit `D` | unlimited | `D` | strike ± `D` | `D` | 3 |
| **Strangle** | buy OTM put + buy OTM call | debit `D` | unlimited | `D` | put − `D`, call + `D` | `D` | 3 |
| **Call / put fly** | buy wing, sell body ×2, buy wing | debit `D` | wing width − `D` | `D` | low + `D`, high − `D` | `D` | 3 |
| **Iron fly** | bull put + bear call sharing the body strike | credit `C` | `C` | wing − `C` | body ± `C` | wing − `C` | 3 |
| **Calendar** | sell near expiry, buy later expiry, one strike | debit `D` | from the risk chart | `D` | from the risk chart | `D` | 3 |
| **Diagonal** | calendar with two strikes | debit `D` | from the risk chart | `D` | from the risk chart | `D` | 3 |
| **Covered call** | sell a call against 100 held shares per contract | credit `C` | strike − share price + `C` | share price − `C` | share price − `C` | the shares | 1 |
| **Cash-secured put** | sell a put against `strike × 100` of buying power | credit `C` | `C` | strike − `C` | strike − `C` | strike × 100 | 1 |

When to reach for which: a **long call/put** is the directional bet with
the most delta and the most theta bleed. The **debit verticals** (bull
call, bear put) buy direction with a capped cost. The **credit verticals**
(bull put, bear call) sell a level you expect to hold -- they win on time
and on the underlying staying on your side of the short strike, and the
typical loss-to-profit ratio is 2–3 : 1. The **iron condor** sells both
sides for a range-bound day. A 0DTE credit spread at a level is the
common day-trading shape; the ticket warns because Alpaca force-closes
same-day-expiry positions around 15:15 ET.

**Straddle / strangle** buy movement in either direction (earnings, a
binary event); the debit is the whole risk and theta is the enemy.
**Butterflies** are cheap bets on a narrow target: the body is sold
twice, the wings bought once each; an **iron fly** is a sold straddle
with wings. **Calendar / diagonal** sell the near expiry's faster time
decay against a later expiry; their profit shape depends on the long
leg's remaining time value at the short expiry, which is why the numbers
come from the risk chart rather than a formula. **Covered call /
cash-secured put** are the income writes: the ticket reports what covers
them (shares held, buying power) and refuses an uncovered one. Alpaca
treats the writes as level 1 and everything with more than one option
leg as level 3; a covered call is sent as a plain sell-to-open order, a
calendar as a two-expiry multi-leg order.

The preview refuses a price that makes no sense: a net price at or above
the width (`Net price 0.55 is not below the spread width (0.5)`), a long
put priced above its strike, a credit spread the market actually quotes
as a debit ("the market quotes this credit spread the other way round --
check the legs").

### Auto-pick and clicking strikes

**Auto-pick** (on load, on a new expiry / strategy / width, and the button
of that name) chooses the legs like this -- the delta targets below are
the defaults of the ticket's **Short** control, which sets how far out
the short leg(s) go for credit verticals and writes, the iron condor and
the strangle, either as a **delta** (0.05 - 0.45) or as **strikes from the
spot** (0 = the first strike outside the spot, the tightest corridor; a
1-strike condor on SPY is a 2-dollar corridor). The setting is kept per
strategy group in the browser; `Shift + +/-` steps it, the ticket shows
the resulting corridor:

- **Credit vertical:** the short leg is the out-of-the-money contract
  whose |delta| is nearest **0.30** (roughly a one-standard-deviation
  strike, ~30% chance of finishing in the money); the long leg is
  **Width** strikes further out. Without greeks (0DTE) the short goes
  ~3% out of the money.
- **Debit vertical:** the long leg is the strike nearest the spot, the
  short leg Width strikes out of the money.
- **Iron condor:** both short legs at ~**0.20** delta, wings Width strikes
  out.
- **Long call / put:** the strike nearest the spot.

- **Straddle:** the strike nearest the spot. **Strangle:** put and call
  at ~**0.25** delta each side.
- **Butterflies:** body at the spot, wings *Wings* strikes either side
  (the iron fly the same on both kinds).
- **Calendar:** the strike nearest the spot that both expiries quote;
  **diagonal:** short leg at ~0.30 delta OTM in the near expiry, long leg
  at the spot in the later one. The later expiry defaults to the first
  one at least a week after the short; the ticket's *Long expiry* list
  changes it, *Calls / Puts* the kind, and *chain shows short / long*
  which expiry's chain the table shows and a click sets.
- **Covered call / cash-secured put:** ~0.30 delta out of the money.

**Width** is a strike count, not a dollar amount: 2 means "two rows
apart", so it follows whatever strike spacing the chain has ($1 on SPY, $5
on a high-priced name).

**Clicking** a strike moves the leg that keeps the spread's shape. For a
bullish pair (bull call, bull put) the long leg sits below the short one;
for a bearish pair above. A click on the far side of the short leg moves
the long leg, a click on the short's side moves the short -- and if that
would collapse the spread, the long leg is pushed one strike out. For an
iron condor the put wing's higher strike is its short and the call
wing's lower strike its short; clicking beyond a short sets that wing's
long. A manual pick switches auto-pick off until the symbol, expiry or
strategy changes.

### The ticket

- **Spreads / Contracts:** quantity. Per-order ceilings:
  `TRADING_MAX_OPTION_CONTRACTS` (20) on paper,
  `TRADING_LIVE_MAX_OPTION_CONTRACTS` (5) live; the collateral must also
  fit under the account's order-notional ceiling and its options buying
  power.
- **Max debit / Min credit / Max premium:** the limit per spread (or per
  contract), prefilled with the **natural** (buy legs at the ask, sell
  legs at the bid: fills at once) or the **mid** (the better price, but
  Alpaca's paper account only fills a multi-leg order against the natural,
  so a mid limit often rests), whichever **Mid | Natural** is set to -- the
  choice is remembered and the single-contract ticket uses it too. The
  limit re-derives on every change until you type; **Reset** returns to
  the prefill. Alpaca's MLEG limit is signed -- positive for a debit,
  negative for a credit -- and the confirm dialog shows that signed value.
- **Preview** (300 ms after any change, server-side): pay/receive × 100 ×
  qty, the **mid** and the **natural** price (buy legs at the ask, sell
  legs at the bid: the worst fill a marketable order gets), spot and DTE,
  max profit / max loss / breakeven(s), collateral against the account's
  options buying power, the ceilings, and each leg with its mid and delta.
  Clicking a leg opens its premium chart; legs can also be dragged onto a
  chart.
- **Warnings** appear for: expiring today; missing greeks on a leg; a
  wide market (bid/ask more than a quarter of the mid apart -- a mid
  limit may not fill); and a spread the market quotes the other way
  round.
- **Risk** (the payoff diagram, under the summary, collapsible): P&L per
  position over the underlying's price. The solid line is the position
  **at expiry** -- for a calendar at the *short* expiry with the long leg
  still valued -- with the profit and loss areas tinted; the dashed line is
  **today**, every leg priced by Black-Scholes at its own implied
  volatility (r = 0, no dividends, no skew model -- an estimate, like the
  chain's greeks, and absent when a leg has no IV). The zero line, the
  spot, the breakevens and max profit / max loss are marked; hovering
  reads off S and the P&L on both curves. The same chart sits under each
  position in Open spreads, priced from fresh quotes every 15 s.
- **Confirm** lists every leg, the signed limit, the risk figures and the
  warnings again; in Live it demands `LIVE`. Orders are day orders (the
  only kind Alpaca allows for options). A rejection from the broker comes
  back with its reason.

Contract symbols always come from the live chain rather than being
composed, so an adjusted root (`SPY1` after a corporate action) or a
non-tradable contract is handled by the data.

### Open spreads

Alpaca reports option positions one contract at a time; the tab groups
them back into spreads by underlying and expiry and classifies them by
shape: two legs of one kind with opposite sides → a vertical, four legs
with the right ordering → an iron condor (or an iron fly when the shorts
share a strike), two longs of both kinds → straddle / strangle, three of
one kind 1-2-1 → a butterfly, one long contract → long call/put, a lone
short put → cash-secured put, a short call with 100 held shares per
contract → covered call, a short leg and a later-expiry long leg of the
same kind → calendar / diagonal, anything else → **custom**. A lone short contract or unequal
quantities are flagged **broken** (the remains of a spread closed one leg
at a time); **expires today** flags a 0DTE position.

Columns: account, expiry with DTE, strategy, quantity, **net entry** (per
share, positive was paid, negative received), mark, P&L. Click a row for
its legs (each draggable to a chart, each a link to the premium chart)
and the controls:

- **Close** reverses every leg in one MLEG order at the current mid
  (editable; a single remaining leg closes with a plain order). The
  preview shows mid and natural for the closing package.
- **Triggers**: exits the dashboard keeps itself, because Alpaca accepts
  no stop orders on options. Two kinds of bound, combinable in one
  trigger:
  - **below / above** on the **underlying's** last price ("close if SPY
    trades below 740");
  - **premium ≤ / ≥** on the position's own **mark** -- the mid of closing
    the package, per share. For a long call `≤` is a stop and `≥` a
    target; for a credit spread the mark is the cost to buy it back, so
    `≤` is the take-profit and `≥` the stop.
  A backend loop checks every 2 s during the regular session (one batched
  stock-price fetch and one option-snapshot fetch per tick) and closes the
  position with a limit stepped 0.05 toward the natural price so it fills
  rather than rests (`TRADING_OPTIONS_TRIGGER_SLIPPAGE`). Triggers are
  stored in `scanner_history.sqlite3` per user and account, survive a
  restart, and show their status: **active**, **fired** (with the price
  that fired it and the order id), **failed** (three attempts without an
  order out), **orphaned** (the legs are no longer held), **cancelled**.
  Arming a live trigger asks for `LIVE` once; the loop does not ask again
  when it fires. Triggers only fire while the backend runs and the
  session is regular.

The same triggers are listed under the position row and, for a single
contract, in the premium chart's own editor.

### The premium chart

A chart of an option contract's own price. Open it by clicking a leg in
the ticket summary or under Open spreads, by clicking a single option
order in the Orders tab, by dragging any contract cell/leg onto a chart,
or by typing the OCC symbol into a pinned chart copy's header.

- **Data:** the contract's minute bars (1m/5m/15m) or native hourly /
  daily / weekly bars from Alpaca's option bars endpoint; **live trades**
  from Alpaca's option websocket shape the forming candle tick by tick,
  the newest **quote** shows as `bid / ask` in the header; closed candles
  are re-fetched every 5 s (Alpaca streams no option bars), higher
  timeframes every 30 s. An illiquid contract moves only when someone
  trades it -- the bid/ask keeps updating regardless.
- **Header:** `SPY 4 Sep 765C`, a PREMIUM badge, the last price, bid /
  ask, and a `SPY ↗` button back to the underlying's chart. Every other
  widget keeps working on the underlying (ticket, chain, news, info).
- **Contract ticket** under the header: Contracts, **Buy** (to open: the
  long call/put path with its preview, ceilings and confirm dialog) and
  **Sell** (to close what is held -- never a naked write). The dialog
  shows mid, natural, spot, DTE and an editable limit. Held quantity,
  entry, P&L and any working order are printed beside it; working orders
  draw as dashed lines at their limits, the held entry as a solid line.
  For a held contract a second row arms a **premium trigger** (`≤` / `≥`)
  and lists active ones with Cancel; their bounds draw as dashed
  stop/target-coloured lines.
- **Levels** (Levels menu; each starts visible the first time it can be
  shown):
  - **Quote** -- bid and ask lines, live.
  - **Session** -- today's high and low of the premium and the previous
    session's close.
  - **Entry ±** -- +50%, +100% and −50% of a held contract's entry, the
    usual "close at 50% profit / cut at 50% loss" marks.
  - **Intrinsic** -- `max(S − K, 0)` for a call, `max(K − S, 0)` for a
    put, from the underlying's price (polled every 5 s). The premium
    trades above it; the gap is the time value. Hidden while zero.
  - **Expected move** -- where the premium lands on a ±1σ move of the
    underlying: `move = S × IV × √T` with `T` the shorter of the rest of
    today and the time to expiry, then `premium ± Δ·move + ½·Γ·move²`
    (a second-order estimate, not a repricing). The labels carry the
    underlying prices behind them.
  - **Theta** -- the premium in an hour and at the close if the
    underlying stands still, from the snapshot's daily theta.
  - **EMA (premium)** -- EMA 9 and 20 of the premium on the displayed
    bars.
  - **VWAP** -- session-anchored VWAP of the premium from the contract's
    bars (the legend button; no premarket anchor, options only trade in
    the regular session).
  - The underlying's levels (daily range, GEX walls, its EMAs) are
    deliberately absent: they live on another price axis.
- Greeks and IV come from a contract snapshot polled every 30 s; near
  expiry Alpaca returns none, so Expected move and Theta disappear on a
  0DTE.
- On a cheap contract the axis can show values below zero: the bottom
  fifth of the pane is reserved for volume and the library extends the
  scale linearly.

### Idea: Claude on this chain (needs `ANTHROPIC_API_KEY`)

The Options widget's third tab asks Claude for up to three **option
structures** on the selected underlying — strategy, expiry and strikes,
concrete enough to trade — and one click loads any of them into the ticket
beside it. Nothing is ordered: the ticket still needs the usual submit (and
the usual typed confirmation in Live mode).

**The model chooses the shape; the server decides what it costs.** Four
steps, and only the first is the model's:

1. `app/ai/options_context.py` gathers what a structure decision actually
   turns on: the chain across three candidate expiries (~7 / ~21 / ~45 DTE,
   never 0DTE — Alpaca cannot compute greeks for a contract expiring today),
   condensed to strikes that are listed, tradable, quoted on both sides, not
   absurdly wide and actually held; GEX with the sample behind it; the
   underlying's VWAP, 30-minute move, prior-week move and average daily
   range; the chart's own horizontal levels from hourly bars; the latest
   headline; implied vol; and the next earnings date.
2. `app/ai/options_idea.py` asks Claude (`claude-opus-5`) for structures as
   plain leg lists, with a reason and a **risk note** each.
3. `app/ai/options_resolve.py` snaps every proposed strike onto one that is
   really listed and restores the ordering each strategy requires — a
   deterministic repair, not a second round trip: re-asking is slower,
   costs another call and is no likelier to land on a real strike. The
   same step reads a calendar or diagonal off its sold leg's expiry when
   the model stated the two expiries the other way round (a restatement of
   one structure, not a different one); a bought leg dated *earlier* than
   the sold one is a reverse calendar and is refused as such.
4. `OptionsService.preview` prices the result through the same path the
   ticket uses.

So every number on a card — net debit or credit, max profit, max loss,
breakevens, collateral, the warnings — is the options stack's, computed
against the live chain and checked against your account's options level and
the notional/contract ceilings. The only thing taken on trust is the choice
of structure.

**Proposals that don't survive are shown, not dropped.** "Iron condor —
options level 3 required" is useful; a quietly shorter list would read as
"nothing appeals today", which is a different and wrong statement. Each
card also carries a **Support N/10** — how well the available data backs
that structure over the alternatives, *not* a probability of profit — and
the tab says in one line what the model could actually see (GEX? news?
earnings? how many days of IV history?), because a suggestion made with all
of it looks identical in prose to one made with none.

**On implied vol, the honest split.** The term structure (ATM IV per
expiry), the skew, and implied over 20-day realised volatility are all
computable from one snapshot and are in the payload from the first run — a
front expiry standing well above the ones behind it is an event being
priced into that week, which is what decides debit versus credit before any
direction does. A real **IV rank is not**: it measures today's IV against
its own trailing range, and that range has to be accumulated. The app now
records one ATM reading per symbol per session (`app/options/
iv_history_store.py`, same SQLite file as everything else) as a side effect
of serving a suggestion, so history builds for the symbols you actually
look at — and `iv_rank` stays `null` until roughly a month of sessions
exists. The prompt is told explicitly that null means *not yet known*,
never "IV is not elevated"; the same rule applies to every other field that
can be absent.

One answer takes a while — three expiries of chain plus the context, then
`claude-opus-5` over all of it, non-streaming — so the request is owned by
the Options widget, not the tab: "Load into ticket" switches to the Chain
tab and the cards are still there when you come back, and a request still
in flight lands wherever you have gone meanwhile (the tab reads **Idea…**
until it does).

Offered on all three accounts -- in **Simulation mode** the chain is the
live one and only the fill is simulated, so the suggestion is as well founded
there as on paper, priced against the simulated book's collateral and level.
Not offered during a **History Replay**: that chain is synthetic (bid/ask
derived from the last print, IV solved back out of it, no open interest) and
GEX, news, earnings and IV history would be today's, which for a past date is
look-ahead. The tab disappears while a session is active, and the endpoint
refuses a call that arrives anyway (`replay_active`).

Framed as descriptive annotation, not investment advice — the same line the
AI Trade Ideas widget and the GEX plan draw.

### Options elsewhere

- **Orders tab:** option orders show as `SPY 4 Sep 765C`; a multi-leg
  order lists its legs. Clicking a single option order opens its premium
  chart, an MLEG parent the underlying. Cancel works as for stocks. The
  **Limit** of a working single-contract (or stock) limit order is
  click-to-edit: a bid that rests below the ask -- the usual reason a
  long put "does not fill" -- can be moved up in place (Enter or Save;
  Alpaca replaces the order, the id changes). A multi-leg order cannot be
  re-priced at Alpaca: cancel it and place the spread again, at the
  natural if it should fill now. Not offered in Live mode, where a price
  change goes through a fresh confirmed ticket.
- **Positions tab** shows stocks only and says where the option positions
  are (in Simulation mode the simulated book's contracts are part of the
  same positions list, so the premium chart's ticket finds what is held).
- **Trading Journal / Trades:** closed option round trips appear with the
  contract symbol and their P&L, per account.
- The **Options widget follows the chart**: with a contract on the chart
  it selects that expiry and marks the strike as a long call/put.

### Configuration

| Setting | Default | Meaning |
|---|---|---|
| `ALPACA_OPTIONS_FEED` | `opra` | Options data feed for chain, snapshots, stream and GEX. |
| `TRADING_MAX_OPTION_CONTRACTS` | 20 | Spreads or contracts per order, paper. |
| `TRADING_LIVE_MAX_OPTION_CONTRACTS` | 5 | Same, live. |
| `TRADING_MAX_ORDER_NOTIONAL` / `TRADING_LIVE_MAX_ORDER_NOTIONAL` | 30 000 / 5 000 | Ceiling on a spread's collateral (max loss). |
| `TRADING_OPTIONS_TRIGGER_CHECK_INTERVAL` | 2.0 s | Trigger loop cadence. |
| `TRADING_OPTIONS_TRIGGER_SLIPPAGE` | 0.05 | How far a trigger's closing limit steps from the mid toward the natural price. |

### Simulation & Replay: options

Simulation mode has its own options book (`backend/app/trading/sim/
options_book.py`), so every strategy the ticket offers can be practised
without an Alpaca order -- and, in a **History Replay**, against a past
day, which is how you rehearse a strategy at the weekend.

- **Starting a replay switches the app to Simulation.** Replay is a
  simulation tool: Paper and Live keep talking to the real account whatever
  the clock says, so a chain or ticket next to a replayed chart would be
  live data that just sits there on a weekend. Rather than have that
  explained, *Start replay* puts the app into Simulation itself and *Stop*
  returns to the mode it found (Live is never restored; a page load never
  wakes up in Live either). Switch away during a session and both the
  replay panel and the Options widget say so, with a one-click way back.
- **Any symbol replays in the chart.** The session's symbol list decides
  what the ranked scanner views contain; the chart is not limited to it.
  Select SPY from the watchlist during a replay and its 5-minute bars are
  fetched on first request (`ReplayEngine.ensure_bars`, same disk cache)
  and clipped to the clock like everything else. The option chain already
  worked this way (the replay option engine fetches an underlying's own
  bars when the session lacks it).
- **The risk chart's what-if sliders.** Under the ticket's payoff chart,
  *Time* moves the model's clock forward by hours and *IV* scales every
  leg's implied volatility (±50 %); a dotted third line shows the position
  repriced there, and the hover readout gives all three values. The
  repricing runs in the browser (`frontend/src/utils/blackScholes.ts`) on
  the legs, net price and valuation moment the payoff now carries, so a
  slider notch costs no request. Same model as the today curve -- constant
  IV per leg, no skew, no dividends, no rate -- and honest about it: the
  point is the size of what time and vol do to a short-dated option before
  the move arrives, not the fill. The **?** button in the widget header
  opens a reference for every term on the widget, from the expiry strip to
  Min credit, Mid/Natural and the sliders.
- **Where the prices come from.** Live in Simulation mode: the same chain
  and snapshots the paper account sees. In a replay: the contracts that
  existed on the replayed day (Alpaca lists expired contracts, from
  February 2024 on) and their **1-minute bars**, read at the replay clock
  -- the last print at or before `as_of` is the price. Alpaca keeps no
  historical bid/ask, IV, greeks or intraday open interest for options,
  so a replayed chain is synthetic where it has to be: **Bid\*/Ask\*** are
  the last print ± `max(2 %, $0.01)`, **IV** is solved from that print by
  Black-Scholes (`implied_vol` in `app/options/payoff.py`), delta/gamma/
  theta follow from it, **OI shows "—"**, and a print older than 30
  minutes at the replay clock is drawn faded (its time is in the cell's
  tooltip and in the ticket's warnings). The chain, the spreads, the
  ticket preview and the risk charts refetch on every replay tick.
- **Fills.** A package fills at the *natural* -- bought legs at the ask,
  sold legs at the bid -- as one thing, no partial fills. The ticket's
  default limit in Simulation mode is the natural, so a plain "Place"
  fills at once; a limit the market has not reached rests as a **working
  package** (listed above *Open spreads*, with a cancel) and is checked on
  every tick of the sim fill loop, or on every replay step while
  replaying. Where a leg has no quote on the side it needs (a one-sided
  live quote), its mid or last plus the same slippage stands in.
- **Positions, P&L, journal.** Contracts are held per contract, grouped
  into spreads exactly like Alpaca's (bull put x2, iron condor...), marked
  at the book's price; cash moves by premium x 100. Closing (the *Close*
  button, the premium chart's *Sell*, or a fired trigger) writes the round
  trip to the Trading Journal with the contract multiplier of 100 and --
  in a replay -- the replayed day's date, so a rehearsed session reads
  like one. Credit spreads reserve their collateral (width less the
  credit, the strike for a cash-secured put) from the simulated buying
  power; a covered call needs the shares in the simulated book.
- **Expiry.** A contract still held at 16:00 ET on its expiry day settles
  at intrinsic value against the underlying (no assignment into shares) --
  in a replay, when the clock crosses that moment.
- **Triggers** work as on the real accounts, stored with account `sim`,
  and are checked by the book's own loops against the book's own prices
  -- never by the live trigger loop against Alpaca positions. They need no
  `TRADING_ENABLED`.
- **The premium chart** of a contract replays too: 1-minute premium bars
  of the replayed day up to the clock, levels and EMAs computed from them,
  no live stream and no live higher-timeframe bars while a session is
  active (either would show the future).
- **Backend restart** during a replay: the session and the book survive
  (sqlite); the option bars are fetched again on the next chain (cached on
  disk under `backend/.cache/bars`, a finished day never changes).

### Limits worth knowing

- The simulated book fills at the last print ± slippage in a replay;
  illiquid strikes far from the money may not have printed for hours, and
  the fill uses that stale price. Stay near the money for realism.
- No partial fills, no assignment into shares, no exercise: expired
  contracts settle in cash at intrinsic value.
- A replayed session needs the underlying's contracts and bars per
  expiry: the first chain of a new expiry (or a new day in a multi-day
  replay) takes a few seconds while they load.
- Paper fills of an MLEG at the mid can rest a while; the trigger loop
  compensates with its stepped limit, manual closes do not.
- Triggers are not broker-side orders: they fire only while the backend
  runs, and only in the regular session.
- Alpaca force-closes 0DTE positions around 15:15 ET and computes no
  greeks for them.
- FINRA's pattern-day-trader rule (three day trades per five sessions
  under $25k equity on a margin account) applies to Alpaca accounts
  regardless of the holder's country, and option round trips count.
- Naked writing is not offered anywhere (it needs level 4 and carries
  unbounded risk).

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
- **Options**: the simulated book (Simulation mode, history replay) has
  no historical bid/ask, IV or open interest to work from -- replay
  quotes are the last print ± slippage with a solved IV, and an illiquid
  strike's last print can be hours old (see *Simulation & Replay:
  options*). Paper MLEG fills at the mid can rest unfilled for a
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
