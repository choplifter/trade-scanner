import asyncio
import logging
from contextlib import asynccontextmanager

import anthropic
import httpx
from a2wsgi import WSGIMiddleware
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.ai.trade_idea_tracker import TradeIdeaTracker
from app.alpaca.client import AlpacaClients
from app.alpaca.universe import build_universe, list_active_equity_symbols
from app.auth.dependency import get_current_user
from app.auth.store import UserStore
from app.broker.crypto import secret_box_from_settings
from app.broker.resolver import BrokerResolver
from app.broker.store import BrokerStore
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.dash_app import dash_app
from app.dash_app.state import bind as bind_dash_state
from app.fundamentals.cache import FundamentalsCache
from app.market_data.news_cache import NewsCache
from app.market_data.news_feed import NewsFeedTracker
from app.market_data.news_stream import NewsStreamManager
from app.market_data.option_stream_manager import OptionStreamManager
from app.market_data.stream_manager import StreamManager
from app.replay.engine import ReplayEngineCache
from app.replay.options_engine import ReplayOptionsEngineCache
from app.replay.loop import run_replay_pacing_loop
from app.replay.store import ReplayStore
from app.routers import (
    admin,
    auth,
    broker,
    meta,
    news_feed,
    replay,
    scanners,
    screener,
    strategies,
    symbols,
    trade_ideas,
    trading,
    trading_options,
    trading_sim,
    trading_sim_options,
    watchlist,
)
from app.scanners.benchmark_tracker import ScannerBenchmarkTracker
from app.scanners.engine import ScannerEngine
from app.scanners.history_store import ScannerHistoryStore
from app.scanners.momentum_cache import MomentumCache
from app.options.chain_fetch import ChainCache
from app.options.monitor import run_options_trigger_loop
from app.options.trigger_store import TriggerStore
from app.trading.journal_store import JournalStore
from app.trading.sim.loop import run_sim_fill_loop
from app.trading.sim.options_service import OptionsWiring
from app.trading.sim.options_store import SimOptionsStore
from app.trading.sim.store import SimStore
from app.trading.trade_store import TradeStore
from app.watchlist.store import WatchlistStore
from app.ws import chart_ws, news_feed_ws, replay_ws, scanner_ws
from app.ws.connection_manager import ConnectionManager
from app.ws.screen_subscriptions import ScreenSubscriptions

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    settings = get_settings()
    if not settings.session_secret_key:
        raise RuntimeError(
            "SESSION_SECRET_KEY is not set. Generate one with "
            "`python -c \"import secrets; print(secrets.token_hex(32))\"` and set it in backend/.env."
        )
    app.state.settings = settings

    user_store = UserStore(settings.scanner_history_db_path)
    await user_store.init_schema()
    app.state.user_store = user_store

    # Per-user Alpaca key pairs (app.broker): secrets encrypted under the
    # session secret (or BROKER_ENCRYPTION_KEY); the resolver decides which
    # TradingClient a request gets -- the user's own, or .env for the admin.
    broker_store = BrokerStore(settings.scanner_history_db_path, secret_box_from_settings(settings))
    await broker_store.init_schema()
    app.state.broker_store = broker_store
    app.state.broker_resolver = BrokerResolver(settings, broker_store)

    clients = AlpacaClients(settings)
    app.state.alpaca_clients = clients

    manager = ConnectionManager()
    app.state.connection_manager = manager
    app.state.stream_manager = StreamManager(clients, manager, settings.max_stream_symbols)
    app.state.option_stream_manager = OptionStreamManager(clients, manager, settings.max_stream_symbols)
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

    # Closed round trips with realized P&L. Same file as the scanner
    # history -- one thing to back up -- and the record that survives a
    # paper-account reset. See app.trading.trade_store.
    trade_store = TradeStore(settings.scanner_history_db_path)
    await trade_store.init_schema()
    app.state.trade_store = trade_store

    # Simulation Mode's local order book -- same file, its own tables (see
    # app.trading.sim.store). Independent of trading_enabled/alpaca_paper:
    # it never touches clients.trading, so it's ready regardless of those.
    sim_store = SimStore(settings.scanner_history_db_path)
    await sim_store.init_schema()
    app.state.sim_store = sim_store
    # The simulated options book: packages and contracts, same file (see
    # app.trading.sim.options_store). Prices come from Alpaca's data API
    # live, or from the replayed moment in a history replay.
    sim_options_store = SimOptionsStore(settings.scanner_history_db_path)
    await sim_options_store.init_schema()
    app.state.sim_options_store = sim_options_store

    # History replay: an optional clock layered on top of Simulation Mode
    # (see app.replay's module docstrings). Session metadata is durable
    # (same db file, its own table); the fetched bars behind a resident
    # ReplayEngine are not -- see app.replay.engine's module docstring.
    replay_store = ReplayStore(settings.scanner_history_db_path)
    await replay_store.init_schema()
    app.state.replay_store = replay_store
    app.state.replay_engines = ReplayEngineCache()
    app.state.replay_option_engines = ReplayOptionsEngineCache()

    # Per-user watchlist -- was localStorage-only before real users existed.
    watchlist_store = WatchlistStore(settings.scanner_history_db_path)
    await watchlist_store.init_schema()
    app.state.watchlist_store = watchlist_store

    # Per-user notes on closed trades -- same file, own table. See
    # app.trading.journal_store; trade_id alone (real or Simulation Mode) is
    # the join key, so this store is independent of trade_store/sim_store.
    journal_store = JournalStore(settings.scanner_history_db_path)
    await journal_store.init_schema()
    app.state.journal_store = journal_store

    fundamentals_client = httpx.AsyncClient(timeout=10.0)
    fundamentals = FundamentalsCache(settings, fundamentals_client)
    app.state.fundamentals = fundamentals

    # Shares the fundamentals client: both talk to FMP with the same key and
    # the same timeout, and its lifetime already spans the app's.
    news_cache = NewsCache(settings, clients, fundamentals_client)
    app.state.news_cache = news_cache

    momentum_cache = MomentumCache(settings, clients)
    app.state.momentum_cache = momentum_cache

    news_feed_tracker = NewsFeedTracker(ring_buffer_size=settings.news_feed_ring_buffer_size)
    app.state.news_feed_tracker = news_feed_tracker

    if settings.has_credentials:
        try:
            universe = await build_universe(clients, settings)
        except Exception:
            logger.exception("Failed to build universe at startup -- scanners will stay empty")
            universe = {}
        try:
            all_symbols = await list_active_equity_symbols(clients)
        except Exception:
            logger.exception(
                "Failed to fetch the full symbol list -- watchlist autocomplete will "
                "fall back to the (narrower) scanner universe"
            )
            all_symbols = []
    else:
        logger.warning(
            "No Alpaca credentials configured. Copy backend/.env.example to backend/.env "
            "and fill in ALPACA_API_KEY_ID/ALPACA_API_SECRET_KEY to get live data."
        )
        universe = {}
        all_symbols = []
    app.state.universe = universe
    app.state.all_symbols = all_symbols

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
        news_feed_tracker,
    )
    engine.screen_subscriptions = screen_subscriptions
    app.state.scanner_engine = engine
    bind_dash_state(app)

    # Options: one chain cache for every widget (market data, so it is
    # account-agnostic) and the persisted underlying-price triggers the
    # loop below watches. Spot prices come from the same lookup the
    # equity ticket uses (engine row first, then Alpaca).
    from app.options.service import OptionsService

    app.state.options_chain_cache = ChainCache(
        clients, OptionsService(clients, settings, engine=engine).spot
    )
    options_trigger_store = TriggerStore(settings.scanner_history_db_path)
    await options_trigger_store.init_schema()
    app.state.options_trigger_store = options_trigger_store
    # What the sim fill loop and the replay pacing loop need to run the
    # simulated options book for a user (see app.trading.sim.options_service).
    options_wiring = OptionsWiring(
        options_store=sim_options_store,
        trigger_store=options_trigger_store,
        option_engines=app.state.replay_option_engines,
        chain_cache=app.state.options_chain_cache,
        scanner_engine=engine,
    )
    app.state.options_wiring = options_wiring

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

    # The market-wide news websocket (see app.market_data.news_stream);
    # the engine's once-a-minute poll remains the backstop.
    news_stream = NewsStreamManager(settings, news_feed_tracker, manager)
    app.state.news_stream = news_stream
    if settings.has_credentials and settings.news_stream_enabled:
        news_stream.start()

    scanner_task = asyncio.create_task(engine.run_loop())
    sim_fill_task = asyncio.create_task(
        run_sim_fill_loop(clients, settings, sim_store, replay_store=replay_store, wiring=options_wiring)
    )
    options_trigger_task = asyncio.create_task(
        run_options_trigger_loop(
            clients, settings, options_trigger_store, app.state.options_chain_cache, engine,
            resolver=app.state.broker_resolver, user_store=user_store,
        )
    )
    replay_task = asyncio.create_task(
        run_replay_pacing_loop(
            replay_store, sim_store, app.state.replay_engines, manager, clients, settings, options_wiring
        )
    )

    try:
        yield
    finally:
        scanner_task.cancel()
        sim_fill_task.cancel()
        options_trigger_task.cancel()
        replay_task.cancel()
        await news_stream.stop()
        await clients.stop_stream()
        await clients.stop_option_stream()
        await fundamentals.aclose()


app = FastAPI(title="Trading Dashboard API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Signed-cookie sessions -- no server-side session store needed. Backs
# get_current_user (app.auth.dependency) below, which gates every router
# except auth.router itself.
app.add_middleware(SessionMiddleware, secret_key=get_settings().session_secret_key)

# Unauthenticated: login has to be reachable before there's a session.
app.include_router(auth.router)

# Everything else requires a logged-in session -- a small trusted group, but
# nobody unauthenticated should see even read-only data (the real account's
# positions included). trading_sim/watchlist need the user's own id inside
# their handlers (not just a gate), so they take Depends(get_current_user)
# per-endpoint instead of here -- see those routers.
_auth_gate = [Depends(get_current_user)]
app.include_router(meta.router, dependencies=_auth_gate)
app.include_router(scanners.router, dependencies=_auth_gate)
app.include_router(screener.router, dependencies=_auth_gate)
app.include_router(strategies.router, dependencies=_auth_gate)
app.include_router(symbols.router, dependencies=_auth_gate)
app.include_router(trade_ideas.router, dependencies=_auth_gate)
# The trading router twice: the paper account, and the real one under /live
# with request.state.trading_account set so every handler knows. What may be
# *written* to the live account is decided in app.trading.guards, not here.
app.include_router(trading.router, prefix="/api/trading", dependencies=_auth_gate)
app.include_router(
    trading.router,
    prefix="/api/trading/live",
    dependencies=[*_auth_gate, Depends(trading.mark_live_account)],
)
app.include_router(trading_options.router, prefix="/api/trading/options", dependencies=_auth_gate)
app.include_router(
    trading_options.router,
    prefix="/api/trading/live/options",
    dependencies=[*_auth_gate, Depends(trading.mark_live_account)],
)
app.include_router(broker.router)
app.include_router(admin.router)
app.include_router(trading_sim.router)
app.include_router(trading_sim_options.router)
app.include_router(replay.router)
app.include_router(news_feed.router)
app.include_router(watchlist.router)
app.include_router(scanner_ws.router)
app.include_router(chart_ws.router)
app.include_router(replay_ws.router)
app.include_router(news_feed_ws.router)

app.mount("/analytics", WSGIMiddleware(dash_app.server))
