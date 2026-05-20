"""Per-device interaction inference layer.

This package wraps the platform interaction classifier (state machine
that turns raw load-cell weights into NET_WEIGHT_LOSS / NET_WEIGHT_GAIN
inferences) and the session manager (which aggregates classifier output
into recorded drink / refill events). A `DeviceRegistry` holds one
runner per device so the FastAPI ingest route can handle multiple beds
concurrently.
"""

from app.interactions.classifier import (
    InteractionResult,
    PlatformInteractionClassifier,
    PlatformState,
)
from app.interactions.registry import DeviceRegistry, DeviceRunner, registry
from app.interactions.session import (
    DrinkEvent,
    RefillEvent,
    SessionManager,
    SessionState,
    SessionSummary,
)

__all__ = [
    "DeviceRegistry",
    "DeviceRunner",
    "DrinkEvent",
    "InteractionResult",
    "PlatformInteractionClassifier",
    "PlatformState",
    "RefillEvent",
    "SessionManager",
    "SessionState",
    "SessionSummary",
    "registry",
]
