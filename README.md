# Stocks in Play — Trading Dashboard

A personal, locally-run "stocks in play" scanner + chart dashboard in the
spirit of trade-ideas.com, bearbulltrader.com, and warriortrading.com: live
market-gainer / premarket-gainer scanners, and a click-to-chart candlestick
widget with a session-anchored VWAP overlay, powered by Alpaca Markets'
real-time IEX data feed.

## 1. Get Alpaca API credentials (required for live data)

1. Sign up free at https://alpaca.markets and create a **paper trading**
   account (no funding required — you only need it for API access, not to
   place trades).
2. In the Alpaca dashboard, generate an **API Key ID** and **Secret Key**.
3. Copy `backend/.env.example` to `backend/.env` and fill in
   `ALPACA_API_KEY_ID` / `ALPACA_API_SECRET_KEY`.

Without valid credentials the app still starts, but the universe stays empty
and scanners will show no rows.

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

## 3. Run the frontend

```
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. The dev server proxies `/api` and `/ws` to the
backend on port 8000, so both must be running.

## What v1 ships

- Two live scanners: **Market Gainers** and **Premarket Gainers**, both
  ranked by % change from prior close, polled from Alpaca snapshots every
  5s (regular hours) / 10s (premarket) and pushed over WebSocket.
- One chart widget: click any scanner row to load that symbol — candlestick
  chart, volume pane, and a session-anchored VWAP line (resets at 09:30 ET),
  fed by Alpaca's live minute-bar stream.
- Session badge (Premarket / Market Open / After Hours / Closed) in the
  header, computed from the NYSE trading calendar.

## Known v1 limitations

- **Feed**: uses Alpaca's free real-time **IEX** feed, which is a
  single-exchange view, not the consolidated SIP tape. Gap %/volume numbers
  will be directionally right but won't exactly match SIP-based tools like
  Trade-Ideas. Upgrading later is a one-line change: set `ALPACA_DATA_FEED=sip`
  in `backend/.env` once you have a paid Alpaca market-data subscription.
- **RVOL / losers / gap scanners / new highs-lows**: not built yet — v1 is
  intentionally just the two gainer scanners to prove out the full
  Alpaca → FastAPI → WebSocket → React → chart pipeline end to end.
- **EMA 9/20, multi-widget draggable grid, watchlists, alerts**: roadmap, see
  the "Roadmap" section of the implementation plan this was built from
  (`~/.claude/plans/encapsulated-sprouting-willow.md` on the machine this was
  built on).
- Scanners will show empty rows outside premarket/regular market hours (the
  scanner loop idles when the market is closed) and always show empty rows
  if `backend/.env` doesn't have valid credentials.

## Project layout

```
backend/   FastAPI app: Alpaca integration, scanner engine, VWAP, WebSockets
frontend/  Vite + React + TypeScript dashboard, lightweight-charts for candles
```

See `backend/app/` and `frontend/src/` for the module breakdown — each file
has a short docstring/comment explaining its role.
