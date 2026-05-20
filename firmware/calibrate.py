"""Calibration helper: tare the load cell, then stream raw readings.

Run this once on a fresh sensor to confirm wiring and to read a known
reference mass so you can compute `CALIBRATION_FACTOR` for main.py:

    factor = raw_with_known_mass / known_mass_grams

After tare, place a calibration weight (e.g. a 100 g standard) on the
platform; the printed raw value divided by 100 is your factor.
"""

from machine import Pin
from hx711 import HX711
import time

# HX711 wiring: DT (data) on GP4, SCK (clock) on GP3.
pin_OUT = Pin(4, Pin.IN, pull=Pin.PULL_DOWN)
pin_SCK = Pin(3, Pin.OUT)

hx = HX711(pin_SCK, pin_OUT)

print("Remove all weight from the sensor...")
time.sleep(2)

hx.tare()
print("Tare done. Zero offset saved.")
print()

print("Place weight on the sensor. Raw values:")
while True:
    val = hx.get_value()
    print("Raw value:", val)
    time.sleep(0.5)
