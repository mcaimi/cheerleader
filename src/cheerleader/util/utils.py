# parsing utility

import os

# loads an env variable and strips it of whitespace
# None if not set
def _strip_or_none(key: str) -> str | None:
    raw = os.getenv(key)
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped if stripped else None

# returns a string setting from the environment
# default if not set
def _str_setting(key: str, default: str) -> str:
    return _strip_or_none(key) or default

# same as _str_setting but for ints
def _int_setting(key: str, default: int) -> int:
    raw = _strip_or_none(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default
