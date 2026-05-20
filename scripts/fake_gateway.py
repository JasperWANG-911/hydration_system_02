"""Simulate one or more coaster gateways POSTing weight samples.

Stands in for the phone + Camgenium link during local development: each
device runs through repeating cycles of (cup placed, stable, removed,
returned-with-some-fluid-consumed) and POSTs the raw weight samples to
the backend's `/ingest/measurements` endpoint.

The classifier needs ~20 samples in its stability window before it
declares CUP_PRESENT_STABLE, so we sample at 10 Hz and hold stable
states for at least 3 seconds.

Usage:
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

SAMPLE_HZ = 10.0
SAMPLE_INTERVAL_S = 1.0 / SAMPLE_HZ
BATCH_SIZE = 10  # flush one POST per ~1s of samples


async def post_batch(
    client: httpx.AsyncClient, device_id: str, samples: list[dict]
) -> None:
    body = json.dumps({"device_id": device_id, "samples": samples}).encode()
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
        if result.get("drinks") or result.get("refills"):
            print(f"[{device_id}] {result}")
    except Exception as e:
        print(f"[{device_id}] POST failed: {e}")


def jitter(value: float, std: float = 0.4) -> float:
    return value + random.gauss(0, std)


class FakeCoaster:
    """One simulated coaster: tracks a target weight and emits samples."""

    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        self.cup_weight = 480.0  # full cup at start
        self.pending: list[dict] = []

    async def emit_sample(
        self, client: httpx.AsyncClient, weight: float, cup_present: bool
    ) -> None:
        self.pending.append(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "weight_g": round(max(weight, 0.0), 2),
                "cup_present": cup_present,
            }
        )
        if len(self.pending) >= BATCH_SIZE:
            batch, self.pending = self.pending, []
            await post_batch(client, self.device_id, batch)

    async def flush(self, client: httpx.AsyncClient) -> None:
        if self.pending:
            batch, self.pending = self.pending, []
            await post_batch(client, self.device_id, batch)

    async def hold(
        self,
        client: httpx.AsyncClient,
        target_weight: float,
        cup_present: bool,
        duration_s: float,
        noise_std: float = 0.4,
    ) -> None:
        steps = max(1, int(duration_s / SAMPLE_INTERVAL_S))
        for _ in range(steps):
            await self.emit_sample(
                client, jitter(target_weight, noise_std), cup_present
            )
            await asyncio.sleep(SAMPLE_INTERVAL_S)

    async def settle(
        self, client: httpx.AsyncClient, target_weight: float
    ) -> None:
        # Mechanical ringing after cup placement — large oscillation that
        # decays over ~1s.
        for offset in (-30, +20, -8, +3, -1):
            await self.hold(client, target_weight + offset, True, 0.2, noise_std=1.0)

    async def cycle(self, client: httpx.AsyncClient) -> None:
        await self.settle(client, self.cup_weight)
        await self.hold(client, self.cup_weight, True, random.uniform(3.0, 8.0))

        # Lift cup
        await self.hold(client, 0.0, False, random.uniform(2.0, 6.0))

        # Decide what happened off-platform
        roll = random.random()
        if roll < 0.7:
            self.cup_weight = max(self.cup_weight - random.randint(30, 150), 5.0)
        elif roll < 0.9:
            self.cup_weight = 480.0  # refill
        # else: cup just moved, no change

        await self.settle(client, self.cup_weight)
        await self.hold(client, self.cup_weight, True, random.uniform(3.0, 6.0))

        if self.cup_weight < 50:
            self.cup_weight = 480.0  # implicit refill when nearly empty


async def device_loop(client: httpx.AsyncClient, device_id: str) -> None:
    coaster = FakeCoaster(device_id)
    # Warm-up: empty platform long enough for the classifier to register NO_CUP
    await coaster.hold(client, 0.0, False, 3.0)
    await coaster.flush(client)
    while True:
        await coaster.cycle(client)
        await coaster.flush(client)


async def run(device_ids: list[str]) -> None:
    async with httpx.AsyncClient(timeout=10.0) as client:
        await asyncio.gather(*(device_loop(client, d) for d in device_ids))


if __name__ == "__main__":
    devices = sys.argv[1:] or DEFAULT_DEVICES
    print(f"posting to {INGEST_URL} for devices: {devices}")
    asyncio.run(run(devices))
