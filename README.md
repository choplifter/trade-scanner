# Stocks in Play — Trading Dashboard

A personal, locally-run "stocks in play" scanner + chart dashboard in the
spirit of trade-ideas.com, bearbulltrader.com, and warriortrading.com: a live
**screener** (table or treemap heatmap view) where you build your own filters
over 22 fields and results stream in over a WebSocket, with gainers /
premarket gainers / losers / most-active shipped as editable presets ranked by
a catalyst-boost/fade-risk-aware scoring formula, a click-to-chart candlestick
widget with a session-anchored VWAP
overlay, EMA/premarket/weekly/monthly range indicators, and company
info/news, a dashboard-wide momentum alarm, AI-generated trade-idea
annotations, a scanner-wide benchmark against SPY, a persistent scanner
match history with fade-risk analysis, one-click backtesting of whatever
screen you're looking at, CLI tools for re-validating the ranking formula
against live and historical data, and a Plotly Dash analytics app — powered by Alpaca Markets' real-time IEX data feed, with
float/market cap/short interest/company info and gap-filling news layered
in from Financial Modeling Prep and FINRA.

## 1. Get Alpaca API credentials (required for live data)

1. Sign up free at https://alpaca.markets and create a **paper trading**
   account (no funding required — you only need it for API access, not to
   place trades).
2. In the Alpaca dashboard, generate an **API Key ID** and **Secret Key**.
3. Copy `backend/.env.example` to `backend/.env` and fill in
   `ALPACA_API_KEY_ID` / `ALPACA_API_SECRET_KEY`.

Without valid credentials the app still starts, but the universe stays empty
and scanners will show no rows.

### Optional keys

Everything below is optional — the app runs fine without any of it, just
with fewer annotations/columns.

- **`ANTHROPIC_API_KEY`** — powers the "AI Trade Ideas" widget (Claude picks
  and annotates the 3 most notable scanner setups). Get a key at
  https://console.anthropic.com → API Keys.
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
  invisible over a weekend/holiday. Rows need at least $1M of today's own
  dollar volume to appear at all (`SCANNER_MIN_DOLLAR_VOLUME`), applied
  *before* your filters, so a thin name that clears the universe filters but
  hasn't traded much today doesn't clutter the list.
- **Presets, not hardcoded views.** **Top Gainers**, **Top Losers** and
  **Most Active** are ordinary screens you can open, read and edit — load one
  and its filters and sort appear in the filter bar. Editing it shows
  "Custom" instead of claiming you're still on the preset. **Most Active**
  ranks by **dollar** volume, direction-agnostic: a raw share-volume level is
  the most IEX-distorted number available, since IEX is one exchange's varying
  slice of the tape, whereas weighting by price tracks where the money
  actually went. **Premarket Gainers** is the one exception and stays a fixed
  view — it's the gap frozen at the 09:30 open, not a question about the
  current rows, so no filter expresses it and the filter bar hides itself
  there.
- **Ranking formula**: gap % magnitude is boosted 1.15x on the **gainers**
  views when a genuine news catalyst is behind the move, and any view's
  magnitude is discounted 0.7x when RVOL exceeds 15x -- both tuned from this
  app's own scanner-history win-rate data (`app/scanners/formulas.py`'s
  `rank_score`). The catalyst boost is deliberately gainers-only: re-checked
  per view, the headline edge is +9.1pp on gainers but statistically
  indistinguishable from zero on losers and most-active, so it's applied only
  where it's measurable (see `_CATALYST_BOOST`, and
  `scripts/ranking_drift_report.py` to re-check as more data accumulates).
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
  **15m %** (trailing-15-minute price change, refreshed every 2 min --
  distinct from gap %, which is since prior close, so a symbol that already
  ran earlier and has since gone flat reads differently from one still
  actively moving right now), volume, RVOL, **RVol 1h**, and (when
  `FMP_API_KEY` is set) float, market cap, short interest % of float,
  exchange, and country. A **Table / Heatmap** toggle switches the same live
  feed to a treemap view (tile size = dollar volume, color = gap %,
  click-to-chart same as the table).

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
| `day_high` | Day High | currency | `gt`, `gte`, `lt`, `lte`, `between` |
| `day_low` | Day Low | currency | `gt`, `gte`, `lt`, `lte`, `between` |
| `spread_pct` | Spread % | percent | `gt`, `gte`, `lt`, `lte`, `between` |
| `volume_1h` | Volume (1h) | number | `gt`, `gte`, `lt`, `lte`, `between` |
| `volume_surge` | Volume Surge (vs prior 1h) | number | `gt`, `gte`, `lt`, `lte`, `between` |
| `rvol_1h` | Rel Volume (1h) | number | `gt`, `gte`, `lt`, `lte`, `between` |
| `is_hod` | At High of Day | boolean | `is_true`, `is_false` |
| `is_lod` | At Low of Day | boolean | `is_true`, `is_false` |
| `is_fade_risk` | Fade Risk | boolean | `is_true`, `is_false` |
| `is_stale` | Stale Price | boolean | `is_true`, `is_false` |
| `float_shares` | Float | number | `gt`, `gte`, `lt`, `lte`, `between` |
| `short_interest_pct` | Short % of Float | percent | `gt`, `gte`, `lt`, `lte`, `between` |
| `rank_score` | Rank Score | number | `gt`, `gte`, `lt`, `lte`, `between` |

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
Market cap, short interest, country, company name, recent headline and 15m %
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
| Volume Accelerating | `rvol_1h > 2`, `pct_change > 0` | `rvol_1h` desc |
| Low Float Runners | `float_shares < 20M`, `pct_change > 5`, `rvol > 3` | `pct_change` desc |

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

**Daily** can reconstruct 16 of the 22 fields — anything derivable from an
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
  a dashboard-wide alert for a fast, still-confirming *upward* move -- 15m
  % at least a threshold (5% default, `ALARM_MOMENTUM_PCT_THRESHOLD`)
  *and* the latest 5-minute candle confirms it three ways: closed at/near
  its high (shaved top, near-zero upper wick, `app/market_data/candle_shape.py`),
  closed green (close > open), and price trading above the session VWAP
  (`app/market_data/vwap.py`) -- the standard day-trading reference for
  "buyers are still in control." Long side only on purpose: a green-candle-
  and-above-VWAP requirement doesn't have a sign-flipped short-side
  equivalent, so downward moves aren't alerted at all. Only regular-session
  (09:30-16:00 ET) candles can trigger, and the trailing 15-minute window
  never crosses a day boundary -- without both guards a "15-minute move"
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
  candlestick chart, volume pane, and a session-anchored VWAP line (resets
  at 09:30 ET), fed by Alpaca's live minute-bar stream, plus a company info
  + recent news panel (name/sector/industry/description from FMP, headlines
  from Alpaca's news feed). Every symbol is clickable, not just the main
  scanner table — the AI past-picks table, the scanner benchmark table, and
  the scanner match history leaderboards all load into the same chart. A
  **Levels** toggle overlays EMA 9/20 (sourced from 1-minute bars
  regardless of the displayed timeframe) plus premarket/weekly/monthly
  range lines — a small pluggable indicator system
  (`backend/app/indicators/`): drop a new file in that directory exposing a
  `compute(ctx)` function and it shows up on the chart on the next
  request, no backend restart needed.
- **AI Trade Ideas** (needs `ANTHROPIC_API_KEY`): Claude ranks the 3 most
  notable current setups from gap %, RVOL, dollar volume, HOD status, news
  catalyst, VWAP position, 15-minute momentum, spread, multi-day context,
  float, and short interest — framed as descriptive scanner annotation, not
  investment advice. A past-picks performance table tracks how prior AI
  picks have actually moved since they were generated.
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
  resolution signals (time-of-day RVOL, the momentum alarm itself -- 15m %
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
- **Analytics app** (`/analytics`, Plotly Dash): a resizable 4-panel scanner
  heatmap + table + symbol detail + AI trade ideas view, plus separate pages
  for the scanner benchmark, scanner match history, cross-symbol
  correlation/comparison, and seasonality.
- **Two layout modes** (React app), switched by the header's **Layout:
  Panels / Grid** toggle and remembered across reloads: **Panels** is the
  default nested-splitter layout (drag the splitters to resize fixed slots),
  and **Grid** makes all five widgets freely repositionable -- drag a widget
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

- **Feed**: uses Alpaca's free real-time **IEX** feed, which is a
  single-exchange view, not the consolidated SIP tape. Gap %/volume numbers
  will be directionally right but won't exactly match SIP-based tools like
  Trade-Ideas. Upgrading later is a one-line change: set `ALPACA_DATA_FEED=sip`
  in `backend/.env` once you have a paid Alpaca market-data subscription.
  IEX can also occasionally return a stale/erroneous single-trade print for
  thin names; `resolve_last_price` (`app/scanners/formulas.py`) discards a
  print that falls outside 2x the day's own recorded high/low range, but
  this is a sanity check, not a substitute for the consolidated tape.
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
  still ranks here.
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
- **Watchlists**: roadmap item, not built yet.
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
  The 5% threshold and 5% shaved-top wick tolerance are unvalidated
  starting heuristics -- worth checking against `scanner_history.sqlite3`
  (same way the catalyst/fade-risk multipliers were validated and
  re-validated) once enough real triggers have accumulated. A 180-day
  daily-bar backtest of `is_shaved_top`
  *on its own* (no 15m % gate, since that needs minute data -- see below)
  found no meaningful standalone edge at a 1-day horizon for either
  gainers or losers, despite large samples -- doesn't confirm or refute
  the *combined* 15m %-and-shape signal the live alarm actually checks,
  just that shape alone isn't doing much work by itself.
- **Backtest harness**: daily-bar resolution only -- can validate the gap%/
  RVOL-based parts of the ranking formula against months of history, plus
  `is_shaved_top` on its own, but not the catalyst boost (needs historical
  news, unbuilt) or the momentum alarm as a whole (15m % needs historical
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
  daily bars carry consolidated-tape volume where the live scanner sees a
  partial IEX slice (the reason that view ranks on dollar volume at all),
  and `--max-symbols` selects the universe's top N *by dollar volume*, so
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
backend/scripts/   Standalone CLI tools (ranking drift report, backtest) -- run via
                    `python -m scripts.<name>` from backend/, not part of the running app
frontend/          Vite + React + TypeScript dashboard, lightweight-charts for candles
```

See `backend/app/` and `frontend/src/` for the module breakdown — each file
has a short docstring/comment explaining its role.
