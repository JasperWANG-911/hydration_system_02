"""
Session Manager for the DRIP Hydration Monitoring System.

Accumulates button-recorded intake events into a running hydration
session. Tracks total fluid intake, logs individual drink events, and
exposes session summaries for the alert engine and persistence layer.

This module has no hardware dependencies and no knowledge of the input
mechanism — it receives explicit :meth:`record_intake` calls and does
not care whether they came from a button, a API call, or a test.
"""

import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from app.config import SessionConfig, SystemConfig


class SessionState(Enum):
    """Lifecycle states for a hydration monitoring session."""

    IDLE = "idle"
    ACTIVE = "active"
    PAUSED = "paused"
    ENDED = "ended"


@dataclass
class DrinkEvent:
    """
    A single recorded intake event within a session.

    Attributes:
        timestamp: Unix timestamp when the intake was recorded.
            For button input this is the timestamp of the first press
            in the aggregation window.
        volume_ml: Volume consumed in millilitres.
    """

    timestamp: float
    volume_ml: float


@dataclass
class SessionSummary:
    """
    Snapshot of the current session state.

    Attributes:
        session_state: Current lifecycle state of the session.
        total_consumed_ml: Cumulative fluid intake for the session.
        drink_count: Number of discrete drink events recorded.
        start_time: Unix timestamp when the session started, or None if
            the session has not yet been started.
        duration_s: Elapsed active session time in seconds (nap time
            excluded), or 0.0 if the session has not started.
        last_drink_time: Unix timestamp of the most recent drink event,
            or None if no drinks have been recorded.
    """

    session_state: SessionState
    total_consumed_ml: float
    drink_count: int
    start_time: float | None
    duration_s: float
    last_drink_time: float | None


class SessionManager:
    """
    Aggregates intake events into a hydration session.

    Each call to :meth:`record_intake` records one drink event and
    accumulates the volume. Session lifecycle (start / pause / resume /
    end) is managed separately so that nap pauses are excluded from the
    active duration used by the pace model.

    Optional callbacks can be registered for drink events so the alert
    engine or UI layer can react without polling::

        manager = SessionManager(config, daily_goal_ml=1500.0)
        manager.on_drink(lambda e: print(f"Drank {e.volume_ml:.0f} ml"))
        manager.start()
        manager.record_intake(150.0)
    """

    def __init__(
        self,
        config: SystemConfig,
        daily_goal_ml: float | None = None,
    ):
        """
        Args:
            config: Top-level system configuration.
            daily_goal_ml: Target fluid intake in ml. Overrides
                ``config.session.default_daily_goal_ml`` when provided.
        """
        cfg: SessionConfig = config.session
        self.daily_goal_ml = (
            daily_goal_ml if daily_goal_ml is not None else cfg.default_daily_goal_ml
        )

        self._state = SessionState.IDLE
        self._start_time: float | None = None
        self._end_time: float | None = None
        self._pause_start: float | None = None
        self._total_paused_s: float = 0.0

        self._total_consumed_ml: float = 0.0
        self._drink_events: list[DrinkEvent] = []

        self._on_drink_callbacks: list[Callable[[DrinkEvent], None]] = []

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
        Pause an active session (nap/sleep mode).

        While paused, :meth:`record_intake` calls are silently ignored.
        Elapsed pause time is excluded from :attr:`SessionSummary.duration_s`
        so the pace model is not penalised for nap time.

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

        No further intake events will be recorded after this call. The
        session summary remains accessible via :meth:`summary`.

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
    # Intake recording
    # -------------------------------------------------------------------------

    def record_intake(
        self, volume_ml: float, now_ts: float | None = None
    ) -> None:
        """
        Record a button-confirmed intake event and update the session total.

        Silently dropped if the session is not active (e.g. during a nap
        pause or before the session has started).

        Args:
            volume_ml: Volume consumed in ml. Must be positive; zero or
                negative values are silently ignored.
            now_ts: Unix timestamp to attribute to this event. Defaults
                to ``time.time()``. Pass the original button-press
                timestamp when replaying buffered events so the record
                reflects when the intake actually occurred.
        """
        if self._state != SessionState.ACTIVE:
            return
        if volume_ml <= 0:
            return

        timestamp = time.time() if now_ts is None else now_ts
        event = DrinkEvent(timestamp=timestamp, volume_ml=volume_ml)

        self._drink_events.append(event)
        self._total_consumed_ml += volume_ml

        for cb in self._on_drink_callbacks:
            cb(event)

    # -------------------------------------------------------------------------
    # Callbacks
    # -------------------------------------------------------------------------

    def on_drink(self, callback: Callable[[DrinkEvent], None]) -> None:
        """
        Register a callback to be called on each recorded drink event.

        Args:
            callback: A callable that accepts a :class:`DrinkEvent`.
                Called synchronously from within :meth:`record_intake`.
        """
        self._on_drink_callbacks.append(callback)

    # -------------------------------------------------------------------------
    # Queries
    # -------------------------------------------------------------------------

    def summary(self) -> SessionSummary:
        """
        Return a snapshot of the current session state.

        Returns:
            A :class:`SessionSummary` reflecting all events recorded
            so far. Safe to call at any lifecycle stage.
        """
        now = time.time()

        if self._start_time is None:
            duration_s = 0.0
        elif self._state == SessionState.ENDED:
            duration_s = self._end_time - self._start_time - self._total_paused_s
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
            start_time=self._start_time,
            duration_s=max(0.0, duration_s),
            last_drink_time=last_drink_time,
        )

    def drink_events(self) -> list[DrinkEvent]:
        """Return a copy of all drink events recorded in this session."""
        return list(self._drink_events)

    def goal_progress(self) -> float:
        """
        Return progress toward the daily intake goal as a fraction.

        Returns:
            A value in [0.0, 1.0+]. 1.0 means the goal has been met.
        """
        if self.daily_goal_ml <= 0:
            return 0.0
        return self._total_consumed_ml / self.daily_goal_ml
