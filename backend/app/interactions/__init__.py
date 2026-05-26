"""Per-device interaction layer.

This package wraps the session manager (which accumulates button-recorded
intake events into drink records) and the device registry (which holds one
session per device for the FastAPI ingest route).
"""

from app.interactions.registry import DeviceRegistry, DeviceRunner, registry
from app.interactions.session import (
    DrinkEvent,
    SessionManager,
    SessionState,
    SessionSummary,
)

__all__ = [
    "DeviceRegistry",
    "DeviceRunner",
    "DrinkEvent",
    "SessionManager",
    "SessionState",
    "SessionSummary",
    "registry",
]
