"""Listing and switching strategy signals from the dashboard.

The switch is loader-level (see app.strategies.switches), so flipping one
here changes what the scanner marks on its next poll tick, what the chart's
strategy lines draw on the next /bars request, and what a full backtest run
includes -- all three read load_strategies, and none holds strategies across
calls.

Both endpoints return the same full listing, so the panel can render
whatever it last heard without a second fetch. Load errors ride along for
the same reason load_strategies returns them: a strategy that failed to
load looks exactly like a quiet market, and the panel is a natural place to
say "1 failed" out loud.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.strategies import loader, switches

router = APIRouter(prefix="/api/strategies", tags=["strategies"])


class SwitchRequest(BaseModel):
    enabled: bool


def _listing() -> dict:
    states, errors = loader.inventory()
    return {
        "strategies": [
            {"name": s.name, "filename": s.filename, "stem": s.stem, "enabled": s.enabled}
            for s in states
        ],
        "errors": [{"filename": e.filename, "error": e.error} for e in errors],
    }


@router.get("")
async def list_strategies() -> dict:
    return _listing()


@router.post("/{stem}")
async def switch_strategy(stem: str, body: SwitchRequest) -> dict:
    states, _ = loader.inventory()
    if stem not in {s.stem for s in states}:
        # 404 rather than persisting an orphan key: a typo'd or deleted
        # strategy switched off in the JSON would silently park the next
        # file that happens to take that name.
        raise HTTPException(status_code=404, detail=f"No strategy named {stem!r}.")
    switches.set_switched(stem, body.enabled)
    return _listing()
