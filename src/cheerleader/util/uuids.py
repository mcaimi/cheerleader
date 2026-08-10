#!/usr/bin/env python
"""UUID utilities."""

import uuid
import time

def generate_uuid() -> str:
    """Generate a new UUID."""
    return str(uuid.uuid4())

def is_valid_uuid(uuid: str) -> bool:
    """Check if a string is a valid UUID."""
    try:
        uuid.UUID(uuid)
        return True
    except ValueError:
        return False

# export the functions
__all__ = [
    "generate_uuid",
    "is_valid_uuid",
]