import asyncio
import json
import logging
from datetime import datetime, timezone

import aiomqtt

from app.alerts import evaluate_periodic_alerts, raise_alert
from app.config import settings
from app.db import SessionLocal
from app.events import EVENT_TYPES, record_event, update_device_heartbeat
from app.pubsub import broker

log = logging.getLogger(__name__)

TOPIC_PATTERN = "hydration/+/+"


def _parse_ts(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)


async def _handle_event(device_id: str, body: dict) -> None:
    event_type = body.get("type")
    if event_type not in EVENT_TYPES:
        log.warning("unknown event type %r from %s", event_type, device_id)
        return
    ts = _parse_ts(body.get("ts"))

    async with SessionLocal() as session:
        await record_event(session, device_id, ts, event_type, body)
        await update_device_heartbeat(session, device_id, ts)

        if event_type == "button":
            await raise_alert(session, await _bed_for_device(session, device_id), "button")
        await session.commit()

    await broker.publish({"kind": "event", "device_id": device_id, "type": event_type})


async def _bed_for_device(session, device_id: str) -> str:
    from app.models import Device

    device = await session.get(Device, device_id)
    return device.bed_id if device else ""


async def _handle_heartbeat(device_id: str, body: dict) -> None:
    ts = _parse_ts(body.get("ts"))
    async with SessionLocal() as session:
        await update_device_heartbeat(session, device_id, ts)
        await session.commit()


async def _dispatch(topic: str, payload: bytes) -> None:
    parts = topic.split("/")
    if len(parts) != 3 or parts[0] != "hydration":
        return
    _, device_id, kind = parts
    try:
        body = json.loads(payload)
    except json.JSONDecodeError:
        log.warning("non-JSON payload on %s", topic)
        return

    if kind == "event":
        await _handle_event(device_id, body)
    elif kind == "heartbeat":
        await _handle_heartbeat(device_id, body)
    # `status` (LWT) is handled by periodic offline check


async def mqtt_loop() -> None:
    while True:
        try:
            async with aiomqtt.Client(settings.mqtt_host, settings.mqtt_port) as client:
                await client.subscribe(TOPIC_PATTERN)
                log.info("MQTT subscribed to %s", TOPIC_PATTERN)
                async for msg in client.messages:
                    try:
                        await _dispatch(str(msg.topic), msg.payload)
                    except Exception:
                        log.exception("error handling %s", msg.topic)
        except aiomqtt.MqttError as e:
            log.warning("MQTT disconnected (%s) — reconnecting in 3s", e)
            await asyncio.sleep(3)


async def alert_loop() -> None:
    while True:
        try:
            async with SessionLocal() as session:
                raised = await evaluate_periodic_alerts(session)
            for bed_id, kind in raised:
                await broker.publish({"kind": "alert", "bed_id": bed_id, "alert_kind": kind})
        except Exception:
            log.exception("alert evaluation failed")
        await asyncio.sleep(60)
