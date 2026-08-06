#!/usr/bin/env python
"""
Resolved agent configuration: ``.env`` overrides defaults from ``src.config``.

Load order: ``python-dotenv`` reads the project ``.env`` into ``os.environ``;
each setting prefers a non-empty environment value, otherwise the value from
``src.config``.
"""

import os
from pathlib import Path
from typing import Any

# import required modules
try:
    from dotenv import load_dotenv
    from cheerleader.util.utils import _str_setting
except ImportError:
    raise ImportError("python-dotenv is required to use this module")

# load the environment variables
def load_environment_variables(envfile: str) -> dict[str, Any]:
    e_file = Path(envfile)
    if not e_file.exists():
        raise FileNotFoundError(f"Environment file {e_file} not found")
    load_dotenv(e_file)  # load the environment variables
    return _get_settings_from_environment()


# get the settings
def _get_settings_from_environment():
    settings = {
        "provider": _str_setting("LLM_PROVIDER", "openai"),
        "llm_model": _str_setting("LLM_MODEL", "gpt-4o-mini"),
        "base_url": _str_setting("BASE_URL", "https://api.openai.com/v1"),
        "api_key": _str_setting("API_KEY", "your-api-key-here"),
        "skills_path": _str_setting("SKILLS_PATH", "./skills"),
        "system_prompt": _str_setting("SYSTEM_PROMPT", "you are an expert in reverse engineering"),
    }
    return settings


# export the settings
__all__ = [
    "load_environment_variables",
]
