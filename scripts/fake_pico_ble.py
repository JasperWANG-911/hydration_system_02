"""Simulate a DRIP Pico W advertising BLE events over the Camgenium relay.

Connects to the phone running Pico Relay via BLE (using bleak), then
writes DRIP-formatted frames into the writable GATT characteristic.
The Camgenium relay should pick these up and forward them to the backend
webhook as base64-encoded ``dataValue`` fields.

Frames use the shared ``protocol`` package: an intake event encoded with
``event_codec`` and wrapped in a SOLO frame with ``framing`` — exactly
what firmware/ble_transport.py builds and what the backend ingest
endpoint decodes.

Usage::

    # On the phone: open Pico Relay, tap Start under BLE Peripheral.
    python scripts/fake_pico_ble.py

Requires::

    pip install bleak
"""

import asyncio
import os
import random
import struct
import sys
import time

from bleak import BleakClient, BleakScanner

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from protocol.event_codec import encode, intake  # noqa: E402
from protocol.framing import build_frame  # noqa: E402

DEVICE_NAME_PREFIX = "L2S2R-"
SERVICE_UUID = "8a3e4d2f-1b6c-4f9e-a7d8-3e5b2c1f4a01"

# Legacy raw event type codes (sleep path only; intake uses event_codec).
EVT_SLEEP_START = 0x02
EVT_SLEEP_END   = 0x03

STEP_ML = 50
DRINK_INTERVAL_S = 1800  # simulate a drink every 30 min

_msg_id = 0


def _next_msg_id() -> int:
    global _msg_id
    _msg_id = (_msg_id + 1) & 0xFF
    return _msg_id


def _build_intake_frame(volume_ml: int) -> bytes:
    return build_frame(encode(intake(volume_ml)), _next_msg_id())


def _build_legacy_frame(event_type: int, volume_ml: int) -> bytes:
    ts_ms = int(time.time() * 1000) & 0xFFFFFFFF
    return struct.pack("<BHI", event_type, volume_ml & 0xFFFF, ts_ms)


async def find_relay():
    print(f"scanning 10s for BLE peripheral starting with '{DEVICE_NAME_PREFIX}'...")
    devices = await BleakScanner.discover(timeout=10.0)
    candidates = [
        d for d in devices if d.name and d.name.startswith(DEVICE_NAME_PREFIX)
    ]
    if not candidates:
        print("\nnothing matched. checklist:")
        print("  - phone screen on and unlocked")
        print("  - Pico Relay open, BLE Peripheral 'started' (tapped Start)")
        print("  - this laptop's bluetooth is on")
        print("\nscan saw these BLE devices:")
        for d in devices:
            print(f"  {d.name or '<no name>'}  {d.address}")
        sys.exit(1)
    if len(candidates) > 1:
        print(f"multiple matches: {[d.name for d in candidates]}; picking first")
    chosen = candidates[0]
    print(f"found: {chosen.name} @ {chosen.address}")
    return chosen


def pick_writable_char(client):
    print("\nservices discovered on peripheral:")
    target_char = None
    for service in client.services:
        print(f"  service {service.uuid}")
        for char in service.characteristics:
            print(f"    char {char.uuid}  props={char.properties}")
            if service.uuid.lower() == SERVICE_UUID.lower():
                if "write" in char.properties or "write-without-response" in char.properties:
                    if target_char is None:
                        target_char = char
    print()
    if target_char is None:
        raise RuntimeError(f"no writable characteristic under service {SERVICE_UUID}")
    print(f"will write to char={target_char.uuid}  props={target_char.properties}")
    return target_char


async def push():
    relay = await find_relay()
    print("connecting...")
    async with BleakClient(relay) as client:
        char = pick_writable_char(client)
        use_response = "write" in char.properties
        print(f"write mode: response={use_response}\n")

        event_count = 0
        while True:
            # Simulate a drink: 2-5 presses worth
            presses = random.randint(2, 5)
            volume_ml = presses * STEP_ML
            frame = _build_intake_frame(volume_ml)

            try:
                await client.write_gatt_char(char, frame, response=use_response)
            except Exception as e:
                print(f"write failed at event {event_count}: {e}")
                break

            print(
                f"[{event_count:>4}] intake  {volume_ml:>4} ml  "
                f"frame={frame.hex()}  ack={use_response}"
            )
            event_count += 1

            # Occasionally simulate a sleep toggle
            if random.random() < 0.05:
                sleep_frame = _build_legacy_frame(EVT_SLEEP_START, 0)
                await client.write_gatt_char(char, sleep_frame, response=use_response)
                print(f"[{event_count:>4}] sleep_start  frame={sleep_frame.hex()}")
                await asyncio.sleep(5)
                wake_frame = _build_legacy_frame(EVT_SLEEP_END, 0)
                await client.write_gatt_char(char, wake_frame, response=use_response)
                print(f"[{event_count:>4}] sleep_end    frame={wake_frame.hex()}")
                event_count += 2

            await asyncio.sleep(DRINK_INTERVAL_S + random.uniform(-60, 60))


if __name__ == "__main__":
    try:
        asyncio.run(push())
    except KeyboardInterrupt:
        print("\nstopped")
