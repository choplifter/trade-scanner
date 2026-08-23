"""Shared fixtures.

The strategy switch file lives in the backend working directory -- the same
place pytest runs from -- so without isolation every test would read
whatever the developer last toggled from the dashboard, and the loader's
real-directory tests would pass or fail with local UI state. Every test gets
a scratch path instead; tests that care about switching write through the
module's own functions.
"""

import pytest

from app.strategies import switches


@pytest.fixture(autouse=True)
def _isolated_strategy_switches(tmp_path, monkeypatch):
    monkeypatch.setattr(switches, "_PATH", tmp_path / "strategy_switches.json")
