"""Simulates one or more devices publishing hydration events over MQTT.

Usage:
    python scripts/fake_device.py                  # all seeded devices
    python scripts/fake_device.py dev-001 dev-002  # specific devices
"""
import asyncio
import json
import os
import random
import sys
from datetime import datetime, timezone

import aiomqtt

MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
DEFAULT_DEVICES = ["dev-001", "dev-002", "dev-003", "dev-004", "dev-005"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def heartbeat_loop(client: aiomqtt.Client, device_id: str) -> None:
    while True:
        await client.publish(
            f"hydration/{device_id}/heartbeat",
            json.dumps({"ts": now_iso(), "battery": random.randint(60, 100)}),
        )
        await asyncio.sleep(30)


async def event_loop(client: aiomqtt.Client, device_id: str) -> None:
    while True:
        await asyncio.sleep(random.uniform(20, 90))
        roll = random.random()
        if roll < 0.65:
            event = {"type": "drink", "ts": now_iso(),
                     "delta_g": -random.randint(50, 250)}
        elif roll < 0.85:
            event = {"type": "refill", "ts": now_iso(),
                     "delta_g": random.randint(200, 500)}
        elif roll < 0.95:
            kind = random.choice(["removed", "placed"])
            event = {"type": kind, "ts": now_iso(), "delta_g": 0}
        else:
            event = {"type": "button", "ts": now_iso()}

        await client.publish(f"hydration/{device_id}/event", json.dumps(event))
        print(f"[{device_id}] {event}")


async def run(device_ids: list[str]) -> None:
    async with aiomqtt.Client(MQTT_HOST, MQTT_PORT) as client:
        tasks = []
        for dev in device_ids:
            tasks.append(asyncio.create_task(heartbeat_loop(client, dev)))
            tasks.append(asyncio.create_task(event_loop(client, dev)))
        await asyncio.gather(*tasks)


if __name__ == "__main__":
    devices = sys.argv[1:] or DEFAULT_DEVICES
    asyncio.run(run(devices))
