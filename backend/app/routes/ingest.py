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
import json
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
from app.models import Bed, Device, Event, Measurement
from app.pubsub import broker, publish_priority_snapshot

log = logging.getLogger(__name__)

router = APIRouter(prefix="/ingest")


class Sample(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ts: datetime
    weight_g: float
    cup_present: bool | None = None


class IngestPayload(BaseModel):
    """Internal payload format used by `scripts/fake_gateway.py`."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    device_id: str = Field(validation_alias="instrumentIdentifier")
    samples: list[Sample]


class CamgeniumDataRecord(BaseModel):
    """One per-packet record inside a Camgenium webhook delivery."""

    model_config = ConfigDict(extra="ignore")

    dataValue: str  # base64-encoded raw BLE frame
    timestamp: datetime
    packetType: int = 0


class CamgeniumWebhookPayload(BaseModel):
    """Shape Camgenium actually sends. Discovered by logging the raw body.

    Differs from `IngestPayload` in three meaningful ways:
      - field name `instrumentIdentifier` (singular, not array, despite
        the schema docs being misleading)
      - records are nested under `data`, not `samples`
      - each record's payload is a base64 binary `dataValue`, not a
        decoded weight reading
    """

    model_config = ConfigDict(extra="ignore")

    webhookId: str
    instrumentIdentifier: str
    timestamp: datetime
    data: list[CamgeniumDataRecord]


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


async def _ensure_device_for_camgenium(
    session: AsyncSession, instrument_id: str
) -> None:
    """Auto-create a bed + device row for an unfamiliar Camgenium instrument.

    `devices.bed_id` is NOT NULL FK so we synthesise a placeholder bed
    under "Unassigned" ward. Nurses can re-assign via the admin UI later.
    """
    device = await session.get(Device, instrument_id)
    if device is not None:
        return
    bed_id = f"CG-{instrument_id[:8]}"
    if await session.get(Bed, bed_id) is None:
        session.add(
            Bed(
                bed_id=bed_id,
                ward="Unassigned",
                room="-",
                label=f"Camgenium {instrument_id[:8]}",
            )
        )
        await session.flush()
    session.add(Device(device_id=instrument_id, bed_id=bed_id))
    await session.flush()


async def _ingest_camgenium(
    cg: "CamgeniumWebhookPayload",
    session: AsyncSession,
) -> dict:
    """Handle a webhook delivery from Camgenium.

    Each `data[]` record carries a base64-encoded raw BLE frame in
    `dataValue`. We don't decode that here yet (the frame format is
    still TBD with the supervisor). For now we persist one placeholder
    measurement per record so `devices.last_seen` and the dashboard
    reflect that data is flowing.
    """
    instrument_id = cg.instrumentIdentifier
    await _ensure_device_for_camgenium(session, instrument_id)

    rows = [
        {
            "ts": _ensure_utc(r.timestamp),
            "device_id": instrument_id,
            "weight_g": 0.0,
            "cup_present": None,
        }
        for r in cg.data
    ]
    if rows:
        stmt = pg_insert(Measurement).values(rows).on_conflict_do_nothing(
            index_elements=["ts", "device_id"]
        )
        await session.execute(stmt)
        latest_ts = max(_ensure_utc(r.timestamp) for r in cg.data)
        await update_device_heartbeat(session, instrument_id, latest_ts)

    await session.commit()
    await broker.publish({"kind": "event", "device_id": instrument_id, "type": "camgenium"})

    return {
        "accepted": len(cg.data),
        "instrumentIdentifier": instrument_id,
        "note": "stored as placeholder measurements; dataValue decoding TBD",
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

    log.info(
        "ingest body (%d bytes): %s",
        len(raw),
        raw[:2000].decode("utf-8", errors="replace"),
    )

    # Camgenium pings the URL with {"test": true, ...} when registering
    # a webhook to verify reachability. Respond 200 to that and bail.
    try:
        peek = json.loads(raw)
        if isinstance(peek, dict) and peek.get("test") is True:
            return {"accepted": 0, "test": True}
    except (ValueError, json.JSONDecodeError):
        peek = None

    # Try the Camgenium webhook shape first — if it matches, route to a
    # different handler that knows how to peel out the `data[]` records.
    if isinstance(peek, dict) and "instrumentIdentifier" in peek and "data" in peek:
        try:
            cg = CamgeniumWebhookPayload.model_validate(peek)
        except ValueError as e:
            raise HTTPException(400, f"invalid Camgenium payload: {e}") from e
        return await _ingest_camgenium(cg, session)

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

    # Refresh the corridor display. Cheap (5-15 row query, fan-out to
    # whoever is on /sse). Doing it after commit means subscribers see
    # the post-write state.
    if drinks or refills:
        await publish_priority_snapshot(session)

    if faults:
        log.warning(
            "sensor fault(s) for device=%s: %d in batch", device_id, len(faults)
        )

    return {
        "accepted": len(samples),
        "drinks": len(drinks),
        "refills": len(refills),
    }
