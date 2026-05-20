"""Coaster firmware entry point (MicroPython on Raspberry Pi Pico).

Responsibilities:
    1. Sample the HX711 load cell at SAMPLE_HZ.
    2. Convert raw counts to grams using CALIBRATION_FACTOR (set via the
       procedure in firmware/calibrate.py).
    3. Hand each sample off to `transmit(sample)`. The backend's
       classifier is responsible for all interaction inference (cup
       removed / returned / drink / refill); this firmware emits raw
       weights only — keeping the MCU dumb means thresholds can be
       tuned in the backend without reflashing.

BLE transport status:
    The base Raspberry Pi Pico (RP2040, no radio) cannot transmit over
    BLE on its own. Until the hardware is finalised — either swapping
    to a Pico W (RP2040 + CYW43439) or wiring an external BLE module
    (HM-10, nRF52, ...) over UART — `transmit()` just prints to stdout.
    Replace its body with the BLE-specific publish call once decided.
"""

import time

from machine import Pin

from hx711 import HX711

# ---- Wiring ----------------------------------------------------------------
DATA_PIN = 4
CLOCK_PIN = 3

# ---- Calibration -----------------------------------------------------------
# raw_counts_at_known_mass / known_mass_grams. See firmware/calibrate.py.
CALIBRATION_FACTOR = 1.0  # PLACEHOLDER — replace after calibration.

# ---- Sampling --------------------------------------------------------------
SAMPLE_HZ = 10
SAMPLE_INTERVAL_MS = 1000 // SAMPLE_HZ
EMPTY_THRESHOLD_G = 15.0


def init_sensor() -> HX711:
    pin_data = Pin(DATA_PIN, Pin.IN, pull=Pin.PULL_DOWN)
    pin_clock = Pin(CLOCK_PIN, Pin.OUT)
    hx = HX711(pin_clock, pin_data)
    print("Taring — keep platform empty...")
    time.sleep(2)
    hx.tare()
    print("Tare complete.")
    return hx


def raw_to_grams(raw: float) -> float:
    if CALIBRATION_FACTOR == 0:
        return 0.0
    return raw / CALIBRATION_FACTOR


def transmit(sample: dict) -> None:
    """Send one sample over the gateway link.

    TODO(hardware): replace with a BLE GATT notify once the BLE path
    (Pico W internal radio or external module over UART) is wired up.
    The sample dict matches the payload field shape expected by
    backend.app.routes.ingest.
    """
    print(sample)


def main() -> None:
    hx = init_sensor()
    while True:
        raw = hx.get_value()
        grams = raw_to_grams(raw)
        sample = {
            "weight_g": round(grams, 2),
            "cup_present": grams > EMPTY_THRESHOLD_G,
            "ts_ms": time.ticks_ms(),
        }
        transmit(sample)
        time.sleep_ms(SAMPLE_INTERVAL_MS)


if __name__ == "__main__":
    main()
