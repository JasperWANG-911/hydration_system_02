"""
Display driver for the DRIP bedside unit.

Drives a 20×4 character LCD (HD44780 with PCF8574 I2C backpack, e.g.
the common 2004A module) using a big-font renderer that fills the top
three rows with oversized digits.

Display layout
--------------
The 20-column display is split into two halves:

    cols  0– 9 : "drank" side  — actual intake (ml)
    cols 10–19 : "target" side — pace-model expected intake (ml)

Each half shows a 4-digit number rendered in a 2-column × 3-row custom
font (using 8 CGRAM characters).  Row 3 (the bottom row) is reserved
for an optional HH:MM clock on the right edge; the rest of row 3 is
blank.  A narrow separator in cols 8–11 keeps the two halves apart.

    Row 0:  [d d d d]  ┊  [t t t t]
    Row 1:  [d d d d]  ┊  [t t t t]
    Row 2:  [d d d d]  ┊  [t t t t]
    Row 3:                    HH:MM

Each digit occupies 2 columns and 3 rows, so a 4-digit number uses
columns 0–7 (with one blank spacer col per digit boundary).

Big-font CGRAM characters (8 slots, indexed 0–7)
-------------------------------------------------
  0 (TOP)  — top 4 pixel rows filled   ████
  1 (BOT)  — bottom 4 pixel rows filled ████
  2 (FULL) — all 8 pixel rows filled   █████
  3 (LEFT) — left 2 columns filled     ██···
  4 (RIGHT)— right 2 columns filled    ···██
  5 (TL)   — top-left corner           ██ (top 4 rows, left 2 cols)
  6 (TR)   — top-right corner          ██ (top 4 rows, right 2 cols)
  7 (BR)   — bottom-right corner       ██ (bottom 4 rows, right 2 cols)

Hardware integration
--------------------
:class:`LcdDisplayDriver` is a stub targeting RPLCD (Raspberry Pi) or a
MicroPython pico-i2c-lcd compatible library. Uncomment the relevant
``# HARDWARE`` block in :meth:`_setup_hardware` and :meth:`_write_raw`.

:class:`MockDisplayDriver` records every :meth:`update` call for tests.
"""

import abc
import time
from dataclasses import dataclass

from app.config import DisplayConfig, SystemConfig

# ---------------------------------------------------------------------------
# CGRAM character bitmaps (8 rows × 5 cols, stored as 8-byte lists)
# ---------------------------------------------------------------------------

_CGRAM: list[list[int]] = [
    # 0 TOP — top half filled
    [0b11111, 0b11111, 0b11111, 0b11111, 0b00000, 0b00000, 0b00000, 0b00000],
    # 1 BOT — bottom half filled
    [0b00000, 0b00000, 0b00000, 0b00000, 0b11111, 0b11111, 0b11111, 0b11111],
    # 2 FULL — all rows filled
    [0b11111, 0b11111, 0b11111, 0b11111, 0b11111, 0b11111, 0b11111, 0b11111],
    # 3 LEFT — left two columns filled
    [0b11000, 0b11000, 0b11000, 0b11000, 0b11000, 0b11000, 0b11000, 0b11000],
    # 4 RIGHT — right two columns filled
    [0b00011, 0b00011, 0b00011, 0b00011, 0b00011, 0b00011, 0b00011, 0b00011],
    # 5 TL — top-left corner (top 4 rows, left 2 cols)
    [0b11000, 0b11000, 0b11000, 0b11000, 0b00000, 0b00000, 0b00000, 0b00000],
    # 6 TR — top-right corner (top 4 rows, right 2 cols)
    [0b00011, 0b00011, 0b00011, 0b00011, 0b00000, 0b00000, 0b00000, 0b00000],
    # 7 BR — bottom-right corner (bottom 4 rows, right 2 cols)
    [0b00000, 0b00000, 0b00000, 0b00000, 0b00011, 0b00011, 0b00011, 0b00011],
]

# Short names to keep _DIGIT_PATTERNS readable
_T  = "\x00"   # TOP
_B  = "\x01"   # BOT
_F  = "\x02"   # FULL
_L  = "\x03"   # LEFT
_R  = "\x04"   # RIGHT
_TL = "\x05"   # TL corner
_TR = "\x06"   # TR corner
_BR = "\x07"   # BR corner
_S  = " "      # space

# Each entry is (top_L, top_R, mid_L, mid_R, bot_L, bot_R) — 2 cols × 3 rows
_DIGIT_PATTERNS: dict[str, tuple[str, str, str, str, str, str]] = {
    "0": (_T,  _T,  _L,  _R,  _B,  _B),
    "1": (_S,  _T,  _S,  _F,  _S,  _B),
    "2": (_T,  _TR, _TL, _B,  _B,  _S),
    "3": (_T,  _TR, _T,  _BR, _T,  _BR),
    "4": (_L,  _R,  _B,  _F,  _S,  _B),
    "5": (_TL, _T,  _B,  _TR, _B,  _B),
    "6": (_TL, _T,  _L,  _TR, _B,  _B),
    "7": (_T,  _T,  _S,  _R,  _S,  _R),
    "8": (_T,  _T,  _F,  _F,  _B,  _B),
    "9": (_T,  _T,  _B,  _R,  _T,  _BR),
    " ": (_S,  _S,  _S,  _S,  _S,  _S),
}


def _render_big_number(value: int, width: int = 4) -> tuple[str, str, str]:
    """
    Render ``value`` as a big-font string triple (row0, row1, row2).

    Each returned string is ``width * 2`` characters wide — two display
    columns per digit.  Values too large for ``width`` digits are
    right-truncated with ``>`` on the left edge as a clipping indicator.

    Args:
        value:  Non-negative integer to render.
        width:  Number of digit cells (default 4 → 8 display columns).

    Returns:
        A tuple ``(top_row, mid_row, bot_row)`` where each string is
        ``width * 2`` characters long.
    """
    digits = str(max(0, value))
    if len(digits) > width:
        # Overflow indicator: fill with '9's and put '>' marker at left
        digits = ">" + "9" * (width - 1)

    # Left-pad with spaces to fill ``width`` digit cells
    digits = digits.rjust(width)

    top = mid = bot = ""
    for ch in digits:
        tl, tr, ml, mr, bl, br = _DIGIT_PATTERNS.get(ch, _DIGIT_PATTERNS[" "])
        top += tl + tr
        mid += ml + mr
        bot += bl + br

    return top, mid, bot


# ---------------------------------------------------------------------------
# Dataclass for MockDisplayDriver history
# ---------------------------------------------------------------------------

@dataclass
class DisplayState:
    """Snapshot of the last value written to the display."""

    actual_ml: float
    expected_ml: float
    dimmed: bool
    updated_at: float


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class DisplayDriver(abc.ABC):
    """Abstract base class for the DRIP display."""

    def __init__(self, config: SystemConfig):
        self._cfg: DisplayConfig = config.display
        self._dimmed: bool = False

    @abc.abstractmethod
    def update(self, actual_ml: float, expected_ml: float) -> None:
        """
        Refresh both numbers on the display.

        Called immediately after each button-press commit and on each
        scheduled pace model refresh.

        Args:
            actual_ml:   Cumulative intake recorded so far (ml).
            expected_ml: Pace model expected intake by now (ml).
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
        """Show bed ID on startup as a device assignment confirmation."""
        # Write directly to the raw layer — bypasses the big-font renderer
        self._write_raw({
            0: f"{'DRIP':^20}",
            1: f"{bed_id[:20]:^20}",
            2: f"{'Ready...':^20}",
            3: " " * 20,
        })

    @abc.abstractmethod
    def _write_raw(self, rows: dict[int, str]) -> None:
        """
        Write up to 4 rows to the physical display.

        Args:
            rows: Mapping of row index (0–3) to a string of exactly 20
                  characters.  Missing rows are left unchanged.
        """

    def _apply_dim(self, dimmed: bool) -> None:
        """Toggle backlight state. Override in hardware implementations."""


# ---------------------------------------------------------------------------
# Hardware driver — 20×4 LCD (2004A) with PCF8574 I2C backpack
# ---------------------------------------------------------------------------

class LcdDisplayDriver(DisplayDriver):
    """
    Big-font display driver for a 20×4 HD44780 LCD (2004A).

    Renders actual and expected intake as large overlapping digits using
    8 custom CGRAM characters.  The display is split down the middle:
    cols 0–9 = actual, cols 10–19 = expected.  Row 3 shows HH:MM (right
    side) when ``show_time`` is True.

    I2C address and pin numbers come from :class:`config.DisplayConfig`.
    """

    def __init__(self, config: SystemConfig, show_time: bool = True):
        super().__init__(config)
        self._show_time = show_time
        self._setup_hardware()
        self._upload_cgram()

    def _setup_hardware(self) -> None:
        # HARDWARE: Initialise the LCD here. Choose one option below.
        #
        # Option A — RPLCD (Raspberry Pi, Python 3):
        #   from RPLCD.i2c import CharLCD
        #   self._lcd = CharLCD(
        #       i2c_expander="PCF8574",
        #       address=self._cfg.i2c_address,
        #       port=1,          # I2C bus 1 on RPi
        #       cols=20,
        #       rows=4,
        #       dotsize=8,
        #       auto_linebreaks=False,
        #   )
        #
        # Option B — pico-i2c-lcd (MicroPython on Pico W):
        #   from machine import I2C, Pin
        #   from pico_i2c_lcd import I2cLcd
        #   i2c = I2C(0, sda=Pin(self._cfg.i2c_sda_pin),
        #                scl=Pin(self._cfg.i2c_scl_pin), freq=400_000)
        #   self._lcd = I2cLcd(i2c, self._cfg.i2c_address, 4, 20)
        self._lcd = None  # replaced by HARDWARE block above

    def _upload_cgram(self) -> None:
        # HARDWARE: Upload all 8 custom characters to LCD CGRAM.
        #
        # RPLCD example:
        #   for idx, bitmap in enumerate(_CGRAM):
        #       self._lcd.create_char(idx, bitmap)
        #
        # pico-i2c-lcd example:
        #   for idx, bitmap in enumerate(_CGRAM):
        #       self._lcd.custom_char(idx, bytearray(bitmap))
        pass

    def update(self, actual_ml: float, expected_ml: float) -> None:
        """Render both values as big digits and write to the display."""
        a_top, a_mid, a_bot = _render_big_number(int(actual_ml),   width=4)
        e_top, e_mid, e_bot = _render_big_number(int(expected_ml), width=4)

        # Each half is 8 chars wide; 4-char gap in the middle (cols 8–11)
        sep = "  |  "[:4].ljust(4)  # a short visual separator, 4 chars

        row0 = a_top + sep + e_top   # 8 + 4 + 8 = 20
        row1 = a_mid + sep + e_mid
        row2 = a_bot + sep + e_bot

        row3 = " " * 20
        if self._show_time:
            import time as _time
            t = _time.localtime()
            clock = f"{t.tm_hour:02d}:{t.tm_min:02d}"
            row3 = clock.rjust(20)

        self._write_raw({0: row0, 1: row1, 2: row2, 3: row3})

    def _write_raw(self, rows: dict[int, str]) -> None:
        # HARDWARE: Write rows to the physical display.
        #
        # RPLCD example:
        #   for row_idx, text in rows.items():
        #       self._lcd.cursor_pos = (row_idx, 0)
        #       self._lcd.write_string(text[:20])
        #
        # pico-i2c-lcd example:
        #   for row_idx, text in rows.items():
        #       self._lcd.move_to(0, row_idx)
        #       self._lcd.putstr(text[:20])
        pass

    def _apply_dim(self, dimmed: bool) -> None:
        # HARDWARE: Toggle backlight.
        #
        # RPLCD:          self._lcd.backlight_enabled = not dimmed
        # pico-i2c-lcd:   self._lcd.backlight_on(not dimmed)
        pass


# ---------------------------------------------------------------------------
# Mock driver for tests
# ---------------------------------------------------------------------------

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

    def _write_raw(self, rows: dict[int, str]) -> None:
        pass  # no-op in mock

    def last_state(self) -> DisplayState | None:
        """Return the most recently written display state, or None."""
        return self._history[-1] if self._history else None

    def history(self) -> list[DisplayState]:
        """Return a copy of the full display state history."""
        return list(self._history)
