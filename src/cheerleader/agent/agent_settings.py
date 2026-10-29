#!/usr/bin/env python
"""
Resolved agent configuration: ``.env`` overrides defaults from ``src.config``.

Load order: ``python-dotenv`` reads the project ``.env`` into ``os.environ``;
each setting prefers a non-empty environment value, otherwise the value from
``src.config``.

If the env file does not exist (e.g. standalone builds where the AI agent is
unused), a warning is logged and settings are resolved from the process
environment and built-in defaults instead of failing.
"""

import logging
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
        # AI is optional: don't hard-fail when the env file is absent.
        # Settings fall back to the process environment / defaults below.
        logging.getLogger(__name__).warning(
            "Environment file %s not found — using process environment and "
            "defaults; copy env.template to .env to configure the AI agent.",
            e_file,
        )
        return _get_settings_from_environment()
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
        "system_prompt": _str_setting(
            "SYSTEM_PROMPT", "you are an expert in reverse engineering"
        ),
    }
    return settings


# export the settings
__all__ = [
    "load_environment_variables",
]
