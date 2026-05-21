"""Per-device runner registry.

The ingest route handles samples from many devices concurrently. Each
device needs its own classifier state (the platform state machine is
stateful: it remembers `pre_removal_weight`, `last_stable_weight`, etc.)
and its own session aggregator (counting drinks toward a daily goal).

`DeviceRunner` bundles one classifier + one session for a given device,
pre-registers callbacks that buffer drink/refill events in a local list,
and exposes :meth:`drain` so the caller can pull those events out and
persist them to the database.

`DeviceRegistry` keeps a map of device_id → runner. Runners are created
lazily on first sample; they live for the process lifetime (state is
not persisted across restarts — see ARCHITECTURE notes).
"""

from __future__ import annotations

from app.config import SystemConfig
from app.interactions.classifier import (
    InteractionResult,
    PlatformInteractionClassifier,
)
from app.interactions.session import (
    DrinkEvent,
    RefillEvent,
    SessionManager,
)


class DeviceRunner:
    """One classifier + one session for a single device."""

    def __init__(
        self,
        device_id: str,
        daily_goal_ml: float = 2000.0,
        config: SystemConfig | None = None,
    ) -> None:
        self.device_id = device_id
        cfg = config if config is not None else SystemConfig()
        self.classifier = PlatformInteractionClassifier(cfg)
        self.session = SessionManager(cfg, daily_goal_ml=daily_goal_ml)
        self._pending_drinks: list[DrinkEvent] = []
        self._pending_refills: list[RefillEvent] = []
        self._pending_faults: list[InteractionResult] = []
        self.session.on_drink(self._pending_drinks.append)
        self.session.on_refill(self._pending_refills.append)
        self.session.on_fault(self._pending_faults.append)
        self.session.start()

    def process_sample(self, weight_g: float, ts: float) -> InteractionResult:
        """Feed one raw weight reading through classifier + session."""
        result = self.classifier.update(weight_g, now_ts=ts)
        self.session.process(result, now_ts=ts)
        return result

    def drain(
        self,
    ) -> tuple[list[DrinkEvent], list[RefillEvent], list[InteractionResult]]:
        """Return — and clear — all buffered drink / refill / fault events."""
        drinks = self._pending_drinks
        refills = self._pending_refills
        faults = self._pending_faults
        self._pending_drinks = []
        self._pending_refills = []
        self._pending_faults = []
        return drinks, refills, faults


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
