"""
Alert Engine for the Hydration Monitoring System.

Watches the session summary and button observations, then decides what
alert level the system should be in. The engine itself does not drive
the LED or send notifications — it produces an :class:`AlertLevel` that
the LED controller and notification layer consume.

This separation means the alert logic can be tested independently of
any hardware, and the same engine can drive both a physical LED and a
web dashboard without modification.
"""

import time
from dataclasses import dataclass
from enum import Enum

from config import AlertConfig, SystemConfig
from session_manager import SessionSummary


class AlertLevel(Enum):
    """
    System-wide alert level, from no action required to urgent.

    Values are ordered by severity — higher ordinal = more urgent.
    Consumers can compare levels with ``>=`` for threshold checks.
    """

    IDLE = "idle"
    GOAL_REACHED = "goal_reached"
    REMINDER = "reminder"
    URGENT = "urgent"


@dataclass
class AlertState:
    """
    Output of one evaluation cycle of the alert engine.

    Attributes:
        level: Current alert level.
        time_since_last_drink_s: Seconds elapsed since the most recent
            drink event, or None if no drink has been recorded yet.
        goal_progress: Fraction of daily goal reached (0.0–1.0+).
        observation_pending: True if a button press has been recorded
            that has not yet been acknowledged by staff.
        evaluated_at: Unix timestamp of this evaluation.
    """

    level: AlertLevel
    time_since_last_drink_s: float | None
    goal_progress: float
    observation_pending: bool
    evaluated_at: float


class AlertEngine:
    """
    Evaluates session state and produces an :class:`AlertState`.

    Call :meth:`evaluate` on each pipeline tick (or whenever the session
    summary changes). The result can then be passed directly to the LED
    controller and any notification handlers.

    Quiet hours suppress everything except URGENT alerts, so the LED
    does not disturb sleeping patients.

    Example::

        engine = AlertEngine(config)
        state = engine.evaluate(session.summary())
        led_controller.apply(state.level)
    """

    def __init__(self, config: SystemConfig):
        self._config: AlertConfig = config.alert
        self._observation_pending: bool = False
        self._observation_timestamp: float | None = None

    def evaluate(self, summary: SessionSummary) -> AlertState:
        """
        Evaluate the current session summary and return an alert state.

        Args:
            summary: Current session snapshot from
                :class:`session_manager.SessionManager`.

        Returns:
            An :class:`AlertState` reflecting the system's current
            urgency level.
        """
        now = time.time()

        time_since_drink = (
            now - summary.last_drink_time
            if summary.last_drink_time is not None
            else None
        )

        goal_progress = (
            summary.total_consumed_ml / max(1.0, self._config.no_drink_warning_s)
        )

        in_quiet_hours = self._in_quiet_hours(now)

        level = self._compute_level(
            time_since_drink=time_since_drink,
            goal_progress=summary.total_consumed_ml,
            in_quiet_hours=in_quiet_hours,
        )

        return AlertState(
            level=level,
            time_since_last_drink_s=time_since_drink,
            goal_progress=goal_progress,
            observation_pending=self._observation_pending,
            evaluated_at=now,
        )

    def record_button_press(self) -> None:
        """
        Record that the observation button has been pressed.

        Sets ``observation_pending`` to True on subsequent
        :meth:`evaluate` calls until :meth:`acknowledge_observation`
        is called.
        """
        self._observation_pending = True
        self._observation_timestamp = time.time()

    def acknowledge_observation(self) -> float | None:
        """
        Acknowledge a pending observation, clearing the pending flag.

        Returns:
            The Unix timestamp of the button press that is being
            acknowledged, or None if no observation was pending.
        """
        ts = self._observation_timestamp
        self._observation_pending = False
        self._observation_timestamp = None
        return ts

    def _compute_level(
        self,
        time_since_drink: float | None,
        goal_progress: float,
        in_quiet_hours: bool,
    ) -> AlertLevel:
        # No drink recorded yet — treat as if the full warning window
        # has elapsed to give an immediate gentle reminder on startup.
        if time_since_drink is None:
            if in_quiet_hours:
                return AlertLevel.IDLE
            return AlertLevel.REMINDER

        if time_since_drink >= self._config.no_drink_urgent_s:
            # Urgent overrides quiet hours — a very long absence is
            # worth a gentle light even at night.
            return AlertLevel.URGENT

        if in_quiet_hours:
            return AlertLevel.IDLE

        if time_since_drink >= self._config.no_drink_warning_s:
            return AlertLevel.REMINDER

        return AlertLevel.IDLE

    def _in_quiet_hours(self, timestamp: float) -> bool:
        if (
            self._config.quiet_hours_start is None
            or self._config.quiet_hours_end is None
        ):
            return False

        import datetime
        hour = datetime.datetime.fromtimestamp(timestamp).hour
        start = self._config.quiet_hours_start
        end = self._config.quiet_hours_end

        # Handle ranges that wrap midnight (e.g. 22:00 → 07:00)
        if start > end:
            return hour >= start or hour < end
        return start <= hour < end