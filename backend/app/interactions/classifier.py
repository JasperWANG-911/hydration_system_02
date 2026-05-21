"""
Platform Interaction Classifier for Hydration Monitoring System.

This module implements a state-machine-based classifier that infers
drink/refill interactions from a single load cell embedded in a
cup platform. Because the sensor is blind while the cup is lifted,
only net weight change (pre-removal vs post-return) is observable;
the exact cause (drinking, spilling, pouring) cannot be determined.
"""

import statistics
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum

from app.config import SensorConfig, SystemConfig


class PlatformState(Enum):
    """Enumeration of all possible platform/sensor states."""

    NO_CUP = "no_cup"
    CUP_PRESENT_STABLE = "cup_present_stable"
    CUP_REMOVED = "cup_removed"
    WAITING_FOR_RETURN = "waiting_for_return"
    CUP_RETURN_DETECTED = "cup_return_detected"
    SETTLING_AFTER_RETURN = "settling_after_return"
    NET_WEIGHT_LOSS = "net_weight_loss"
    NET_WEIGHT_GAIN = "net_weight_gain"
    NO_MEANINGFUL_CHANGE = "no_meaningful_change"
    UNSTABLE_MOVEMENT = "unstable_movement"
    SENSOR_NOISE = "sensor_noise"
    SENSOR_FAULT = "sensor_fault"
    CUP_ABSENT_TIMEOUT = "cup_absent_timeout"


@dataclass
class InteractionResult:
    """
    Result returned by the classifier on every sensor update.

    Attributes:
        state: The inferred platform state for this update cycle.
        confidence: Estimated confidence in the classification (0.0-1.0).
        metadata: Optional supporting data (weights, net change, etc.).
    """

    state: PlatformState
    confidence: float
    metadata: dict = field(default_factory=dict)


class PlatformInteractionClassifier:
    """
    State-machine classifier for a single load-cell cup platform.

    The classifier processes raw weight readings and advances through
    a defined set of states to infer interaction events (e.g. cup
    removed, fluid consumed, cup refilled). It does NOT directly
    observe drinking or spilling — only net weight change is
    measurable.

    All thresholds and timing values are read from
    :class:`config.SensorConfig` so they can be tuned in one place
    without touching this file.

    Typical usage::

        config = SystemConfig()
        classifier = PlatformInteractionClassifier(config)
        while True:
            raw_weight = read_sensor()
            result = classifier.update(raw_weight)
            handle_result(result)
    """

    def __init__(self, config: SystemConfig):
        """
        Args:
            config: Top-level system configuration. Sensor thresholds
                are read from ``config.sensor``.
        """
        cfg: SensorConfig = config.sensor

        self._empty_threshold = cfg.empty_threshold_g
        self._noise_threshold = cfg.noise_threshold_g
        self._max_valid_weight = cfg.max_valid_weight_g
        self._stable_variance_threshold = cfg.stable_variance_threshold
        self._stability_window_size = cfg.stability_window_size
        self._absent_timeout_s = cfg.absent_timeout_s
        self._meaningful_change_threshold = cfg.meaningful_change_threshold_g

        self.current_state = PlatformState.NO_CUP

        self.weight_buffer: deque = deque(maxlen=self._stability_window_size)

        self.last_stable_weight: float = 0.0
        self.pre_removal_weight: float | None = None
        self.return_weight: float | None = None
        self.removal_timestamp: float | None = None

    def _variance(self) -> float:
        """
        Return the population variance of the current weight buffer.

        Returns:
            Population variance, or 0.0 if fewer than two samples exist.
        """
        if len(self.weight_buffer) < 2:
            return 0.0
        return statistics.pvariance(self.weight_buffer)

    def _stable_average(self) -> float:
        """
        Return the arithmetic mean of the current weight buffer.

        Returns:
            Mean weight in grams, or 0.0 if the buffer is empty.
        """
        if not self.weight_buffer:
            return 0.0
        return statistics.mean(self.weight_buffer)

    def _is_stable(self) -> bool:
        """
        Determine whether the weight buffer represents a stable reading.

        Stability requires the buffer to be full and its population
        variance to be below ``stable_variance_threshold``.

        Returns:
            True if the reading is stable, False otherwise.
        """
        if len(self.weight_buffer) < self._stability_window_size:
            return False
        return self._variance() < self._stable_variance_threshold

    def _detect_sensor_fault(self, weight: float) -> bool:
        """
        Check whether a raw weight reading indicates a sensor fault.

        A fault is flagged if the reading is physically impossible:
        either a large negative value (below -50 g) or above the
        configured maximum valid weight.

        Args:
            weight: Raw load cell reading in grams.

        Returns:
            True if a sensor fault is detected, False otherwise.
        """
        if weight < -50:
            return True
        if weight > self._max_valid_weight:
            return True
        return False

    def update(
        self, weight: float, now_ts: float | None = None
    ) -> InteractionResult:
        """
        Process one raw load-cell reading and advance the state machine.

        This method should be called on every sensor sample. It appends
        the reading to an internal rolling buffer, evaluates stability,
        and transitions between states as appropriate.

        Args:
            weight: Current load cell reading in grams.
            now_ts: Unix timestamp to attribute to this sample. Defaults
                to ``time.time()`` for live readings; pass the sample's
                own timestamp when replaying buffered data so the
                cup-absent timeout uses the right reference.

        Returns:
            An :class:`InteractionResult` describing the inferred state,
            a confidence score, and any relevant metadata for this cycle.
        """
        timestamp = time.time() if now_ts is None else now_ts
        self.weight_buffer.append(weight)

        if self._detect_sensor_fault(weight):
            self.current_state = PlatformState.SENSOR_FAULT
            return InteractionResult(
                state=PlatformState.SENSOR_FAULT,
                confidence=0.99,
                metadata={"weight": weight},
            )

        # Not enough samples yet for reliable inference.
        if len(self.weight_buffer) < self._stability_window_size:
            return InteractionResult(
                state=PlatformState.SENSOR_NOISE,
                confidence=0.50,
            )

        stable_weight = self._stable_average()
        variance = self._variance()
        stable = self._is_stable()

        if self.current_state == PlatformState.NO_CUP:

            if stable and stable_weight > self._empty_threshold:
                self.last_stable_weight = stable_weight
                self.current_state = PlatformState.CUP_PRESENT_STABLE
                return InteractionResult(
                    state=PlatformState.CUP_PRESENT_STABLE,
                    confidence=0.95,
                    metadata={"stable_weight": stable_weight},
                )

            return InteractionResult(
                state=PlatformState.NO_CUP,
                confidence=0.95,
            )

        if self.current_state == PlatformState.CUP_PRESENT_STABLE:

            # Use raw weight for removal detection so the transition is
            # immediate rather than waiting for the rolling average to drain.
            if weight < self._empty_threshold:
                self.pre_removal_weight = self.last_stable_weight
                self.removal_timestamp = timestamp
                self.current_state = PlatformState.WAITING_FOR_RETURN
                return InteractionResult(
                    state=PlatformState.CUP_REMOVED,
                    confidence=0.99,
                    metadata={"pre_removal_weight": self.pre_removal_weight},
                )

            if not stable:
                return InteractionResult(
                    state=PlatformState.UNSTABLE_MOVEMENT,
                    confidence=0.70,
                    metadata={"variance": variance},
                )

            self.last_stable_weight = stable_weight
            return InteractionResult(
                state=PlatformState.CUP_PRESENT_STABLE,
                confidence=0.95,
                metadata={"stable_weight": stable_weight},
            )

        if self.current_state == PlatformState.WAITING_FOR_RETURN:

            absence_duration = timestamp - self.removal_timestamp

            if absence_duration > self._absent_timeout_s:
                self.current_state = PlatformState.CUP_ABSENT_TIMEOUT
                return InteractionResult(
                    state=PlatformState.CUP_ABSENT_TIMEOUT,
                    confidence=0.95,
                    metadata={"absence_duration_s": absence_duration},
                )

            # Use raw weight so cup return is detected immediately,
            # without waiting for the rolling buffer to catch up.
            if weight > self._empty_threshold:
                self.current_state = PlatformState.SETTLING_AFTER_RETURN
                return InteractionResult(
                    state=PlatformState.CUP_RETURN_DETECTED,
                    confidence=0.96,
                )

            return InteractionResult(
                state=PlatformState.WAITING_FOR_RETURN,
                confidence=0.90,
            )

        if self.current_state == PlatformState.SETTLING_AFTER_RETURN:

            # Guard: cup removed again before settling completes.
            if weight < self._empty_threshold:
                self.pre_removal_weight = self.last_stable_weight
                self.removal_timestamp = timestamp
                self.current_state = PlatformState.WAITING_FOR_RETURN
                return InteractionResult(
                    state=PlatformState.CUP_REMOVED,
                    confidence=0.95,
                    metadata={"pre_removal_weight": self.pre_removal_weight},
                )

            if not stable:
                return InteractionResult(
                    state=PlatformState.UNSTABLE_MOVEMENT,
                    confidence=0.75,
                    metadata={"variance": variance},
                )

            self.return_weight = stable_weight
            net_change = self.return_weight - self.pre_removal_weight
            self.last_stable_weight = stable_weight
            self.current_state = PlatformState.CUP_PRESENT_STABLE

            if abs(net_change) < self._meaningful_change_threshold:
                return InteractionResult(
                    state=PlatformState.NO_MEANINGFUL_CHANGE,
                    confidence=0.90,
                    metadata={"net_change": net_change},
                )

            if net_change < 0:
                return InteractionResult(
                    state=PlatformState.NET_WEIGHT_LOSS,
                    confidence=0.88,
                    metadata={
                        "net_change": net_change,
                        "possible_interpretation": "possible_consumption_or_spill",
                    },
                )

            return InteractionResult(
                state=PlatformState.NET_WEIGHT_GAIN,
                confidence=0.88,
                metadata={
                    "net_change": net_change,
                    "possible_interpretation": "possible_refill",
                },
            )

        # Fallback — should not normally be reached.
        return InteractionResult(
            state=PlatformState.SENSOR_NOISE,
            confidence=0.40,
        )
