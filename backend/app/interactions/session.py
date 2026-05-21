"""
Session Manager for Hydration Monitoring System.

Consumes :class:`InteractionResult` events from the platform classifier
and accumulates them into a running hydration session. Responsible for
tracking total fluid intake, logging individual drink events, and
exposing session summaries for upstream consumers such as the alert
engine and persistence layer.

This module does not perform hardware I/O or issue alerts; it is a pure
aggregation layer between the classifier and the rest of the system.
"""

import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from app.config import SessionConfig, SystemConfig
from app.interactions.classifier import InteractionResult, PlatformState


class SessionState(Enum):
    """Lifecycle states for a hydration monitoring session."""

    IDLE = "idle"
    ACTIVE = "active"
    PAUSED = "paused"
    ENDED = "ended"


@dataclass
class DrinkEvent:
    """
    A single inferred consumption event within a session.

    Attributes:
        timestamp: Unix timestamp when the event was recorded.
        volume_ml: Estimated volume consumed in millilitres.
        confidence: Confidence score inherited from the classifier (0.0-1.0).
        raw_net_change_g: Raw net weight change reported by the classifier.
            Negative values indicate weight loss (consumption).
    """

    timestamp: float
    volume_ml: float
    confidence: float
    raw_net_change_g: float


@dataclass
class RefillEvent:
    """
    A single inferred refill event within a session.

    Attributes:
        timestamp: Unix timestamp when the event was recorded.
        volume_added_ml: Estimated volume added in millilitres.
        confidence: Confidence score inherited from the classifier (0.0-1.0).
        raw_net_change_g: Raw net weight change reported by the classifier.
            Positive values indicate weight gain (refill).
    """

    timestamp: float
    volume_added_ml: float
    confidence: float
    raw_net_change_g: float


@dataclass
class SessionSummary:
    """
    Snapshot of the current session state.

    Attributes:
        session_state: Current lifecycle state of the session.
        total_consumed_ml: Cumulative fluid intake for the session.
        drink_count: Number of discrete drink events recorded.
        refill_count: Number of discrete refill events recorded.
        start_time: Unix timestamp when the session started, or None if
            the session has not yet been started.
        duration_s: Elapsed session time in seconds, or 0.0 if the
            session has not started.
        last_drink_time: Unix timestamp of the most recent drink event,
            or None if no drinks have been recorded.
    """

    session_state: SessionState
    total_consumed_ml: float
    drink_count: int
    refill_count: int
    start_time: float | None
    duration_s: float
    last_drink_time: float | None


class SessionManager:
    """
    Aggregates platform classifier events into a hydration session.

    Each call to :meth:`process` ingests one :class:`InteractionResult`.
    Only events with states ``NET_WEIGHT_LOSS`` or ``NET_WEIGHT_GAIN``
    produce recorded events; all other states are silently ignored unless
    they affect session lifecycle (e.g. ``SENSOR_FAULT``).

    Fluid volume is estimated from weight using a configurable density
    factor (default 1.0 g/ml, appropriate for water). For other beverages
    the caller should adjust ``fluid_density_g_per_ml`` accordingly.

    Optional callbacks can be registered for drink and refill events,
    allowing the alert engine or UI layer to react without polling::

        manager = SessionManager(daily_goal_ml=2000.0)
        manager.on_drink(lambda e: print(f"Drank {e.volume_ml:.0f} ml"))
        manager.start()

        while True:
            result = classifier.update(read_sensor())
            manager.process(result)
    """

    def __init__(
        self,
        config: SystemConfig,
        daily_goal_ml: float | None = None,
    ):
        """
        Args:
            config: Top-level system configuration. Session thresholds
                are read from ``config.session``.
            daily_goal_ml: Target fluid intake in ml. Overrides
                ``config.session.default_daily_goal_ml`` when provided.
                Pass the patient's personal goal from
                :class:`patient_profile.PatientProfile` here.
        """
        cfg: SessionConfig = config.session

        self.daily_goal_ml = daily_goal_ml if daily_goal_ml is not None else cfg.default_daily_goal_ml
        self.fluid_density_g_per_ml = cfg.fluid_density_g_per_ml
        self.min_credible_volume_ml = cfg.min_credible_volume_ml
        self.max_credible_volume_ml = cfg.max_credible_volume_ml

        self._state = SessionState.IDLE
        self._start_time: float | None = None
        self._end_time: float | None = None
        self._pause_start: float | None = None
        self._total_paused_s: float = 0.0

        self._total_consumed_ml: float = 0.0
        self._drink_events: list[DrinkEvent] = []
        self._refill_events: list[RefillEvent] = []

        self._on_drink_callbacks: list[Callable[[DrinkEvent], None]] = []
        self._on_refill_callbacks: list[Callable[[RefillEvent], None]] = []
        self._on_fault_callbacks: list[Callable[[InteractionResult], None]] = []

    # -------------------------------------------------------------------------
    # Session lifecycle
    # -------------------------------------------------------------------------

    def start(self) -> None:
        """
        Begin a new session.

        Raises:
            RuntimeError: If the session is already active or has ended.
        """
        if self._state == SessionState.ACTIVE:
            raise RuntimeError("Session is already active.")
        if self._state == SessionState.ENDED:
            raise RuntimeError(
                "Session has ended. Create a new SessionManager to start again."
            )
        self._state = SessionState.ACTIVE
        self._start_time = time.time()

    def pause(self) -> None:
        """
        Pause an active session.

        While paused, incoming classifier events are ignored. Elapsed
        pause time is excluded from :attr:`SessionSummary.duration_s`.

        Raises:
            RuntimeError: If the session is not currently active.
        """
        if self._state != SessionState.ACTIVE:
            raise RuntimeError("Can only pause an active session.")
        self._state = SessionState.PAUSED
        self._pause_start = time.time()

    def resume(self) -> None:
        """
        Resume a paused session.

        Raises:
            RuntimeError: If the session is not currently paused.
        """
        if self._state != SessionState.PAUSED:
            raise RuntimeError("Can only resume a paused session.")
        self._total_paused_s += time.time() - self._pause_start
        self._pause_start = None
        self._state = SessionState.ACTIVE

    def end(self) -> None:
        """
        End the session.

        No further events will be processed after this call. The session
        summary remains accessible via :meth:`summary`.

        Raises:
            RuntimeError: If the session has not been started.
        """
        if self._state == SessionState.IDLE:
            raise RuntimeError("Session has not been started.")
        if self._state == SessionState.PAUSED:
            self._total_paused_s += time.time() - self._pause_start
        self._end_time = time.time()
        self._state = SessionState.ENDED

    # -------------------------------------------------------------------------
    # Event processing
    # -------------------------------------------------------------------------

    def process(
        self, result: InteractionResult, now_ts: float | None = None
    ) -> None:
        """
        Ingest one classifier result and update session state.

        Events are silently dropped if the session is not active. Only
        ``NET_WEIGHT_LOSS`` and ``NET_WEIGHT_GAIN`` states produce
        recorded events; all others are ignored except ``SENSOR_FAULT``,
        which triggers any registered fault callbacks.

        Args:
            result: The :class:`InteractionResult` produced by the
                platform classifier for the current sample cycle.
            now_ts: Unix timestamp to attach to any recorded event.
                Defaults to ``time.time()`` for live processing; pass the
                originating sample's timestamp when replaying buffered
                data so the event's ``timestamp`` field reflects when
                the interaction actually occurred.
        """
        if self._state != SessionState.ACTIVE:
            return

        timestamp = time.time() if now_ts is None else now_ts

        if result.state == PlatformState.SENSOR_FAULT:
            for cb in self._on_fault_callbacks:
                cb(result)
            return

        if result.state == PlatformState.NET_WEIGHT_LOSS:
            self._handle_weight_loss(result, timestamp)

        elif result.state == PlatformState.NET_WEIGHT_GAIN:
            self._handle_weight_gain(result, timestamp)

    def _handle_weight_loss(
        self, result: InteractionResult, timestamp: float
    ) -> None:
        net_change_g = result.metadata.get("net_change", 0.0)
        volume_ml = abs(net_change_g) / self.fluid_density_g_per_ml

        if volume_ml < self.min_credible_volume_ml:
            return

        clamped = min(volume_ml, self.max_credible_volume_ml)

        event = DrinkEvent(
            timestamp=timestamp,
            volume_ml=clamped,
            confidence=result.confidence,
            raw_net_change_g=net_change_g,
        )

        self._drink_events.append(event)
        self._total_consumed_ml += clamped

        for cb in self._on_drink_callbacks:
            cb(event)

    def _handle_weight_gain(
        self, result: InteractionResult, timestamp: float
    ) -> None:
        net_change_g = result.metadata.get("net_change", 0.0)
        volume_ml = net_change_g / self.fluid_density_g_per_ml

        event = RefillEvent(
            timestamp=timestamp,
            volume_added_ml=volume_ml,
            confidence=result.confidence,
            raw_net_change_g=net_change_g,
        )

        self._refill_events.append(event)

        for cb in self._on_refill_callbacks:
            cb(event)

    # -------------------------------------------------------------------------
    # Callbacks
    # -------------------------------------------------------------------------

    def on_drink(self, callback: Callable[[DrinkEvent], None]) -> None:
        """
        Register a callback to be called on each drink event.

        Args:
            callback: A callable that accepts a :class:`DrinkEvent`.
                Called synchronously from within :meth:`process`.
        """
        self._on_drink_callbacks.append(callback)

    def on_refill(self, callback: Callable[[RefillEvent], None]) -> None:
        """
        Register a callback to be called on each refill event.

        Args:
            callback: A callable that accepts a :class:`RefillEvent`.
                Called synchronously from within :meth:`process`.
        """
        self._on_refill_callbacks.append(callback)

    def on_fault(self, callback: Callable[[InteractionResult], None]) -> None:
        """
        Register a callback to be called when a sensor fault is detected.

        Args:
            callback: A callable that accepts the raw
                :class:`InteractionResult` carrying the fault state.
                Called synchronously from within :meth:`process`.
        """
        self._on_fault_callbacks.append(callback)

    # -------------------------------------------------------------------------
    # Queries
    # -------------------------------------------------------------------------

    def summary(self) -> SessionSummary:
        """
        Return a snapshot of the current session state.

        Returns:
            A :class:`SessionSummary` reflecting all events processed
            so far. Safe to call at any lifecycle stage.
        """
        now = time.time()

        if self._start_time is None:
            duration_s = 0.0
        elif self._state == SessionState.ENDED:
            duration_s = (
                self._end_time - self._start_time - self._total_paused_s
            )
        elif self._state == SessionState.PAUSED:
            duration_s = (
                now
                - self._start_time
                - self._total_paused_s
                - (now - self._pause_start)
            )
        else:
            duration_s = now - self._start_time - self._total_paused_s

        last_drink_time = (
            self._drink_events[-1].timestamp if self._drink_events else None
        )

        return SessionSummary(
            session_state=self._state,
            total_consumed_ml=self._total_consumed_ml,
            drink_count=len(self._drink_events),
            refill_count=len(self._refill_events),
            start_time=self._start_time,
            duration_s=duration_s,
            last_drink_time=last_drink_time,
        )

    def drink_events(self) -> list[DrinkEvent]:
        """
        Return a copy of all drink events recorded in this session.

        Returns:
            List of :class:`DrinkEvent` instances in chronological order.
        """
        return list(self._drink_events)

    def refill_events(self) -> list[RefillEvent]:
        """
        Return a copy of all refill events recorded in this session.

        Returns:
            List of :class:`RefillEvent` instances in chronological order.
        """
        return list(self._refill_events)

    def goal_progress(self) -> float:
        """
        Return progress toward the daily intake goal as a fraction.

        Returns:
            A value between 0.0 and 1.0, where 1.0 means the goal has
            been met. Values above 1.0 are possible if intake exceeds
            the goal.
        """
        if self.daily_goal_ml <= 0:
            return 0.0
        return self._total_consumed_ml / self.daily_goal_ml
