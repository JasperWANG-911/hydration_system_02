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

    Typical usage::

        classifier = PlatformInteractionClassifier()
        while True:
            raw_weight = read_sensor()
            result = classifier.update(raw_weight)
            handle_result(result)
    """

    def __init__(self):
        # Platform considered empty below this weight (grams)
        self.empty_threshold = 15

        # Fluctuations smaller than this are treated as noise (grams)
        self.noise_threshold = 5

        # Maximum physically plausible weight reading (grams)
        self.max_valid_weight = 5000

        # Population variance must be below this for a reading to be
        # considered stable
        self.stable_variance_threshold = 8

        # Number of samples required to assess stability
        self.stability_window_size = 20

        # Seconds before an absent cup triggers a timeout event
        self.absent_timeout_s = 300

        # Net change smaller than this (grams) is classified as no
        # meaningful change
        self.meaningful_change_threshold = 10

        self.current_state = PlatformState.NO_CUP

        self.weight_buffer: deque = deque(
            maxlen=self.stability_window_size
        )

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
        if len(self.weight_buffer) < self.stability_window_size:
            return False
        return self._variance() < self.stable_variance_threshold

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
        if weight > self.max_valid_weight:
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
        if len(self.weight_buffer) < self.stability_window_size:
            return InteractionResult(
                state=PlatformState.SENSOR_NOISE,
                confidence=0.50,
            )

        stable_weight = self._stable_average()
        variance = self._variance()
        stable = self._is_stable()

        if self.current_state == PlatformState.NO_CUP:

            if stable and stable_weight > self.empty_threshold:
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

            if not stable:
                return InteractionResult(
                    state=PlatformState.UNSTABLE_MOVEMENT,
                    confidence=0.70,
                    metadata={"variance": variance},
                )

            if stable_weight < self.empty_threshold:
                self.pre_removal_weight = self.last_stable_weight
                self.removal_timestamp = timestamp
                self.current_state = PlatformState.WAITING_FOR_RETURN
                return InteractionResult(
                    state=PlatformState.CUP_REMOVED,
                    confidence=0.99,
                    metadata={"pre_removal_weight": self.pre_removal_weight},
                )

            self.last_stable_weight = stable_weight
            return InteractionResult(
                state=PlatformState.CUP_PRESENT_STABLE,
                confidence=0.95,
                metadata={"stable_weight": stable_weight},
            )

        if self.current_state == PlatformState.WAITING_FOR_RETURN:

            absence_duration = timestamp - self.removal_timestamp

            if absence_duration > self.absent_timeout_s:
                self.current_state = PlatformState.CUP_ABSENT_TIMEOUT
                return InteractionResult(
                    state=PlatformState.CUP_ABSENT_TIMEOUT,
                    confidence=0.95,
                    metadata={"absence_duration_s": absence_duration},
                )

            # Use the raw reading rather than the stable average here so
            # that cup return is detected immediately, without waiting for
            # the rolling buffer to fully reflect the new weight.
            if weight > self.empty_threshold:
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

            # Guard: cup removed again before settling completes
            if weight < self.empty_threshold:
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

            if abs(net_change) < self.meaningful_change_threshold:
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
