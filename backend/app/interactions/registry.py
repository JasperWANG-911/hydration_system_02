"""Per-device runner registry.

The ingest route handles events from many devices concurrently. Each
device needs its own session state (accumulating drinks toward a daily
goal). `DeviceRunner` bundles one session for a given device, registers
callbacks that buffer drink events in a local list, and exposes
:meth:`drain` so the caller can pull those events out and persist them
to the database.

`DeviceRegistry` keeps a map of device_id → runner. Runners are created
lazily on first event; they live for the process lifetime (state is
not persisted across restarts).
"""

from __future__ import annotations

from app.config import SystemConfig
from app.interactions.session import DrinkEvent, SessionManager


class DeviceRunner:
    """One session for a single device."""

    def __init__(
        self,
        device_id: str,
        daily_goal_ml: float = 2000.0,
        config: SystemConfig | None = None,
    ) -> None:
        self.device_id = device_id
        cfg = config if config is not None else SystemConfig()
        self.session = SessionManager(cfg, daily_goal_ml=daily_goal_ml)
        self._pending_drinks: list[DrinkEvent] = []
        self.session.on_drink(self._pending_drinks.append)
        self.session.start()

    def record_intake(self, volume_ml: float, ts: float) -> None:
        """Record one button-confirmed intake event."""
        self.session.record_intake(volume_ml, now_ts=ts)

    def drain(self) -> list[DrinkEvent]:
        """Return — and clear — all buffered drink events."""
        drinks = self._pending_drinks
        self._pending_drinks = []
        return drinks


class DeviceRegistry:
    """Process-wide map of device_id → DeviceRunner."""

    def __init__(self) -> None:
        self._runners: dict[str, DeviceRunner] = {}

    def get(self, device_id: str, daily_goal_ml: float = 2000.0) -> DeviceRunner:
        runner = self._runners.get(device_id)
        if runner is None:
            runner = DeviceRunner(device_id, daily_goal_ml=daily_goal_ml)
            self._runners[device_id] = runner
        return runner

    def reset(self, device_id: str | None = None) -> None:
        """Drop one runner (or all of them) — useful in tests."""
        if device_id is None:
            self._runners.clear()
        else:
            self._runners.pop(device_id, None)


registry = DeviceRegistry()
