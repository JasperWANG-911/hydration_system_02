from machine import Pin
from measurment.hx711_pio import HX711
import time

# Set up the HX711 on GP12 (data) and GP13 (clock)
pin_OUT = Pin(4, Pin.IN, pull=Pin.PULL_DOWN)
pin_SCK = Pin(3, Pin.OUT)

hx = HX711(pin_SCK, pin_OUT)

# Step 1: Read raw values with nothing on the sensor
print("Remove all weight from the sensor...")
time.sleep(2)

# Tare (set current reading as zero)
hx.tare()
print("Tare done. Zero offset saved.")
print()

# Step 2: Continuously read and print values
print("Place weight on the sensor. Raw values:")
while True:
    val = hx.get_value()
    print("Raw value:", val)
    time.sleep(0.5)