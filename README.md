# Stocks in Play — Trading Dashboard

A personal, locally-run "stocks in play" scanner + chart dashboard in the
spirit of trade-ideas.com, bearbulltrader.com, and warriortrading.com: live
gainers / premarket gainers / losers / most-active scanners, a
click-to-chart candlestick widget with a session-anchored VWAP overlay and
company info/news, AI-generated trade-idea annotations, a scanner-wide
benchmark against SPY, and a Plotly Dash analytics app — powered by Alpaca
Markets' real-time IEX data feed, with float/market cap/short interest/
company info layered in from Financial Modeling Prep and FINRA.

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
  FINRA itself.

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
  from the last session isn't invisible over a weekend/holiday.
- Scanner columns: symbol (click to copy its unambiguous `EXCHANGE:SYMBOL`
  TradingView format), company name, last price, gap %, volume, RVOL, and
  (when `FMP_API_KEY` is set) float, market cap, short interest % of float,
  exchange, and country.
- One chart widget: click any scanner row to load that symbol — candlestick
  chart, volume pane, and a session-anchored VWAP line (resets at 09:30 ET),
  fed by Alpaca's live minute-bar stream, plus a company info + recent news
  panel (name/sector/industry/description from FMP, headlines from Alpaca's
  news feed).
- **AI Trade Ideas** (needs `ANTHROPIC_API_KEY`): Claude ranks the 3 most
  notable current setups from gap %, RVOL, dollar volume, HOD status, news
  catalyst, VWAP position, 15-minute momentum, spread, multi-day context,
  float, and short interest — framed as descriptive scanner annotation, not
  investment advice. A past-picks performance table tracks how prior AI
  picks have actually moved since they were generated.
- **Scanner benchmark**: every symbol the scanner itself has ever flagged
  (gainers/losers/most active — not just the 3 AI picks above) gets logged
  the moment it first appears, then tracked against SPY from that instant —
  the real check on whether the scanner's own criteria (gap %, RVOL, the
  movers backstop) find stocks that keep moving, not just noise.
- **Analytics app** (`/analytics`, Plotly Dash): a resizable 4-panel scanner
  heatmap + table + symbol detail + AI trade ideas view, plus separate pages
  for the scanner benchmark, cross-symbol correlation/comparison, and
  seasonality.
- Session badge (Premarket / Market Open / After Hours / Closed) in the
  header, computed from the NYSE trading calendar.

## Known limitations

- **Feed**: uses Alpaca's free real-time **IEX** feed, which is a
  single-exchange view, not the consolidated SIP tape. Gap %/volume numbers
  will be directionally right but won't exactly match SIP-based tools like
  Trade-Ideas. Upgrading later is a one-line change: set `ALPACA_DATA_FEED=sip`
  in `backend/.env` once you have a paid Alpaca market-data subscription.
- **Dedicated RVOL scanner / new highs-lows**: not built -- RVOL is shown
  as a column on every scanner, not a ranking of its own.
- **Float/market cap/short interest**: only fetched for symbols currently in
  a ranked scanner view (not the whole universe), so it's absent until a
  symbol actually appears there. Short interest is FINRA's own biweekly
  bulk file, which in practice runs noticeably behind FINRA's advertised
  publish schedule -- expect it to reflect a settlement date roughly 2-4
  weeks old, not this week's.
- **EMA 9/20, multi-widget draggable grid (React app), watchlists, alerts**:
  roadmap items, not built yet.
- **Scanner benchmark**: entries are still recorded from closed-market
  fallback data, but the actual price/SPY comparison columns only populate
  once live polling resumes (they need a live SPY price and a live price
  for the flagged symbol) -- expect "—" for those specifically outside
  market hours, same as float/market cap. The log itself isn't persisted
  across backend restarts, same as the AI trade-ideas performance table.
- Outside premarket/regular market hours, scanners fall back to the most
  recently completed session's real data instead of polling live (labeled
  "LAST SESSION" in the UI) -- they only show empty rows if `backend/.env`
  doesn't have valid Alpaca credentials.

## Project layout

```
backend/   FastAPI app: Alpaca integration, scanner engine, VWAP, WebSockets,
           fundamentals (FMP + FINRA), AI trade ideas, Plotly Dash analytics app
frontend/  Vite + React + TypeScript dashboard, lightweight-charts for candles
```

See `backend/app/` and `frontend/src/` for the module breakdown — each file
has a short docstring/comment explaining its role.
