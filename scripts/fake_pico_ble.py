"""Pretend to be a Pico over BLE — connect to the phone running Pico
Relay and write filler bytes into the GATT characteristic so Camgenium
sees 'data' from this agent and (hopefully) auto-creates an instrument
on its side.

We deliberately don't care about the on-the-wire frame format at this
stage. The goal here is just:

    1. Confirm the phone accepts writes from a BLE central.
    2. Trigger whatever instrument-registration flow Camgenium has, so
       we can grab a real instrument ID and subscribe to it via webhook.

Real frame layout comes later, once we know the BLE characteristic +
Pico SDK contract.

Requires:  pip install bleak

Usage:
    # Make sure on the phone:
    #   - Pico Relay is open
    #   - NBA Status is "Connected" (green dot)
    #   - You've tapped Start under "BLE Peripheral" so the device is
    #     advertising as 'L2S2R-...'
    python scripts/fake_pico_ble.py
"""
import asyncio
import struct
import sys
import time

from bleak import BleakClient, BleakScanner

DEVICE_NAME_PREFIX = "L2S2R-"
SERVICE_UUID = "8a3e4d2f-1b6c-4f9e-a7d8-3e5b2c1f4a01"
MTU_BYTES = 16
SAMPLE_INTERVAL_S = 1.0


async def find_phone():
    print(f"scanning 10s for BLE peripheral starting with '{DEVICE_NAME_PREFIX}'...")
    devices = await BleakScanner.discover(timeout=10.0)
    candidates = [
        d for d in devices if d.name and d.name.startswith(DEVICE_NAME_PREFIX)
    ]
    if not candidates:
        print()
        print("nothing matched. checklist:")
        print("  - phone screen on and unlocked")
        print("  - Pico Relay open, BLE Peripheral 'started' (tapped Start)")
        print("  - this laptop's bluetooth is on")
        print("  - (mac) terminal has Bluetooth permission in System Settings")
        print()
        print("scan saw these BLE devices:")
        for d in devices:
            print(f"  {d.name or '<no name>'}  {d.address}")
        sys.exit(1)
    if len(candidates) > 1:
        print(f"multiple matches: {[d.name for d in candidates]}; picking first")
    chosen = candidates[0]
    print(f"found: {chosen.name} @ {chosen.address}")
    return chosen


def pick_writable_char(client):
    """Find any characteristic on SERVICE_UUID that we can write to."""
    print()
    print("services discovered on peripheral:")
    target = None
    target_char = None
    for service in client.services:
        print(f"  service {service.uuid}")
        for char in service.characteristics:
            print(f"    char {char.uuid}  props={char.properties}")
            if service.uuid.lower() == SERVICE_UUID.lower():
                if "write" in char.properties or "write-without-response" in char.properties:
                    if target_char is None:
                        target = service
                        target_char = char
    print()
    if target_char is None:
        raise RuntimeError(
            f"no writable characteristic under service {SERVICE_UUID}"
        )
    print(
        f"will write to service={target.uuid}  char={target_char.uuid}  "
        f"props={target_char.properties}"
    )
    return target_char


async def push():
    phone = await find_phone()
    print("connecting...")
    async with BleakClient(phone) as client:
        char = pick_writable_char(client)
        # Use write-with-response (ack-based). On macOS,
        # write-without-response is unreliable: writes succeed locally
        # but may be silently dropped before reaching the peripheral.
        use_response = "write" in char.properties
        print(f"write mode: response={use_response}")
        i = 0
        while True:
            # Arbitrary 16-byte filler. The first 2 bytes are a counter
            # so we can verify on the phone side that bytes are arriving;
            # the rest is a timestamp + zeros. Real frame format is TBD.
            payload = struct.pack(
                "<HHIQ",
                i & 0xFFFF,
                0x4242,
                int(time.time()),
                0,
            )[:MTU_BYTES]
            try:
                await client.write_gatt_char(char, payload, response=use_response)
            except Exception as e:
                print(f"write failed at frame {i}: {e}")
                break
            print(f"sent frame {i:>4}  ({len(payload)} bytes)  ack={use_response}")
            i += 1
            await asyncio.sleep(SAMPLE_INTERVAL_S)


if __name__ == "__main__":
    try:
        asyncio.run(push())
    except KeyboardInterrupt:
        print("\nstopped")
