"""
Display driver for the DRIP bedside unit.

Drives a small I2C character display (e.g. 16×2 LCD with PCF8574
I2C backpack, or SSD1306 OLED) showing two numbers side by side:
actual intake so far, and the pace model's expected-by-now value.

Display layout (16-column LCD example):

    Drank:    450 ml
    Expect:   750 ml

When the unit enters nap/sleep mode the backlight is dimmed (not off —
the numbers remain readable if a nurse glances in) and restored on wake.

Hardware integration
--------------------
:class:`I2cDisplayDriver` is a stub. Replace :meth:`_write_lines` and
:meth:`_setup_hardware` with real display library calls once the
display hardware is confirmed. Search for ``# HARDWARE`` comments.

:class:`MockDisplayDriver` records every update for testing.
"""

import abc
import time
from dataclasses import dataclass

from app.config import DisplayConfig, SystemConfig


@dataclass
class DisplayState:
    """Snapshot of the last value written to the display."""

    actual_ml: float
    expected_ml: float
    dimmed: bool
    updated_at: float


class DisplayDriver(abc.ABC):
    """Abstract base class for the DRIP display."""

    def __init__(self, config: SystemConfig):
        self._cfg: DisplayConfig = config.display
        self._dimmed: bool = False

    @abc.abstractmethod
    def update(self, actual_ml: float, expected_ml: float) -> None:
        """
        Refresh both numbers on the display.

        Called immediately after each button press and on each scheduled
        pace model refresh.

        Args:
            actual_ml: Cumulative intake recorded so far in ml.
            expected_ml: Pace model expected intake by now in ml.
        """

    def dim(self) -> None:
        """Reduce display brightness for nap/sleep mode."""
        self._dimmed = True
        self._apply_dim(True)

    def wake(self) -> None:
        """Restore full display brightness on wake."""
        self._dimmed = False
        self._apply_dim(False)

    def show_startup(self, bed_id: str) -> None:
        """
        Show the bed ID on startup as a device assignment confirmation.

        Gives nursing staff a quick visual check that the right unit is
        at the right bed before the session begins.
        """
        self._write_lines(f"DRIP   {bed_id[:8]}", "Ready...")

    @abc.abstractmethod
    def _write_lines(self, line1: str, line2: str) -> None:
        """Write two lines of text to the physical display."""

    def _apply_dim(self, dimmed: bool) -> None:
        """Toggle backlight state. Override in hardware implementations."""


class I2cDisplayDriver(DisplayDriver):
    """
    I2C display driver for LCD1602 with PCF8574 backpack or SSD1306 OLED.

    I2C address and pin numbers are read from :class:`config.DisplayConfig`.
    The display type (LCD vs OLED) is determined by which ``# HARDWARE``
    block you uncomment in :meth:`_setup_hardware`.
    """

    def __init__(self, config: SystemConfig):
        super().__init__(config)
        self._setup_hardware()

    def _setup_hardware(self) -> None:
        # HARDWARE: Initialise I2C and your display library here.
        #
        # Option A — 16×2 LCD with PCF8574 I2C backpack (Raspberry Pi):
        #   from RPLCD.i2c import CharLCD
        #   self._lcd = CharLCD(
        #       i2c_expander="PCF8574",
        #       address=self._cfg.i2c_address,
        #       port=1,
        #       cols=16,
        #       rows=2,
        #       dotsize=8,
        #   )
        #
        # Option B — SSD1306 OLED 128×32 (Raspberry Pi):
        #   import board, busio, adafruit_ssd1306
        #   i2c = busio.I2C(board.SCL, board.SDA)
        #   self._oled = adafruit_ssd1306.SSD1306_I2C(128, 32, i2c,
        #                    addr=self._cfg.i2c_address)
        #
        # Option C — LCD1602 via I2C on Pico W (MicroPython):
        #   from machine import I2C, Pin
        #   from lcd_api import LcdApi
        #   from pico_i2c_lcd import I2cLcd
        #   i2c = I2C(0, sda=Pin(self._cfg.i2c_sda_pin),
        #                scl=Pin(self._cfg.i2c_scl_pin), freq=400000)
        #   self._lcd = I2cLcd(i2c, self._cfg.i2c_address, 2, 16)
        pass

    def update(self, actual_ml: float, expected_ml: float) -> None:
        line1 = f"Drank:  {int(actual_ml):>5} ml"
        line2 = f"Expect: {int(expected_ml):>5} ml"
        self._write_lines(line1, line2)

    def _write_lines(self, line1: str, line2: str) -> None:
        # HARDWARE: Write two lines to the physical display.
        #
        # LCD1602 example:
        #   self._lcd.clear()
        #   self._lcd.write_string(line1[:16])
        #   self._lcd.cursor_pos = (1, 0)
        #   self._lcd.write_string(line2[:16])
        #
        # SSD1306 OLED example (requires Pillow / adafruit_framebuf):
        #   self._oled.fill(0)
        #   self._oled.text(line1[:21], 0, 0)
        #   self._oled.text(line2[:21], 0, 16)
        #   self._oled.show()
        pass

    def _apply_dim(self, dimmed: bool) -> None:
        # HARDWARE: Toggle backlight or contrast.
        #
        # LCD1602 (RPLCD):
        #   self._lcd.backlight_enabled = not dimmed
        #
        # SSD1306 OLED (0 = off, 255 = full brightness):
        #   self._oled.contrast(30 if dimmed else 255)
        pass


class MockDisplayDriver(DisplayDriver):
    """
    Fake display driver for testing without hardware.

    Records every :meth:`update` call in a history list so tests can
    assert on display content without any physical display present.

    Example::

        display = MockDisplayDriver(config)
        display.update(450.0, 750.0)
        assert display.last_state().actual_ml == 450.0
    """

    def __init__(self, config: SystemConfig):
        super().__init__(config)
        self._history: list[DisplayState] = []

    def update(self, actual_ml: float, expected_ml: float) -> None:
        self._history.append(
            DisplayState(
                actual_ml=actual_ml,
                expected_ml=expected_ml,
                dimmed=self._dimmed,
                updated_at=time.time(),
            )
        )

    def _write_lines(self, line1: str, line2: str) -> None:
        pass

    def last_state(self) -> DisplayState | None:
        """Return the most recently written display state, or None."""
        return self._history[-1] if self._history else None

    def history(self) -> list[DisplayState]:
        """Return a copy of the full display state history."""
        return list(self._history)
