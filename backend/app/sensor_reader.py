"""
Sensor Reader for the Hydration Monitoring System.

Provides a hardware abstraction layer between the raw load cell / ADC
and the rest of the system. The classifier and pipeline consume only
calibrated float readings in grams; they never interact with GPIO,
SPI, or I2C directly.

Hardware integration
--------------------
This module defines :class:`SensorReader` as an abstract base class.
Two concrete implementations are provided:

- :class:`HX711SensorReader` — real hardware driver for the HX711 ADC,
  which is the most common chip used with load cells on the Pi.
  **THIS CLASS CONTAINS STUBS.** Replace the body of :meth:`_read_raw`
  with your actual HX711 library calls before connecting hardware.

- :class:`MockSensorReader` — deterministic fake for unit and
  integration tests. Inject arbitrary weight sequences without any
  hardware present.

Where to plug in real hardware
-------------------------------
Search for the comment ``# HARDWARE`` in :class:`HX711SensorReader`.
Every line marked that way is a stub that needs replacing with real
library calls. No other file needs to change.
"""

import abc
import time
from collections import deque

from app.config import SensorConfig, SystemConfig


class SensorReader(abc.ABC):
    """
    Abstract base class for load cell sensor readers.

    Subclass this and implement :meth:`_read_raw` to add support for
    a new ADC chip or communication protocol. The rest of the system
    only ever calls :meth:`read_grams` and :meth:`tare`.
    """

    def __init__(self, config: SystemConfig):
        self._config: SensorConfig = config.sensor
        self._tare_offset: float = 0.0
        self._scale_factor: float = 1.0

    def tare(self, num_samples: int = 10) -> None:
        """
        Zero the sensor by averaging several readings as the baseline.

        Call this once on startup with the platform empty, and again
        any time you want to re-zero (e.g. after repositioning the unit).

        Args:
            num_samples: Number of raw readings to average for the tare.
                More samples produce a more stable baseline.
        """
        readings = [self._read_raw() for _ in range(num_samples)]
        self._tare_offset = sum(readings) / len(readings)

    def set_scale_factor(self, known_weight_g: float) -> None:
        """
        Calibrate the scale factor using a known reference weight.

        Place a reference weight on the platform after taring, then call
        this method with the true weight. The scale factor is stored and
        applied to all subsequent readings.

        Args:
            known_weight_g: True weight of the calibration object in grams.

        Raises:
            ValueError: If ``known_weight_g`` is zero or negative.
        """
        if known_weight_g <= 0:
            raise ValueError("Calibration weight must be positive.")
        raw = self._read_raw() - self._tare_offset
        if raw == 0:
            raise ValueError(
                "Raw reading after tare is zero; cannot compute scale factor."
            )
        self._scale_factor = known_weight_g / raw

    def read_grams(self) -> float:
        """
        Return a single calibrated weight reading in grams.

        Applies tare offset and scale factor to the raw ADC value.

        Returns:
            Calibrated weight in grams. May be slightly negative due to
            noise when the platform is empty; the classifier handles this.
        """
        raw = self._read_raw()
        return (raw - self._tare_offset) * self._scale_factor

    @abc.abstractmethod
    def _read_raw(self) -> float:
        """
        Return a single uncalibrated ADC reading.

        Subclasses must implement this. The value has no physical unit;
        calibration is applied in :meth:`read_grams`.

        Returns:
            Raw ADC value as a float.
        """


class HX711SensorReader(SensorReader):
    """
    Load cell reader for the HX711 24-bit ADC.

    The HX711 is wired to the Raspberry Pi via two GPIO pins: one for
    the clock signal and one for data. This class is a stub — the
    structure is complete but the actual library calls are marked with
    ``# HARDWARE`` comments and must be replaced before connecting
    physical hardware.

    Wiring (default pins, configurable in :class:`config.SensorConfig`):
        - VCC  → 3.3 V or 5 V (check your specific load cell)
        - GND  → GND
        - DT   → GPIO pin for data (see hardware docs)
        - SCK  → GPIO pin for clock (see hardware docs)

    Recommended library: ``hx711`` (pip install hx711) or
    ``RPi.GPIO`` with a custom bit-bang implementation.
    """

    def __init__(self, config: SystemConfig, data_pin: int, clock_pin: int):
        """
        Args:
            config: System configuration.
            data_pin: BCM GPIO pin number connected to HX711 DT.
            clock_pin: BCM GPIO pin number connected to HX711 SCK.
        """
        super().__init__(config)
        self._data_pin = data_pin
        self._clock_pin = clock_pin
        self._hx711 = None
        self._setup_hardware()

    def _setup_hardware(self) -> None:
        # HARDWARE: Replace this entire method body with real setup.
        # Example using the hx711 library:
        #
        #   import RPi.GPIO as GPIO
        #   from hx711 import HX711
        #   GPIO.setmode(GPIO.BCM)
        #   self._hx711 = HX711(dout_pin=self._data_pin,
        #                        pd_sck_pin=self._clock_pin)
        #   self._hx711.reset()
        #
        # Until hardware is connected, this is intentionally a no-op so
        # that the rest of the system can still be imported and tested.
        pass

    def _read_raw(self) -> float:
        # HARDWARE: Replace this with a real ADC read, e.g.:
        #
        #   reading = self._hx711.get_raw_data_mean(readings=3)
        #   if reading is False:
        #       raise IOError("HX711 read failed.")
        #   return float(reading)
        #
        # Returning 0.0 here so the stub is importable without hardware.
        return 0.0


class MockSensorReader(SensorReader):
    """
    Deterministic fake sensor for testing without hardware.

    Feed a sequence of weight values via :meth:`push` and they will be
    returned one by one from :meth:`read_grams`. If the queue runs out,
    the last pushed value is repeated.

    Example::

        reader = MockSensorReader(config)
        reader.push([500.0] * 25 + [0.0] * 5 + [420.0] * 25)
        weight = reader.read_grams()  # → 500.0
    """

    def __init__(self, config: SystemConfig):
        super().__init__(config)
        self._queue: deque[float] = deque()
        self._last: float = 0.0

    def push(self, values: list[float]) -> None:
        """
        Queue a sequence of raw weight values to be returned by reads.

        Args:
            values: List of weight values in grams. Consumed in order.
        """
        self._queue.extend(values)

    def _read_raw(self) -> float:
        if self._queue:
            self._last = self._queue.popleft()
        return self._last

    def read_grams(self) -> float:
        # MockSensorReader bypasses calibration — values pushed are
        # already in grams, so we skip tare/scale and return directly.
        return self._read_raw()