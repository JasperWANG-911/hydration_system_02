"""
Button Box input handler for the DRIP hydration monitoring system.

Manages three physical buttons on the bedside unit:
  - PLUS  (+step_ml per press, default 50 ml)
  - MINUS (-step_ml per press, net delta floored at 0)
  - SLEEP (toggles nap / sleep mode)

Consecutive PLUS/MINUS presses within ``aggregation_window_s`` are
collected into a single :class:`IntakeEvent` so that pouring a full
glass (three or four presses) appears as one drink record rather than
four separate ones. The window is evaluated lazily on each
:meth:`drain_intake` call — no background timer is needed.

Hardware integration
--------------------
:class:`GpioButtonBox` is a stub. Replace the body of
:meth:`_setup_hardware` with real GPIO interrupt registration.
Search for ``# HARDWARE`` comments for the exact lines to change.

:class:`MockButtonBox` simulates button presses for testing.
"""

import abc
import time
from dataclasses import dataclass

from app.config import ButtonConfig, SystemConfig


@dataclass
class IntakeEvent:
    """
    A committed, aggregated intake record produced when the aggregation
    window closes.

    Attributes:
        timestamp: Unix timestamp of the first press in the window.
        volume_ml: Net intake in ml (always > 0; negative-net windows
            are silently discarded).
        press_count: Total number of plus and minus presses in the window.
    """

    timestamp: float
    volume_ml: float
    press_count: int


@dataclass
class SleepToggleEvent:
    """
    Produced each time the sleep button is pressed.

    Attributes:
        timestamp: Unix timestamp of the press.
        sleeping: True if the unit is entering sleep/nap mode,
            False if waking.
    """

    timestamp: float
    sleeping: bool


class ButtonBox(abc.ABC):
    """
    Abstract base class for the DRIP button box.

    Subclass this to add support for a different input mechanism.
    The pipeline and session manager only call :meth:`drain_intake`,
    :meth:`drain_sleep`, and :meth:`poll`.
    """

    def __init__(self, config: SystemConfig):
        self._cfg: ButtonConfig = config.button
        self._pending_delta: float = 0.0
        self._window_start: float | None = None
        self._press_count: int = 0
        self._sleeping: bool = False
        self._committed: list[IntakeEvent] = []
        self._sleep_events: list[SleepToggleEvent] = []

    # -------------------------------------------------------------------------
    # Public interface
    # -------------------------------------------------------------------------

    @property
    def sleeping(self) -> bool:
        """True while the unit is in nap / sleep mode."""
        return self._sleeping

    def drain_intake(self) -> list[IntakeEvent]:
        """
        Return and clear all committed intake events.

        Also checks whether the open aggregation window has expired and
        commits it if so. Called by the pipeline on each tick.

        Returns:
            List of :class:`IntakeEvent` instances since the last drain.
        """
        self._maybe_commit()
        events = list(self._committed)
        self._committed.clear()
        return events

    def drain_sleep(self) -> list[SleepToggleEvent]:
        """
        Return and clear all pending sleep toggle events.

        Returns:
            List of :class:`SleepToggleEvent` instances since the last drain.
        """
        events = list(self._sleep_events)
        self._sleep_events.clear()
        return events

    @abc.abstractmethod
    def poll(self) -> None:
        """
        Check for new button presses and record any found.

        Called by the pipeline on each tick. Interrupt-driven
        implementations leave this as a no-op.
        """

    # -------------------------------------------------------------------------
    # Internal press recording (called by interrupt callbacks or mock)
    # -------------------------------------------------------------------------

    def _record_plus(self, now: float | None = None) -> None:
        t = time.time() if now is None else now
        self._maybe_commit(t)
        if self._window_start is None:
            self._window_start = t
        self._pending_delta += self._cfg.step_ml
        self._press_count += 1

    def _record_minus(self, now: float | None = None) -> None:
        t = time.time() if now is None else now
        self._maybe_commit(t)
        if self._window_start is None:
            # Minus with no open window — nothing to subtract.
            return
        self._pending_delta = max(0.0, self._pending_delta - self._cfg.step_ml)
        self._press_count += 1

    def _record_sleep(self, now: float | None = None) -> None:
        t = time.time() if now is None else now
        # Commit any open drink window before toggling sleep state.
        self._force_commit(t)
        self._sleeping = not self._sleeping
        self._sleep_events.append(SleepToggleEvent(timestamp=t, sleeping=self._sleeping))

    # -------------------------------------------------------------------------
    # Aggregation window helpers
    # -------------------------------------------------------------------------

    def _maybe_commit(self, now: float | None = None) -> None:
        if self._window_start is None:
            return
        t = time.time() if now is None else now
        if t - self._window_start >= self._cfg.aggregation_window_s:
            self._force_commit(t)

    def _force_commit(self, now: float) -> None:
        if self._window_start is None:
            return
        if self._pending_delta > 0:
            self._committed.append(
                IntakeEvent(
                    timestamp=self._window_start,
                    volume_ml=self._pending_delta,
                    press_count=self._press_count,
                )
            )
        # Negative or zero net delta is silently discarded.
        self._pending_delta = 0.0
        self._window_start = None
        self._press_count = 0


class GpioButtonBox(ButtonBox):
    """
    Physical button box using Raspberry Pi GPIO interrupt callbacks.

    Registers a falling-edge interrupt on each of the three configured
    GPIO pins. :meth:`poll` is a no-op — the interrupt callbacks handle
    everything asynchronously.

    Assumes pull-up resistors (internal or external) so each pin reads
    HIGH at rest and falls to LOW on press.
    """

    def __init__(self, config: SystemConfig):
        super().__init__(config)
        self._last_press: dict[str, float] = {"plus": 0.0, "minus": 0.0, "sleep": 0.0}
        self._setup_hardware()

    def _setup_hardware(self) -> None:
        # HARDWARE: Register GPIO interrupts for all three buttons.
        #
        #   import RPi.GPIO as GPIO
        #   GPIO.setmode(GPIO.BCM)
        #   for pin in (self._cfg.plus_pin, self._cfg.minus_pin, self._cfg.sleep_pin):
        #       GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        #   GPIO.add_event_detect(self._cfg.plus_pin,  GPIO.FALLING,
        #       callback=self._on_plus,  bouncetime=int(self._cfg.debounce_s * 1000))
        #   GPIO.add_event_detect(self._cfg.minus_pin, GPIO.FALLING,
        #       callback=self._on_minus, bouncetime=int(self._cfg.debounce_s * 1000))
        #   GPIO.add_event_detect(self._cfg.sleep_pin, GPIO.FALLING,
        #       callback=self._on_sleep, bouncetime=int(self._cfg.debounce_s * 1000))
        pass

    def _on_plus(self, channel: int) -> None:
        now = time.time()
        if now - self._last_press["plus"] >= self._cfg.debounce_s:
            self._last_press["plus"] = now
            if not self._sleeping:
                self._record_plus(now)

    def _on_minus(self, channel: int) -> None:
        now = time.time()
        if now - self._last_press["minus"] >= self._cfg.debounce_s:
            self._last_press["minus"] = now
            if not self._sleeping:
                self._record_minus(now)

    def _on_sleep(self, channel: int) -> None:
        now = time.time()
        if now - self._last_press["sleep"] >= self._cfg.debounce_s:
            self._last_press["sleep"] = now
            self._record_sleep(now)

    def poll(self) -> None:
        # Interrupt-driven — nothing to do on each poll tick.
        pass


class MockButtonBox(ButtonBox):
    """
    Fake button box for testing without hardware.

    Call :meth:`press_plus`, :meth:`press_minus`, or :meth:`press_sleep`
    to simulate button presses from test code.

    Example::

        box = MockButtonBox(config)
        box.press_plus()
        box.press_plus()
        box.press_plus()
        # advance time past aggregation window, then drain
        events = box.drain_intake()
        assert events[0].volume_ml == 150.0
    """

    def poll(self) -> None:
        pass

    def press_plus(self, now: float | None = None) -> None:
        """Simulate a press of the PLUS button."""
        self._record_plus(now)

    def press_minus(self, now: float | None = None) -> None:
        """Simulate a press of the MINUS button."""
        self._record_minus(now)

    def press_sleep(self, now: float | None = None) -> None:
        """Simulate a press of the SLEEP button."""
        self._record_sleep(now)
