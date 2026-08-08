"""Live backend state for Dash callbacks.

Dash builds its app/layout/callbacks at import time, but the real backend
objects (scanner engine, Alpaca clients, universe) only exist once
main.py's lifespan() startup has run. Callbacks and page layout() functions
read from this module-level singleton at call time -- strictly after
bind() -- instead of capturing state at import time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

    from app.alpaca.client import AlpacaClients
    from app.alpaca.universe import UniverseSymbol
    from app.core.config import Settings
    from app.scanners.engine import ScannerEngine


class BackendState:
    def __init__(self) -> None:
        self.alpaca_clients: "AlpacaClients | None" = None
        self.scanner_engine: "ScannerEngine | None" = None
        self.settings: "Settings | None" = None
        self.universe: "dict[str, UniverseSymbol] | None" = None


backend_state = BackendState()


def bind(app: "FastAPI") -> None:
    """Called once, at the end of lifespan() startup, after app.state is
    fully populated. ScannerEngine mutates its own attributes in place
    rather than being replaced, so binding the reference once is enough --
    no re-binding needed on later poll cycles.
    """
    backend_state.alpaca_clients = app.state.alpaca_clients
    backend_state.scanner_engine = app.state.scanner_engine
    backend_state.settings = app.state.settings
    backend_state.universe = app.state.universe
