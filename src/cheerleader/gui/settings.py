"""
Persistent GUI preferences: recent files and zoom.

Stored as JSON in ``$CHEERLEADER_CONFIG_DIR`` (tests point it at a temp
dir), else ``$XDG_CONFIG_HOME/cheerleader``, else ``~/.config/cheerleader``.
Best effort throughout: a missing, unreadable or corrupt file yields the
defaults, and a failed save is silently skipped — preferences must never
stop the app.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from cheerleader.gui.const import CONFIG_DIR_ENV, RECENT_MAX


def config_dir() -> str:
    override = os.environ.get(CONFIG_DIR_ENV)
    if override:
        return override
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "cheerleader")


def settings_path() -> str:
    return os.path.join(config_dir(), "gui.json")


@dataclass
class Settings:
    recent: list[str] = field(default_factory=list)  # most recent first
    zoom: float = 1.0

    @classmethod
    def load(cls) -> Settings:
        try:
            with open(settings_path(), encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        recent = [p for p in data.get("recent", []) if isinstance(p, str)]
        zoom = data.get("zoom", 1.0)
        return cls(
            recent=recent[:RECENT_MAX],
            zoom=float(zoom) if isinstance(zoom, (int, float)) else 1.0,
        )

    def save(self) -> None:
        path = settings_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = f"{path}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"recent": self.recent, "zoom": self.zoom}, fh, indent=2)
            os.replace(tmp, path)
        except OSError:
            pass

    def add_recent(self, path: str) -> None:
        """Move *path* to the front of the recent list (deduplicated)."""
        self.recent = [path, *(p for p in self.recent if p != path)][:RECENT_MAX]
        self.save()

    def remove_recent(self, path: str) -> None:
        self.recent = [p for p in self.recent if p != path]
        self.save()

    def clear_recent(self) -> None:
        self.recent = []
        self.save()
