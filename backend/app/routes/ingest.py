"""HTTP ingest endpoint.

Camgenium (in production) and `scripts/fake_gateway.py` (in development)
both POST raw weight samples here. The endpoint:

1. Verifies an HMAC-SHA256 signature against the shared secret.
2. Persists every raw sample to the `measurements` hypertable so the
   classifier output can always be recomputed from source.
3. Feeds each sample through the per-device classifier + session in
   `app.interactions.registry` to derive drink / refill events.
4. Writes those events into the `events` table (with intake_delta_ml
   filled in for drinks) so downstream alert rules and dashboards see
   the same shape they always did.
5. Updates `devices.last_seen` and publishes a pub/sub update so SSE
   subscribers refresh.
"""

import hashlib
import hmac
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.events import update_device_heartbeat
from app.interactions import DrinkEvent, RefillEvent, registry
from app.models import Event, Measurement
from app.pubsub import broker

log = logging.getLogger(__name__)

router = APIRouter(prefix="/ingest")


class Sample(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ts: datetime
    weight_g: float
    cup_present: bool | None = None


class IngestPayload(BaseModel):
    """Normalised body POSTed to /ingest/measurements.

    Accepts either `device_id` (our own seed / dev format) or
    `instrumentIdentifier` (what Camgenium's Harvester API uses).
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    device_id: str = Field(validation_alias="instrumentIdentifier")
    samples: list[Sample]


def _verify_signature(raw_body: bytes, signature_header: str | None) -> None:
    """Reject the request if the HMAC signature is missing or doesn't match.

    Skipped entirely when `INGEST_SHARED_SECRET` is unset or still the
    placeholder so local dev against `fake_gateway.py` works without
    ceremony.
    """
    secret = settings.ingest_shared_secret
    if not secret or secret == "change-me":
        return
    if not signature_header:
        raise HTTPException(401, "missing signature")
    expected = hmac.new(
        secret.encode(), raw_body, hashlib.sha256
    ).hexdigest()
    provided = signature_header.removeprefix("sha256=").strip()
    if not hmac.compare_digest(expected, provided):
        raise HTTPException(401, "bad signature")


def _ensure_utc(ts: datetime) -> datetime:
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)


async def _persist_measurements(
    session: AsyncSession,
    device_id: str,
    samples: list[Sample],
) -> None:
    rows = [
        {
            "ts": _ensure_utc(s.ts),
            "device_id": device_id,
            "weight_g": s.weight_g,
            "cup_present": s.cup_present,
        }
        for s in samples
    ]
    # ON CONFLICT DO NOTHING — duplicates can arise when Camgenium retries
    # webhook deliveries; idempotency keeps the hypertable clean.
    stmt = pg_insert(Measurement).values(rows).on_conflict_do_nothing(
        index_elements=["ts", "device_id"]
    )
    await session.execute(stmt)


def _drink_to_event_row(device_id: str, drink: DrinkEvent) -> dict:
    return {
        "ts": datetime.fromtimestamp(drink.timestamp, tz=timezone.utc),
        "device_id": device_id,
        "type": "drink",
        "payload": {
            "volume_ml": drink.volume_ml,
            "raw_net_change_g": drink.raw_net_change_g,
            "confidence": drink.confidence,
        },
        "intake_delta_ml": int(round(drink.volume_ml)),
    }


def _refill_to_event_row(device_id: str, refill: RefillEvent) -> dict:
    return {
        "ts": datetime.fromtimestamp(refill.timestamp, tz=timezone.utc),
        "device_id": device_id,
        "type": "refill",
        "payload": {
            "volume_added_ml": refill.volume_added_ml,
            "raw_net_change_g": refill.raw_net_change_g,
            "confidence": refill.confidence,
        },
        "intake_delta_ml": None,
    }


async def _persist_events(
    session: AsyncSession,
    device_id: str,
    drinks: list[DrinkEvent],
    refills: list[RefillEvent],
) -> None:
    rows = [_drink_to_event_row(device_id, d) for d in drinks]
    rows.extend(_refill_to_event_row(device_id, r) for r in refills)
    if not rows:
        return
    stmt = pg_insert(Event).values(rows).on_conflict_do_nothing(
        index_elements=["ts", "device_id"]
    )
    await session.execute(stmt)


@router.post("/measurements", status_code=202)
async def ingest_measurements(
    request: Request,
    x_camgenium_signature: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
):
    raw = await request.body()
    _verify_signature(raw, x_camgenium_signature)

    try:
        payload = IngestPayload.model_validate_json(raw)
    except ValueError as e:
        raise HTTPException(400, f"invalid payload: {e}") from e

    if not payload.samples:
        return {"accepted": 0, "drinks": 0, "refills": 0}

    samples = sorted(payload.samples, key=lambda s: s.ts)
    device_id = payload.device_id
    runner = registry.get(device_id)

    for sample in samples:
        runner.process_sample(sample.weight_g, _ensure_utc(sample.ts).timestamp())
    drinks, refills, faults = runner.drain()

    await _persist_measurements(session, device_id, samples)
    await _persist_events(session, device_id, drinks, refills)
    await update_device_heartbeat(
        session, device_id, _ensure_utc(samples[-1].ts)
    )
    await session.commit()

    for _ in drinks:
        await broker.publish(
            {"kind": "event", "device_id": device_id, "type": "drink"}
        )
    for _ in refills:
        await broker.publish(
            {"kind": "event", "device_id": device_id, "type": "refill"}
        )

    if faults:
        log.warning(
            "sensor fault(s) for device=%s: %d in batch", device_id, len(faults)
        )

    return {
        "accepted": len(samples),
        "drinks": len(drinks),
        "refills": len(refills),
    }
