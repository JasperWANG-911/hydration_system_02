"""
Cactus LED Controller for the DRIP Hydration Monitoring System.

Translates an :class:`alert_engine.AlertLevel` into a gentle breathing
pulse on the cactus LED (patient-facing, back of device) and the corridor
indicator strip (front/top edge). Both are wired in parallel to the same
GPIO pin so they always show the same state.

Two states for now:
  IDLE     — LED off; patient is on track.
  REMINDER — slow amber breathing pulse; patient needs attention.

A third priority tier (higher-urgency colour or faster pulse) is
reserved for a future release.

Hardware integration
--------------------
:class:`RgbLedController` is a stub. Replace the body of
:meth:`_set_color` with real PWM or NeoPixel calls.
Search for ``# HARDWARE`` comments for the exact lines to change.

:class:`MockLedController` records every state applied, which lets
tests assert on LED behaviour without any hardware present.
"""

import abc
import math
import time
from dataclasses import dataclass, field

from app.alert_engine import AlertLevel
from app.config import LedConfig, SystemConfig


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
    """Abstract base class for cactus LED drivers."""

    def __init__(self, config: SystemConfig):
        self._config: LedConfig = config.led

    @abc.abstractmethod
    def apply(self, level: AlertLevel) -> None:
        """Apply the visual state corresponding to an alert level."""

    @abc.abstractmethod
    def off(self) -> None:
        """Turn the LED off immediately, regardless of current state."""


class RgbLedController(LedController):
    """
    RGB LED driver for a NeoPixel or PWM RGB LED wired to a single GPIO.

    Produces a slow sinusoidal breathing effect. The colour and peak
    brightness are fixed (amber) for REMINDER; the LED is off for IDLE.
    The breathing cycle uses a sine wave so the transition feels organic.

    Both the patient-facing cactus LED and the corridor strip are wired
    in parallel to the same pin — they always behave identically.
    """

    def __init__(self, config: SystemConfig, pin: int):
        """
        Args:
            config: System configuration.
            pin: GPIO pin number (BCM) connected to the LED data line.
        """
        super().__init__(config)
        self._pin = pin
        self._current_level = AlertLevel.IDLE
        self._cycle_start = time.time()
        self._setup_hardware()

    def _setup_hardware(self) -> None:
        # HARDWARE: Initialise your LED library here. Examples:
        #
        # NeoPixel (single pixel or short strip):
        #   import board, neopixel
        #   self._pixel = neopixel.NeoPixel(board.D18, 1, brightness=1.0,
        #                                    auto_write=False)
        #
        # RPi PWM RGB (common cathode, three pins — amber uses R+G only):
        #   import RPi.GPIO as GPIO
        #   GPIO.setmode(GPIO.BCM)
        #   GPIO.setup([RED_PIN, GREEN_PIN, BLUE_PIN], GPIO.OUT)
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

        # REMINDER (and future tiers) → amber breathing pulse.
        brightness = self._breathing_brightness(self._config.reminder_brightness)
        self._set_color(self._config.reminder_color, brightness)

    def off(self) -> None:
        """Turn the LED off immediately."""
        self._set_color((0, 0, 0), 0.0)

    def _breathing_brightness(self, peak: float) -> float:
        """Sine wave that oscillates between 0 and ``peak`` over ``pulse_period_s``."""
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
        if level == AlertLevel.IDLE:
            color, brightness = self._config.idle_color, self._config.idle_brightness
        else:
            color, brightness = self._config.reminder_color, self._config.reminder_brightness
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
        """Return the most recently applied LED state, or None."""
        return self._history[-1] if self._history else None

    def history(self) -> list[LedState]:
        """Return a copy of the full LED state history."""
        return list(self._history)
