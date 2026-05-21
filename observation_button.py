"""
Observation Button for the Hydration Monitoring System.

Handles the physical button on the cactus unit. When pressed, the
button records a staff or family observation against the current
session. This is the primary way a passer-by can flag that a patient
appears not to have drunk recently, without needing a phone or computer.

A single press means: "I noticed something — please check on this
patient." The exact nature of the observation is recorded as a
free-text note if provided, or as a generic flag if not.

Hardware integration
--------------------
:class:`GpioObservationButton` is a stub. Replace the body of
:meth:`_setup_hardware` with real GPIO interrupt registration.
Search for ``# HARDWARE`` comments for the exact lines to change.

:class:`MockObservationButton` simulates button presses for testing.
"""

import abc
import time
from dataclasses import dataclass, field

from config import ButtonConfig, SystemConfig


@dataclass
class ButtonObservation:
    """
    A single recorded observation triggered by a button press.

    Attributes:
        timestamp: Unix timestamp of the press.
        note: Optional free-text note. In practice this will often be
            empty (the button is physical and has no keyboard); the note
            can be added later by staff reviewing the record.
        acknowledged: Whether a staff member has reviewed this
            observation. Set to True by the persistence layer when the
            record is confirmed.
    """

    timestamp: float = field(default_factory=time.time)
    note: str = ""
    acknowledged: bool = False


class ObservationButton(abc.ABC):
    """
    Abstract base class for the observation button.

    Subclass this to add support for a different input mechanism
    (capacitive touch, NFC tap, etc.). The pipeline and alert engine
    only call :meth:`poll` and :meth:`drain`.
    """

    def __init__(self, config: SystemConfig):
        self._config: ButtonConfig = config.button
        self._pending: list[ButtonObservation] = []
        self._last_press_time: float = 0.0

    def drain(self) -> list[ButtonObservation]:
        """
        Return and clear all pending observations.

        Called by the pipeline on each tick so that new observations
        are forwarded to the alert engine and persistence layer.

        Returns:
            List of :class:`ButtonObservation` instances recorded since
            the last call to :meth:`drain`. Empty list if none.
        """
        pending = list(self._pending)
        self._pending.clear()
        return pending

    def _record_press(self, note: str = "") -> None:
        now = time.time()
        if now - self._last_press_time < self._config.debounce_s:
            return
        self._last_press_time = now
        self._pending.append(ButtonObservation(timestamp=now, note=note))

    @abc.abstractmethod
    def poll(self) -> None:
        """
        Check for new button presses and record any found.

        Called by the pipeline on each tick. Implementations should be
        non-blocking. GPIO interrupt-driven implementations can leave
        this as a no-op and call :meth:`_record_press` from the
        interrupt callback instead.
        """


class GpioObservationButton(ObservationButton):
    """
    Physical button reader using Raspberry Pi GPIO.

    Registers a falling-edge interrupt on the configured GPIO pin so
    that button presses are captured asynchronously. :meth:`poll` is a
    no-op for this implementation — the interrupt callback handles
    everything.

    Assumes a pull-up resistor (internal or external) so the pin reads
    HIGH at rest and falls to LOW on press.
    """

    def __init__(self, config: SystemConfig):
        super().__init__(config)
        self._setup_hardware()

    def _setup_hardware(self) -> None:
        # HARDWARE: Register a GPIO interrupt for the button pin.
        #
        #   import RPi.GPIO as GPIO
        #   GPIO.setmode(GPIO.BCM)
        #   GPIO.setup(self._config.gpio_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        #   GPIO.add_event_detect(
        #       self._config.gpio_pin,
        #       GPIO.FALLING,
        #       callback=self._on_press,
        #       bouncetime=int(self._config.debounce_s * 1000),
        #   )
        pass

    def _on_press(self, channel: int) -> None:
        # HARDWARE: This method is called by the GPIO interrupt.
        # It calls _record_press which handles debounce and queuing.
        # No changes needed here once _setup_hardware is implemented.
        self._record_press()

    def poll(self) -> None:
        # Interrupt-driven — nothing to do on each poll tick.
        pass


class MockObservationButton(ObservationButton):
    """
    Fake button for testing without hardware.

    Call :meth:`press` to simulate a button press from test code.

    Example::

        button = MockObservationButton(config)
        button.press(note="Patient declined water at 14:30")
        observations = button.drain()
        assert len(observations) == 1
    """

    def poll(self) -> None:
        pass

    def press(self, note: str = "") -> None:
        """
        Simulate a button press.

        Args:
            note: Optional observation note to attach.
        """
        self._record_press(note=note)