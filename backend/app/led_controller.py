"""
Cactus LED Controller for the Hydration Monitoring System.

Translates an :class:`alert_engine.AlertLevel` into a gentle breathing
pulse on the cactus LED. The LED is intentionally unobtrusive — low
brightness, warm white tones, slow pulse cycles — so it reads as a
friendly ambient indicator rather than a clinical alarm.

Hardware integration
--------------------
:class:`RgbLedController` is a stub. Replace the bodies of
:meth:`_set_color` with real PWM or NeoPixel calls.
Search for ``# HARDWARE`` comments for the exact lines to change.

:class:`MockLedController` records every state applied, which lets
tests assert on LED behaviour without any hardware present.
"""

import abc
import math
import time
from dataclasses import dataclass, field

from alert_engine import AlertLevel
from config import LedConfig, SystemConfig


@dataclass
class LedState:
    """
    A snapshot of what the LED controller last applied.

    Attributes:
        level: The :class:`AlertLevel` that produced this state.
        color: RGB tuple (0–255 each) currently set.
        brightness: Normalised brightness at the moment of the snapshot.
        applied_at: Unix timestamp of when the state was applied.
    """

    level: AlertLevel
    color: tuple[int, int, int]
    brightness: float
    applied_at: float


class LedController(abc.ABC):
    """
    Abstract base class for cactus LED drivers.

    Subclass this to add support for a different LED type (NeoPixel,
    simple PWM RGB, single-colour LED, etc.). The alert engine and
    pipeline only call :meth:`apply` and :meth:`off`.
    """

    def __init__(self, config: SystemConfig):
        self._config: LedConfig = config.led

    @abc.abstractmethod
    def apply(self, level: AlertLevel) -> None:
        """
        Apply the visual state corresponding to an alert level.

        This method is called on every pipeline tick. Implementations
        should be non-blocking — do not sleep inside this method.

        Args:
            level: The current :class:`AlertLevel` from the alert engine.
        """

    @abc.abstractmethod
    def off(self) -> None:
        """Turn the LED off immediately, regardless of current state."""


class RgbLedController(LedController):
    """
    RGB LED driver for a single NeoPixel or PWM RGB LED on the Pi.

    Produces a slow sinusoidal breathing effect whose colour and peak
    brightness depend on the current alert level. The breathing cycle
    uses a sine wave so the transition feels organic rather than stepped.

    The LED stays off (``IDLE``) while the patient is drinking regularly
    and during quiet hours. It pulses a warm white at low brightness for
    ``REMINDER``, slightly brighter for ``URGENT``, and a soft green
    briefly for ``GOAL_REACHED``.
    """

    def __init__(self, config: SystemConfig, pin: int):
        """
        Args:
            config: System configuration.
            pin: GPIO pin number (BCM) connected to the LED data line.
                For a NeoPixel strip this is the data pin.
                For a PWM RGB LED this should be the shared PWM pin.
        """
        super().__init__(config)
        self._pin = pin
        self._current_level = AlertLevel.IDLE
        self._cycle_start = time.time()
        self._setup_hardware()

    def _setup_hardware(self) -> None:
        # HARDWARE: Initialise your LED library here. Examples:
        #
        # NeoPixel (single pixel):
        #   import board, neopixel
        #   self._pixel = neopixel.NeoPixel(board.D18, 1, brightness=1.0,
        #                                    auto_write=False)
        #
        # RPi PWM RGB (common cathode, three pins):
        #   import RPi.GPIO as GPIO
        #   GPIO.setmode(GPIO.BCM)
        #   GPIO.setup([self._pin, ...], GPIO.OUT)
        #   self._pwm_r = GPIO.PWM(RED_PIN, 1000)
        #   self._pwm_g = GPIO.PWM(GREEN_PIN, 1000)
        #   self._pwm_b = GPIO.PWM(BLUE_PIN, 1000)
        #   self._pwm_r.start(0); self._pwm_g.start(0); self._pwm_b.start(0)
        pass

    def apply(self, level: AlertLevel) -> None:
        """
        Apply the visual state for the given alert level.

        Computes the current point in the breathing cycle and sets the
        LED colour and brightness accordingly.

        Args:
            level: Current alert level from the alert engine.
        """
        if level != self._current_level:
            self._current_level = level
            self._cycle_start = time.time()

        if level == AlertLevel.IDLE:
            self._set_color((0, 0, 0), 0.0)
            return

        color, peak_brightness = self._level_params(level)
        brightness = self._breathing_brightness(peak_brightness)
        self._set_color(color, brightness)

    def off(self) -> None:
        """Turn the LED off immediately."""
        self._set_color((0, 0, 0), 0.0)

    def _level_params(
        self, level: AlertLevel
    ) -> tuple[tuple[int, int, int], float]:
        cfg = self._config
        if level == AlertLevel.REMINDER:
            return cfg.reminder_color, cfg.reminder_brightness
        if level == AlertLevel.URGENT:
            return cfg.urgent_color, cfg.urgent_brightness
        if level == AlertLevel.GOAL_REACHED:
            return cfg.goal_color, cfg.goal_brightness
        return cfg.idle_color, cfg.idle_brightness

    def _breathing_brightness(self, peak: float) -> float:
        # Sine wave oscillates between 0 and peak over pulse_period_s.
        # Using (sin + 1) / 2 maps the -1..1 sine range to 0..1.
        elapsed = time.time() - self._cycle_start
        phase = (elapsed % self._config.pulse_period_s) / self._config.pulse_period_s
        return peak * (math.sin(2 * math.pi * phase - math.pi / 2) + 1) / 2

    def _set_color(self, color: tuple[int, int, int], brightness: float) -> None:
        # HARDWARE: Apply color and brightness to the physical LED here.
        #
        # NeoPixel example:
        #   r = int(color[0] * brightness)
        #   g = int(color[1] * brightness)
        #   b = int(color[2] * brightness)
        #   self._pixel[0] = (r, g, b)
        #   self._pixel.show()
        #
        # PWM RGB example:
        #   self._pwm_r.ChangeDutyCycle(color[0] / 255 * brightness * 100)
        #   self._pwm_g.ChangeDutyCycle(color[1] / 255 * brightness * 100)
        #   self._pwm_b.ChangeDutyCycle(color[2] / 255 * brightness * 100)
        pass


class MockLedController(LedController):
    """
    Fake LED controller for testing.

    Records every state applied in a history list so tests can assert
    on LED behaviour without any hardware present.

    Example::

        led = MockLedController(config)
        led.apply(AlertLevel.REMINDER)
        assert led.last_state().level == AlertLevel.REMINDER
    """

    def __init__(self, config: SystemConfig):
        super().__init__(config)
        self._history: list[LedState] = []

    def apply(self, level: AlertLevel) -> None:
        color, brightness = self._resolve(level)
        self._history.append(
            LedState(
                level=level,
                color=color,
                brightness=brightness,
                applied_at=time.time(),
            )
        )

    def off(self) -> None:
        self._history.append(
            LedState(
                level=AlertLevel.IDLE,
                color=(0, 0, 0),
                brightness=0.0,
                applied_at=time.time(),
            )
        )

    def last_state(self) -> LedState | None:
        """
        Return the most recently applied LED state, or None.

        Returns:
            The last :class:`LedState` recorded, or None if ``apply``
            has never been called.
        """
        return self._history[-1] if self._history else None

    def history(self) -> list[LedState]:
        """
        Return a copy of the full LED state history.

        Returns:
            List of :class:`LedState` instances in chronological order.
        """
        return list(self._history)

    def _resolve(
        self, level: AlertLevel
    ) -> tuple[tuple[int, int, int], float]:
        cfg = self._config
        mapping = {
            AlertLevel.IDLE: (cfg.idle_color, cfg.idle_brightness),
            AlertLevel.REMINDER: (cfg.reminder_color, cfg.reminder_brightness),
            AlertLevel.URGENT: (cfg.urgent_color, cfg.urgent_brightness),
            AlertLevel.GOAL_REACHED: (cfg.goal_color, cfg.goal_brightness),
        }
        return mapping.get(level, (cfg.idle_color, cfg.idle_brightness))