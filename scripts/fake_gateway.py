"""Simulate one or more DRIP button boxes POSTing intake events.

Stands in for the Pico W + Camgenium link during local development.
Each simulated device generates realistic drinking patterns throughout
a waking day and POSTs button press events to the backend's
``/ingest/measurements`` endpoint.

Usage::

    python scripts/fake_gateway.py                   # all seeded devices
    python scripts/fake_gateway.py dev-001 dev-002   # specific devices

Env vars:
    INGEST_URL              default http://localhost:8000/ingest/measurements
    INGEST_SHARED_SECRET    if set (and not 'change-me'), requests are HMAC-signed
"""

import asyncio
import hashlib
import hmac
import json
import os
import random
import sys
from datetime import datetime, timezone

import httpx

INGEST_URL = os.environ.get(
    "INGEST_URL", "http://localhost:8000/ingest/measurements"
)
SHARED_SECRET = os.environ.get("INGEST_SHARED_SECRET", "")
DEFAULT_DEVICES = ["dev-001", "dev-002", "dev-003", "dev-004", "dev-005"]

STEP_ML = 50
# Simulated drinking patterns: (mean_presses, std_presses, interval_s)
# Heavier at mealtimes, light otherwise.
_DRINK_PATTERNS = [
    (3, 1, 1800),   # small drink every 30 min
    (5, 2, 3600),   # larger drink at mealtimes (every hour)
]


async def post_event(
    client: httpx.AsyncClient, device_id: str, volume_ml: float
) -> None:
    payload = {
        "device_id": device_id,
        "intake_events": [
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "volume_ml": volume_ml,
            }
        ],
        "sleep_events": [],
    }
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if SHARED_SECRET and SHARED_SECRET != "change-me":
        sig = hmac.new(
            SHARED_SECRET.encode(), body, hashlib.sha256
        ).hexdigest()
        headers["X-Camgenium-Signature"] = sig
    try:
        r = await client.post(INGEST_URL, content=body, headers=headers)
        r.raise_for_status()
        result = r.json()
        print(f"[{device_id}] posted {volume_ml:.0f} ml → {result}")
    except Exception as e:
        print(f"[{device_id}] POST failed: {e}")


async def device_loop(client: httpx.AsyncClient, device_id: str) -> None:
    # Stagger device start times so they don't all fire at once.
    await asyncio.sleep(random.uniform(0, 5))

    while True:
        # Alternate between small and meal-sized drinks.
        pattern = random.choice(_DRINK_PATTERNS)
        mean_presses, std_presses, interval_s = pattern
        presses = max(1, int(random.gauss(mean_presses, std_presses)))
        volume_ml = presses * STEP_ML
        await post_event(client, device_id, float(volume_ml))
        await asyncio.sleep(interval_s + random.uniform(-300, 300))


async def run(device_ids: list[str]) -> None:
    async with httpx.AsyncClient(timeout=10.0) as client:
        await asyncio.gather(*(device_loop(client, d) for d in device_ids))


if __name__ == "__main__":
    devices = sys.argv[1:] or DEFAULT_DEVICES
    print(f"posting to {INGEST_URL} for devices: {devices}")
    asyncio.run(run(devices))
