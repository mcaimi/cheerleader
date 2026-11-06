"""Shared test configuration."""

import pytest


@pytest.fixture(autouse=True)
def _isolated_gui_settings(tmp_path, monkeypatch):
    """Keep GUI preferences (recent files, zoom) out of the real home dir."""
    monkeypatch.setenv("CHEERLEADER_CONFIG_DIR", str(tmp_path / "config"))
