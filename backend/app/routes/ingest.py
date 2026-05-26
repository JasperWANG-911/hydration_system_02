"""HTTP ingest endpoint.

Camgenium (in production) and ``scripts/fake_gateway.py`` (in
development) both POST button events here. The endpoint:

1. Verifies an HMAC-SHA256 signature against the shared secret.
2. Persists each intake event to the ``events`` table via the per-device
   session in ``app.interactions.registry``.
3. Updates ``devices.last_seen`` and publishes a pub/sub update so SSE
   subscribers refresh.

Payload shapes accepted
-----------------------
- **DrinkPayload** (``fake_gateway.py`` and direct Pico W WiFi fallback):
  ``{"device_id": "...", "intake_events": [...], "sleep_events": [...]}``
- **CamgeniumWebhookPayload** (production BLE path via Camgenium relay):
  ``{"webhookId": "...", "instrumentIdentifier": "...", "data": [...]}``
  Each ``data[].dataValue`` is a base64-encoded DRIP BLE frame.
  Frame decoding is implemented in :func:`_decode_ble_frame`.
"""

import base64
import hashlib
import hmac
import json
import logging
import struct
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.events import update_device_heartbeat
from app.interactions import DrinkEvent, registry
from app.models import Bed, Device, Event
from app.pubsub import broker, publish_priority_snapshot

log = logging.getLogger(__name__)

router = APIRouter(prefix="/ingest")

# BLE frame event type codes — must match firmware/ble_transport.py
_EVT_INTAKE = 0x01
_EVT_SLEEP_START = 0x02
_EVT_SLEEP_END = 0x03


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class IntakeEventPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ts: datetime
    volume_ml: float


class SleepEventPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ts: datetime
    event_type: Literal["sleep_start", "sleep_end"]


class DrinkPayload(BaseModel):
    """Shape used by ``scripts/fake_gateway.py`` and direct HTTP fallback."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    device_id: str = Field(validation_alias="instrumentIdentifier")
    intake_events: list[IntakeEventPayload] = []
    sleep_events: list[SleepEventPayload] = []


class CamgeniumDataRecord(BaseModel):
    """One per-packet record inside a Camgenium webhook delivery."""

    model_config = ConfigDict(extra="ignore")

    dataValue: str      # base64-encoded raw BLE frame
    timestamp: datetime
    packetType: int = 0


class CamgeniumWebhookPayload(BaseModel):
    """Shape Camgenium sends when forwarding BLE data from the relay."""

    model_config = ConfigDict(extra="ignore")

    webhookId: str
    instrumentIdentifier: str
    timestamp: datetime
    data: list[CamgeniumDataRecord]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _verify_signature(raw_body: bytes, signature_header: str | None) -> None:
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


def _decode_ble_frame(data_value: str) -> tuple[int, float, int] | None:
    """
    Decode a base64-encoded DRIP BLE frame from a Camgenium dataValue.

    Frame format (7 bytes, little-endian):
        byte 0:    event type  (0x01=intake, 0x02=sleep_start, 0x03=sleep_end)
        bytes 1-2: volume_ml   (uint16; 0 for sleep events)
        bytes 3-6: ts_ms       (uint32, device ticks_ms at time of event)

    Returns:
        Tuple of (event_type, volume_ml, ts_ms), or None if the frame
        cannot be decoded (wrong length, corrupt base64, etc.).
    """
    try:
        raw = base64.b64decode(data_value)
        if len(raw) < 7:
            return None
        event_type, volume_ml, ts_ms = struct.unpack_from("<BHI", raw)
        return event_type, float(volume_ml), ts_ms
    except Exception:
        return None


def _drink_to_event_row(device_id: str, drink: DrinkEvent) -> dict:
    return {
        "ts": datetime.fromtimestamp(drink.timestamp, tz=timezone.utc),
        "device_id": device_id,
        "type": "drink",
        "payload": {"volume_ml": drink.volume_ml},
        "intake_delta_ml": int(round(drink.volume_ml)),
    }


async def _ensure_device(session: AsyncSession, instrument_id: str) -> None:
    """Auto-create a bed + device row for an unfamiliar instrument."""
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
                label=f"DRIP {instrument_id[:8]}",
            )
        )
        await session.flush()
    session.add(Device(device_id=instrument_id, bed_id=bed_id))
    await session.flush()


async def _persist_events(
    session: AsyncSession,
    device_id: str,
    drinks: list[DrinkEvent],
) -> None:
    rows = [_drink_to_event_row(device_id, d) for d in drinks]
    if not rows:
        return
    stmt = pg_insert(Event).values(rows).on_conflict_do_nothing(
        index_elements=["ts", "device_id"]
    )
    await session.execute(stmt)


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

async def _ingest_camgenium(
    cg: CamgeniumWebhookPayload,
    session: AsyncSession,
) -> dict:
    """Handle a webhook delivery from the Camgenium relay.

    Each ``data[]`` record carries a base64-encoded DRIP BLE frame in
    ``dataValue``. Frames are decoded and dispatched as intake or sleep
    events. Unknown or malformed frames are logged and skipped.
    """
    instrument_id = cg.instrumentIdentifier
    await _ensure_device(session, instrument_id)
    runner = registry.get(instrument_id)

    accepted_intake = 0
    drinks_produced: list[DrinkEvent] = []

    for record in cg.data:
        decoded = _decode_ble_frame(record.dataValue)
        if decoded is None:
            log.warning(
                "Skipping undecodable BLE frame from %s: %s",
                instrument_id,
                record.dataValue[:40],
            )
            continue

        event_type, volume_ml, _ts_ms = decoded
        record_ts = _ensure_utc(record.timestamp).timestamp()

        if event_type == _EVT_INTAKE:
            runner.record_intake(volume_ml, record_ts)
            accepted_intake += 1
        elif event_type in (_EVT_SLEEP_START, _EVT_SLEEP_END):
            # Sleep events are informational at the backend for now;
            # they affect the on-device pace model only.
            log.debug(
                "Sleep event type=%d from %s at %s",
                event_type,
                instrument_id,
                record.timestamp,
            )
        else:
            log.warning("Unknown BLE frame event_type=%d from %s", event_type, instrument_id)

    drinks_produced = runner.drain()
    await _persist_events(session, instrument_id, drinks_produced)

    latest_ts = max((_ensure_utc(r.timestamp) for r in cg.data), default=None)
    if latest_ts:
        await update_device_heartbeat(session, instrument_id, latest_ts)

    await session.commit()

    for _ in drinks_produced:
        await broker.publish(
            {"kind": "event", "device_id": instrument_id, "type": "drink"}
        )

    if drinks_produced:
        await publish_priority_snapshot(session)

    return {
        "accepted_frames": len(cg.data),
        "accepted_intake": accepted_intake,
        "drinks": len(drinks_produced),
        "instrumentIdentifier": instrument_id,
    }


async def _ingest_direct(
    payload: DrinkPayload,
    session: AsyncSession,
) -> dict:
    """Handle a direct DrinkPayload from fake_gateway.py or HTTP fallback."""
    device_id = payload.device_id
    await _ensure_device(session, device_id)
    runner = registry.get(device_id)

    for event in sorted(payload.intake_events, key=lambda e: e.ts):
        runner.record_intake(
            event.volume_ml,
            _ensure_utc(event.ts).timestamp(),
        )

    drinks = runner.drain()
    await _persist_events(session, device_id, drinks)

    if payload.intake_events:
        latest_ts = max(_ensure_utc(e.ts) for e in payload.intake_events)
        await update_device_heartbeat(session, device_id, latest_ts)

    await session.commit()

    for _ in drinks:
        await broker.publish(
            {"kind": "event", "device_id": device_id, "type": "drink"}
        )

    if drinks:
        await publish_priority_snapshot(session)

    return {
        "accepted": len(payload.intake_events),
        "drinks": len(drinks),
    }


@router.post("/measurements", status_code=202)
async def ingest_measurements(
    request: Request,
    x_camgenium_signature: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
):
    raw = await request.body()
    _verify_signature(raw, x_camgenium_signature)

    log.debug(
        "ingest body (%d bytes): %s",
        len(raw),
        raw[:500].decode("utf-8", errors="replace"),
    )

    try:
        peek = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        raise HTTPException(400, "invalid JSON")

    # Camgenium liveness probe — respond and bail.
    if isinstance(peek, dict) and peek.get("test") is True:
        return {"accepted": 0, "test": True}

    # Camgenium webhook shape: has instrumentIdentifier + data[].
    if isinstance(peek, dict) and "instrumentIdentifier" in peek and "data" in peek:
        try:
            cg = CamgeniumWebhookPayload.model_validate(peek)
        except ValueError as e:
            raise HTTPException(400, f"invalid Camgenium payload: {e}") from e
        return await _ingest_camgenium(cg, session)

    # Direct button-event payload (fake_gateway.py / HTTP fallback).
    try:
        payload = DrinkPayload.model_validate(peek)
    except ValueError as e:
        raise HTTPException(400, f"invalid payload: {e}") from e

    return await _ingest_direct(payload, session)
