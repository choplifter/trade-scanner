"""_stepped_as_of is the manual counterpart to one pacing-loop tick (see
BAR_STEP in app.replay.loop) -- these pin its arithmetic and clamping
directly, without standing up the FastAPI app/DB fixtures the /step
endpoint itself needs.
"""

from datetime import datetime, timedelta, timezone

from app.replay.loop import BAR_STEP
from app.routers.replay import _stepped_as_of


def test_forward_advances_by_one_bar_step():
    as_of = datetime(2026, 8, 15, 14, 0, tzinfo=timezone.utc)
    assert _stepped_as_of(as_of, "forward", None, None) == as_of + BAR_STEP


def test_backward_retreats_by_one_bar_step():
    as_of = datetime(2026, 8, 15, 14, 0, tzinfo=timezone.utc)
    assert _stepped_as_of(as_of, "backward", None, None) == as_of - BAR_STEP


def test_forward_clamps_at_end():
    as_of = datetime(2026, 8, 15, 14, 0, tzinfo=timezone.utc)
    end = as_of + timedelta(minutes=2)  # closer than one full BAR_STEP away
    assert _stepped_as_of(as_of, "forward", None, end) == end


def test_backward_clamps_at_start():
    as_of = datetime(2026, 8, 15, 14, 0, tzinfo=timezone.utc)
    start = as_of - timedelta(minutes=2)
    assert _stepped_as_of(as_of, "backward", start, None) == start


def test_already_at_end_stays_put_on_forward_step():
    as_of = datetime(2026, 8, 15, 14, 0, tzinfo=timezone.utc)
    assert _stepped_as_of(as_of, "forward", None, as_of) == as_of


def test_already_at_start_stays_put_on_backward_step():
    as_of = datetime(2026, 8, 15, 14, 0, tzinfo=timezone.utc)
    assert _stepped_as_of(as_of, "backward", as_of, None) == as_of


def test_no_engine_resident_steps_unclamped():
    """start/end of None (engine not resident) must not raise, and applies
    the delta as-is."""
    as_of = datetime(2026, 8, 15, 14, 0, tzinfo=timezone.utc)
    assert _stepped_as_of(as_of, "forward", None, None) == as_of + BAR_STEP
    assert _stepped_as_of(as_of, "backward", None, None) == as_of - BAR_STEP
