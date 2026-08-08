import asyncio
import logging
from contextlib import asynccontextmanager

import anthropic
from a2wsgi import WSGIMiddleware
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.ai.trade_idea_tracker import TradeIdeaTracker
from app.alpaca.client import AlpacaClients
from app.alpaca.universe import build_universe
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.dash_app import dash_app
from app.dash_app.state import bind as bind_dash_state
from app.market_data.stream_manager import StreamManager
from app.routers import meta, scanners, symbols, trade_ideas
from app.scanners.engine import ScannerEngine
from app.ws import chart_ws, scanner_ws
from app.ws.connection_manager import ConnectionManager

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    settings = get_settings()
    app.state.settings = settings

    clients = AlpacaClients(settings)
    app.state.alpaca_clients = clients

    manager = ConnectionManager()
    app.state.connection_manager = manager
    app.state.stream_manager = StreamManager(clients, manager, settings.max_stream_symbols)

    app.state.anthropic_client = (
        anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        if settings.has_anthropic_credentials
        else None
    )
    app.state.trade_idea_tracker = TradeIdeaTracker()

    if settings.has_credentials:
        try:
            universe = await build_universe(clients, settings)
        except Exception:
            logger.exception("Failed to build universe at startup -- scanners will stay empty")
            universe = {}
    else:
        logger.warning(
            "No Alpaca credentials configured. Copy backend/.env.example to backend/.env "
            "and fill in ALPACA_API_KEY_ID/ALPACA_API_SECRET_KEY to get live data."
        )
        universe = {}
    app.state.universe = universe

    engine = ScannerEngine(clients, settings, universe, manager)
    app.state.scanner_engine = engine
    bind_dash_state(app)

    try:
        # So a same-day restart (or a first-ever start after the open)
        # still gets a real "premarket gainers" snapshot instead of it
        # silently mirroring "gainers" until tomorrow's market open.
        await engine.backfill_premarket_snapshot()
    except Exception:
        logger.exception("Premarket backfill failed -- premarket_gainers will mirror gainers")

    try:
        await engine.backfill_latest_session_gainers()
    except Exception:
        logger.exception("Latest-session gainers backfill failed -- scanner may show empty when closed")

    scanner_task = asyncio.create_task(engine.run_loop())

    try:
        yield
    finally:
        scanner_task.cancel()
        await clients.stop_stream()


app = FastAPI(title="Trading Dashboard API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(meta.router)
app.include_router(scanners.router)
app.include_router(symbols.router)
app.include_router(trade_ideas.router)
app.include_router(scanner_ws.router)
app.include_router(chart_ws.router)

app.mount("/analytics", WSGIMiddleware(dash_app.server))
