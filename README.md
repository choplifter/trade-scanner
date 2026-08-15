# Stocks in Play — Trading Dashboard

A personal, locally-run "stocks in play" scanner + chart dashboard in the
spirit of trade-ideas.com, bearbulltrader.com, and warriortrading.com: live
gainers / premarket gainers / losers / most-active scanners (table or
treemap heatmap view) ranked with a catalyst-boost/fade-risk-aware scoring
formula, a click-to-chart candlestick widget with a session-anchored VWAP
overlay, EMA/premarket/weekly/monthly range indicators, and company
info/news, a dashboard-wide momentum alarm, AI-generated trade-idea
annotations, a scanner-wide benchmark against SPY, a persistent scanner
match history with fade-risk analysis, CLI tools for re-validating the
ranking formula against live and historical data, and a Plotly Dash
analytics app — powered by Alpaca Markets' real-time IEX data feed, with
float/market cap/short interest/company info layered in from Financial
Modeling Prep and FINRA.

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

- Four live scanners: **Market Gainers**, **Premarket Gainers**, and
  **Losers** (all ranked by % change from prior close), plus **Most Active**
  (ranked by share volume, direction-agnostic) — polled from Alpaca
  snapshots every 5s (regular hours) / 10s (premarket) and pushed over
  WebSocket. A movers-screener backstop periodically pulls in today's
  runners that weren't in the trailing-volume-filtered universe to begin
  with, and keeps running even while the market's closed so a big mover
  from the last session isn't invisible over a weekend/holiday. Rows need
  at least $1M of today's own dollar volume to appear in a ranked view
  (`SCANNER_MIN_DOLLAR_VOLUME`), so a thin name that technically clears the
  universe filters but hasn't traded much yet today doesn't clutter the
  list.
- **Ranking formula**: gap %/volume magnitude is boosted 1.15x when a
  genuine news catalyst is behind the move, and discounted 0.7x when RVOL
  exceeds 15x -- both tuned from this app's own scanner-history win-rate
  data (`app/scanners/formulas.py`'s `rank_score`). A roundup headline that
  just lists a symbol alongside a dozen others ("12 Health Care Stocks
  Moving...") doesn't count as a catalyst, only a story actually about that
  symbol does (`is_roundup_headline` in `app/market_data/news.py`). **FADE RISK**
  and **⚡ MOMENTUM** badges on the symbol cell surface the RVOL and
  momentum-alarm flags directly, not just indirectly via rank order.
- Scanner columns: symbol (click to copy its unambiguous `EXCHANGE:SYMBOL`
  TradingView format), a 📰 flag when there's a recent news headline
  (hover for the headline; refreshed every 15 min for whatever's currently
  ranked, not fetched per poll tick), company name, last price, gap %,
  **15m %** (trailing-15-minute price change, refreshed every 2 min --
  distinct from gap %, which is since prior close, so a symbol that already
  ran earlier and has since gone flat reads differently from one still
  actively moving right now), volume, RVOL, and (when `FMP_API_KEY` is set)
  float, market cap, short interest % of float, exchange, and country. A
  **Table / Heatmap** toggle switches the same live feed to a treemap view
  (tile size = dollar volume, color = gap %, click-to-chart same as the
  table).
- **Momentum alarm** (React app only, off by default, long setups only):
  a dashboard-wide alert for a fast, still-confirming *upward* move -- 15m
  % at least a threshold (5% default, `ALARM_MOMENTUM_PCT_THRESHOLD`)
  *and* the latest 1-minute candle confirms it three ways: closed at/near
  its high (shaved top, near-zero upper wick, `app/market_data/candle_shape.py`),
  closed green (close > open), and price trading above the session VWAP
  (`app/market_data/vwap.py`) -- the standard day-trading reference for
  "buyers are still in control." Long side only on purpose: a green-candle-
  and-above-VWAP requirement doesn't have a sign-flipped short-side
  equivalent, so downward moves aren't alerted at all. Unlike a chart
  indicator (which only ever watches whatever symbol's chart happens to be
  open), this watches every ranked view continuously. Toggle in the header
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
  live ranking functions (`engine._rank_gainers`/`_rank_losers`, not a
  reimplementation) to reconstruct what would have ranked as a gainer/
  loser on past trading days -- a much bigger sample than however many
  days of live history have accumulated so far. Both share
  `app/scanners/bucket_analysis.py`'s bucketing so a live check and a
  historical one measure things identically. Also breaks down win rate by
  `is_shaved_top` (did the entry day's own candle close at/near its high?
  -- wick-shape analysis works on any OHLC bar, so this doesn't need
  minute data). The backtest is daily-bar-only: no catalyst backtesting
  (needs historical news, unbuilt), no minute-resolution signals (time-of-
  day RVOL, the momentum alarm itself -- 15m % is inherently a minute-
  resolution concept), and it applies today's universe membership across
  the whole lookback window (survivorship bias) -- all stated up front in
  its own report output.
- **Analytics app** (`/analytics`, Plotly Dash): a resizable 4-panel scanner
  heatmap + table + symbol detail + AI trade ideas view, plus separate pages
  for the scanner benchmark, scanner match history, cross-symbol
  correlation/comparison, and seasonality.
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
- **Float/market cap/short interest**: only fetched for symbols currently in
  a ranked scanner view (not the whole universe), so it's absent until a
  symbol actually appears there. Short interest is FINRA's own biweekly
  bulk file, which in practice runs noticeably behind FINRA's advertised
  publish schedule -- expect it to reflect a settlement date roughly 2-4
  weeks old, not this week's.
- **Multi-widget draggable grid (React app), watchlists**: roadmap items,
  not built yet -- panels resize (drag the splitters) but aren't
  freely repositionable.
- **Momentum alarm**: React app only, not in the Dash analytics app (the
  center-overlay/toggle interaction pattern doesn't translate to Dash's
  page-reload-driven callbacks the same way; the Dash Backtest page does
  cover the underlying alert condition's historical win rate, see below).
  Both scanner tables do show the underlying ⚡ MOMENTUM badge regardless.
  The 5% threshold and 10% shaved-top wick tolerance are unvalidated
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
  tool can't validate it either; only live/intraday history can.
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
