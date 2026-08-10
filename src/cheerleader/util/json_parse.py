#!/usr/bin/env python
"""JSON parsing helpers for environment variables."""

import json
import os
from typing import Any, cast

# parse a JSON object from an environment variable
def _parse_json_object_env(var_name: str) -> dict[str, Any]:
    raw_env = os.getenv(var_name)
    if not raw_env or not raw_env.strip():
        return {}
    try:
        out = json.loads(raw_env)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in {var_name}: {e}") from e
    if not isinstance(out, dict):
        raise ValueError(f"{var_name} must be a JSON object, got {type(out).__name__}")
    return cast(dict[str, Any], out)

# parse a JSON object from a string
def _parse_json_object_str(json_str: str) -> dict[str, Any]:
    try:
        out = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}") from e
    if not isinstance(out, dict):
        raise ValueError(f"JSON must be a JSON object, got {type(out).__name__}")
    return cast(dict[str, Any], out)

# export the function
__all__ = ["_parse_json_object_env", "_parse_json_object_str"]