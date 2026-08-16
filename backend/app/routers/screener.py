"""Screener API -- shared by both front ends.

The React app and the Dash app both build their filter controls and column
pickers from GET /fields rather than listing columns themselves, so a field
added to app.scanners.screener.FIELDS shows up in both with no UI change.
That is the whole reason the registry lives on the server.

The actual screening is delegated to app.scanners.screener_service, which
the Dash page calls directly in-process -- so both surfaces run identical
logic rather than one reimplementing the other.
"""

from fastapi import APIRouter, Request

from app.scanners import screener
from app.scanners.screener_service import screen_live_rows

router = APIRouter(prefix="/api/screener", tags=["screener"])


@router.get("/fields")
async def get_fields() -> dict:
    """The screenable field registry: name, label, type and the operators
    valid for that type. Clients render their UI from this.
    """
    return {"fields": screener.fields_payload()}


@router.get("/presets")
async def get_presets() -> dict:
    """Built-in screens, including the three that used to be hardcoded
    views. Returned as full filter specs so a UI can load one and then let
    the user edit it, rather than treating them as opaque names.
    """
    return {"presets": screener.preset_list()}


@router.post("/run")
async def run_screen(screen: screener.Screen, request: Request) -> dict:
    """Run a screen against the current live rows."""
    return screen_live_rows(
        request.app.state.scanner_engine, request.app.state.settings, screen
    )
