"""
Alert Engine for the DRIP Hydration Monitoring System.

Watches the session summary and decides what alert level the system
should be in. The engine itself does not drive the LED or send
notifications — it produces an :class:`AlertLevel` that the LED
controller and notification layer consume.

This separation means the alert logic can be tested independently of
any hardware, and the same engine can drive both the physical cactus
LED and the web dashboard without modification.
"""

import time
from dataclasses import dataclass
from enum import Enum

from app.config import AlertConfig, SystemConfig
from app.interactions.session import SessionSummary


class AlertLevel(Enum):
    """
    System-wide alert level.

    Two active states for now; a third priority tier is reserved for a
    future release.

    IDLE     — patient is on track; LED off.
    REMINDER — patient needs attention; LED on (amber breathing pulse).
    """

    IDLE = "idle"
    REMINDER = "reminder"


@dataclass
class AlertState:
    """
    Output of one evaluation cycle of the alert engine.

    Attributes:
        level: Current alert level.
        time_since_last_drink_s: Seconds elapsed since the most recent
            drink event, or None if no drink has been recorded yet.
        goal_progress: Fraction of daily goal reached (0.0–1.0+).
        evaluated_at: Unix timestamp of this evaluation.
    """

    level: AlertLevel
    time_since_last_drink_s: float | None
    goal_progress: float
    evaluated_at: float


class AlertEngine:
    """
    Evaluates session state and produces an :class:`AlertState`.

    Call :meth:`evaluate` on each pipeline tick (or whenever the session
    summary changes). The result can then be passed directly to the LED
    controller and any notification handlers.

    Quiet hours suppress everything except URGENT alerts so the cactus
    LED does not disturb sleeping patients overnight.

    Example::

        engine = AlertEngine(config, daily_goal_ml=1500.0)
        state = engine.evaluate(session.summary())
        led_controller.apply(state.level)
    """

    def __init__(self, config: SystemConfig, daily_goal_ml: float = 2000.0):
        self._config: AlertConfig = config.alert
        self._daily_goal_ml: float = daily_goal_ml

    def evaluate(self, summary: SessionSummary) -> AlertState:
        """
        Evaluate the current session summary and return an alert state.

        Args:
            summary: Current session snapshot from
                :class:`session.SessionManager`.

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

        goal_progress = summary.total_consumed_ml / max(1.0, self._daily_goal_ml)

        in_quiet_hours = self._in_quiet_hours(now)

        level = self._compute_level(
            time_since_drink=time_since_drink,
            goal_progress=goal_progress,
            in_quiet_hours=in_quiet_hours,
        )

        return AlertState(
            level=level,
            time_since_last_drink_s=time_since_drink,
            goal_progress=goal_progress,
            evaluated_at=now,
        )

    def _compute_level(
        self,
        time_since_drink: float | None,
        goal_progress: float,
        in_quiet_hours: bool,
    ) -> AlertLevel:
        # Goal reached → patient is fine; LED stays off.
        if goal_progress >= 1.0:
            return AlertLevel.IDLE

        # No drink recorded yet → immediate gentle reminder (suppressed
        # during quiet hours so the LED doesn't disturb sleeping patients).
        if time_since_drink is None:
            return AlertLevel.IDLE if in_quiet_hours else AlertLevel.REMINDER

        # Both warning and urgent thresholds map to REMINDER for now.
        # The urgent tier will be exposed as a separate visual state in a
        # later release; for now a single amber pulse covers both cases.
        if time_since_drink >= self._config.no_drink_urgent_s:
            # Long absence overrides quiet hours.
            return AlertLevel.REMINDER

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

        # Handle ranges that wrap midnight (e.g. 22:00 → 07:00).
        if start > end:
            return hour >= start or hour < end
        return start <= hour < end
