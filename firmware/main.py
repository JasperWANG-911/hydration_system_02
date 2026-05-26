"""DRIP bedside unit firmware (MicroPython, Raspberry Pi Pico W).

One unit manages:
  - PLUS  button → record +STEP_ML intake
  - MINUS button → record -STEP_ML intake (net delta floored at 0)
  - SLEEP button → toggle nap / sleep mode (pace model pauses, display dims)
  - Cactus LED   → driven by alert threshold (local or backend-controlled)
  - I2C display  → shows actual intake vs. pace model expected
  - BLE          → transmits aggregated drink events to Camgenium relay

All GPIO pin numbers are at the top of this file — edit them to match
your physical wiring. No other file needs to change.

Aggregation window
------------------
Multiple button presses within AGGREGATION_WINDOW_MS are treated as one
drink event. The window starts on the first press and is committed when
no press has occurred for AGGREGATION_WINDOW_MS milliseconds. The net
delta (pluses minus minuses) is what gets transmitted and counted.
"""

import time
from machine import Pin

from ble_transport import BleTransport

# ---- GPIO pin assignments (edit to match wiring) -------------------------
PLUS_PIN     = 14   # + button: pull-up, active LOW
MINUS_PIN    = 15   # - button: pull-up, active LOW
SLEEP_PIN    = 16   # sleep/wake toggle: pull-up, active LOW
LED_PIN      = 18   # cactus LED data pin (NeoPixel / PWM)
I2C_SDA_PIN  = 4    # display I2C SDA
I2C_SCL_PIN  = 5    # display I2C SCL
I2C_ADDRESS  = 0x27 # I2C display address (PCF8574: 0x27 or 0x3F)

# ---- Hydration parameters ------------------------------------------------
STEP_ML               = 50      # ml credited per button press
AGGREGATION_WINDOW_MS = 15_000  # presses within this window form one event
DAILY_GOAL_ML         = 2000    # ml target (update per patient via backend)

# ---- Pace model ----------------------------------------------------------
ACTIVE_DAY_HOURS  = 16.0        # assumed waking hours per day (linear model)
PACE_REFRESH_MS   = 30 * 60 * 1000  # refresh display every 30 minutes

# ---- BLE identity --------------------------------------------------------
DEVICE_NAME = "DRIP-001"        # edit per unit; must be unique per bed

# ---- Debounce ------------------------------------------------------------
DEBOUNCE_MS = 300

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

_total_ml: float = 0.0
_sleeping: bool = False

_session_start_ms: int = 0
_pause_start_ms: int = 0
_total_paused_ms: int = 0
_in_pause: bool = False

# Aggregation window
_pending_delta_ml: float = 0.0
_window_start_ms: int = 0
_window_open: bool = False
_press_count: int = 0

# Debounce tracking
_last_press_ms = [0, 0, 0]  # [plus, minus, sleep]


# ---------------------------------------------------------------------------
# Pace model (linear)
# ---------------------------------------------------------------------------

def _active_elapsed_s() -> float:
    now_ms = time.ticks_ms()
    total_ms = time.ticks_diff(now_ms, _session_start_ms) - _total_paused_ms
    if _in_pause:
        total_ms -= time.ticks_diff(now_ms, _pause_start_ms)
    return max(0, total_ms) / 1000.0


def _expected_ml() -> int:
    active_day_s = ACTIVE_DAY_HOURS * 3600.0
    fraction = min(_active_elapsed_s() / active_day_s, 1.0)
    return int(fraction * DAILY_GOAL_ML)


# ---------------------------------------------------------------------------
# Display (I2C — stub, fill in HARDWARE blocks)
# ---------------------------------------------------------------------------

def display_update(actual_ml: float, expected_ml: int) -> None:
    # HARDWARE: Write two lines to your I2C display.
    #
    # Example for LCD1602 via pico-i2c-lcd library:
    #   lcd.clear()
    #   lcd.putstr(f"Drank:  {int(actual_ml):>5}ml")
    #   lcd.move_to(0, 1)
    #   lcd.putstr(f"Expect: {expected_ml:>5}ml")
    #
    # Print to REPL during development:
    print(f"[DISPLAY] Drank: {int(actual_ml):>5} ml  |  Expect: {expected_ml:>5} ml")


def display_dim() -> None:
    # HARDWARE: Reduce backlight (e.g. lcd.backlight_enabled = False or contrast 30).
    pass


def display_wake() -> None:
    # HARDWARE: Restore full backlight.
    pass


# ---------------------------------------------------------------------------
# Aggregation window
# ---------------------------------------------------------------------------

def _maybe_commit(now_ms: int) -> None:
    global _pending_delta_ml, _window_open, _press_count, _total_ml
    if not _window_open:
        return
    if time.ticks_diff(now_ms, _window_start_ms) < AGGREGATION_WINDOW_MS:
        return
    if _pending_delta_ml > 0:
        _total_ml += _pending_delta_ml
        ble.transmit_intake(_pending_delta_ml, _window_start_ms)
    _pending_delta_ml = 0.0
    _window_open = False
    _press_count = 0


def _force_commit(now_ms: int) -> None:
    global _pending_delta_ml, _window_open, _press_count, _total_ml
    if not _window_open:
        return
    if _pending_delta_ml > 0:
        _total_ml += _pending_delta_ml
        ble.transmit_intake(_pending_delta_ml, _window_start_ms)
    _pending_delta_ml = 0.0
    _window_open = False
    _press_count = 0


# ---------------------------------------------------------------------------
# Button actions
# ---------------------------------------------------------------------------

def _record_plus(now_ms: int) -> None:
    global _window_open, _window_start_ms, _pending_delta_ml, _press_count
    _maybe_commit(now_ms)
    if not _window_open:
        _window_start_ms = now_ms
        _window_open = True
    _pending_delta_ml += STEP_ML
    _press_count += 1
    display_update(_total_ml + _pending_delta_ml, _expected_ml())


def _record_minus(now_ms: int) -> None:
    global _pending_delta_ml, _press_count
    if not _window_open:
        return  # nothing to subtract
    _maybe_commit(now_ms)
    _pending_delta_ml = max(0.0, _pending_delta_ml - STEP_ML)
    _press_count += 1
    display_update(_total_ml + _pending_delta_ml, _expected_ml())


def _toggle_sleep(now_ms: int) -> None:
    global _sleeping, _in_pause, _pause_start_ms, _total_paused_ms
    _force_commit(now_ms)
    _sleeping = not _sleeping
    if _sleeping:
        _in_pause = True
        _pause_start_ms = now_ms
        display_dim()
        ble.transmit_sleep_start(now_ms)
    else:
        if _in_pause:
            _total_paused_ms += time.ticks_diff(now_ms, _pause_start_ms)
            _in_pause = False
        display_wake()
        ble.transmit_sleep_end(now_ms)
        display_update(_total_ml, _expected_ml())


# ---------------------------------------------------------------------------
# Interrupt handlers
# ---------------------------------------------------------------------------

def _on_plus(pin) -> None:
    now_ms = time.ticks_ms()
    if time.ticks_diff(now_ms, _last_press_ms[0]) < DEBOUNCE_MS:
        return
    _last_press_ms[0] = now_ms
    if not _sleeping:
        _record_plus(now_ms)


def _on_minus(pin) -> None:
    now_ms = time.ticks_ms()
    if time.ticks_diff(now_ms, _last_press_ms[1]) < DEBOUNCE_MS:
        return
    _last_press_ms[1] = now_ms
    if not _sleeping:
        _record_minus(now_ms)


def _on_sleep(pin) -> None:
    now_ms = time.ticks_ms()
    if time.ticks_diff(now_ms, _last_press_ms[2]) < DEBOUNCE_MS:
        return
    _last_press_ms[2] = now_ms
    _toggle_sleep(now_ms)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    global ble, _session_start_ms

    btn_plus  = Pin(PLUS_PIN,  Pin.IN, Pin.PULL_UP)
    btn_minus = Pin(MINUS_PIN, Pin.IN, Pin.PULL_UP)
    btn_sleep = Pin(SLEEP_PIN, Pin.IN, Pin.PULL_UP)

    btn_plus.irq(trigger=Pin.IRQ_FALLING,  handler=_on_plus)
    btn_minus.irq(trigger=Pin.IRQ_FALLING, handler=_on_minus)
    btn_sleep.irq(trigger=Pin.IRQ_FALLING, handler=_on_sleep)

    ble = BleTransport(DEVICE_NAME)
    ble.start()

    _session_start_ms = time.ticks_ms()
    display_wake()
    display_update(0, 0)

    last_pace_refresh_ms = time.ticks_ms()

    while True:
        now_ms = time.ticks_ms()

        # Commit aggregation window if it has expired.
        _maybe_commit(now_ms)

        # Refresh pace display on schedule.
        if time.ticks_diff(now_ms, last_pace_refresh_ms) >= PACE_REFRESH_MS:
            if not _sleeping:
                display_update(_total_ml, _expected_ml())
            last_pace_refresh_ms = now_ms

        time.sleep_ms(100)


if __name__ == "__main__":
    main()
