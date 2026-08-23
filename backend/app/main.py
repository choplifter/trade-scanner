import asyncio
import logging
from contextlib import asynccontextmanager

import anthropic
import httpx
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
from app.fundamentals.cache import FundamentalsCache
from app.market_data.news_cache import NewsCache
from app.market_data.stream_manager import StreamManager
from app.routers import meta, scanners, screener, strategies, symbols, trade_ideas, trading
from app.scanners.benchmark_tracker import ScannerBenchmarkTracker
from app.scanners.engine import ScannerEngine
from app.scanners.history_store import ScannerHistoryStore
from app.scanners.momentum_cache import MomentumCache
from app.ws import chart_ws, scanner_ws
from app.ws.connection_manager import ConnectionManager
from app.ws.screen_subscriptions import ScreenSubscriptions

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
    # Live user-defined screens. Separate from ConnectionManager because a
    # screen's result is per-connection, not shareable across a topic --
    # see app.ws.screen_subscriptions.
    screen_subscriptions = ScreenSubscriptions()
    app.state.screen_subscriptions = screen_subscriptions

    app.state.anthropic_client = (
        anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        if settings.has_anthropic_credentials
        else None
    )
    app.state.trade_idea_tracker = TradeIdeaTracker()
    app.state.scanner_benchmark_tracker = ScannerBenchmarkTracker()

    scanner_history_store = ScannerHistoryStore(settings.scanner_history_db_path)
    await scanner_history_store.init_schema()
    app.state.scanner_history_store = scanner_history_store

    fundamentals_client = httpx.AsyncClient(timeout=10.0)
    fundamentals = FundamentalsCache(settings, fundamentals_client)
    app.state.fundamentals = fundamentals

    # Shares the fundamentals client: both talk to FMP with the same key and
    # the same timeout, and its lifetime already spans the app's.
    news_cache = NewsCache(settings, clients, fundamentals_client)
    app.state.news_cache = news_cache

    momentum_cache = MomentumCache(settings, clients)
    app.state.momentum_cache = momentum_cache

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

    engine = ScannerEngine(
        clients,
        settings,
        universe,
        manager,
        fundamentals,
        app.state.scanner_benchmark_tracker,
        scanner_history_store,
        news_cache,
        momentum_cache,
        fundamentals_client,
    )
    engine.screen_subscriptions = screen_subscriptions
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
        await engine.backfill_latest_session_rows()
    except Exception:
        logger.exception("Latest-session rows backfill failed -- scanners may show empty when closed")

    scanner_task = asyncio.create_task(engine.run_loop())

    try:
        yield
    finally:
        scanner_task.cancel()
        await clients.stop_stream()
        await fundamentals.aclose()


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
app.include_router(screener.router)
app.include_router(strategies.router)
app.include_router(symbols.router)
app.include_router(trade_ideas.router)
app.include_router(trading.router)
app.include_router(scanner_ws.router)
app.include_router(chart_ws.router)

app.mount("/analytics", WSGIMiddleware(dash_app.server))
