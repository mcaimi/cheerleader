"""GUI preferences store (recent files + zoom); no display needed."""

from __future__ import annotations

import json
import os

from cheerleader.gui.const import RECENT_MAX
from cheerleader.gui.settings import Settings, settings_path


def test_defaults_when_missing() -> None:
    s = Settings.load()
    assert s.recent == [] and s.zoom == 1.0


def test_round_trip_and_dedup() -> None:
    s = Settings.load()
    s.add_recent("/a")
    s.add_recent("/b")
    s.add_recent("/a")  # moves to the front, no duplicate
    s.zoom = 1.4
    s.save()
    loaded = Settings.load()
    assert loaded.recent == ["/a", "/b"]
    assert loaded.zoom == 1.4


def test_recent_is_capped() -> None:
    s = Settings.load()
    for i in range(RECENT_MAX + 5):
        s.add_recent(f"/f{i}")
    assert len(Settings.load().recent) == RECENT_MAX


def test_corrupt_file_yields_defaults() -> None:
    os.makedirs(os.path.dirname(settings_path()), exist_ok=True)
    with open(settings_path(), "w") as fh:
        fh.write("{not json")
    assert Settings.load() == Settings()
    with open(settings_path(), "w") as fh:
        json.dump({"recent": [1, "/ok"], "zoom": "big"}, fh)
    s = Settings.load()
    assert s.recent == ["/ok"] and s.zoom == 1.0
