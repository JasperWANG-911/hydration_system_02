from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Device, Event, Stay

EVENT_TYPES = {"drink", "refill", "removed", "placed", "button"}


def compute_intake_delta_ml(event_type: str, delta_g: float | None) -> int | None:
    """Only `drink` events with negative weight delta count as intake.
    Returns a positive ml value, or None if the event doesn't contribute."""
    if event_type != "drink" or delta_g is None or delta_g >= 0:
        return None
    return int(round(-delta_g * settings.g_to_ml))


async def resolve_open_stay(session: AsyncSession, device_id: str) -> Stay | None:
    """device → bed → currently open stay (if any)."""
    stmt = (
        select(Stay)
        .join(Device, Device.bed_id == Stay.bed_id)
        .where(Device.device_id == device_id, Stay.discharged_at.is_(None))
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def record_event(
    session: AsyncSession,
    device_id: str,
    ts: datetime,
    event_type: str,
    payload: dict,
) -> Event:
    delta_g = payload.get("delta_g")
    intake = compute_intake_delta_ml(event_type, delta_g)

    event = Event(
        ts=ts,
        device_id=device_id,
        type=event_type,
        payload=payload,
        intake_delta_ml=intake,
    )
    session.add(event)
    return event


async def update_device_heartbeat(
    session: AsyncSession, device_id: str, ts: datetime
) -> None:
    device = await session.get(Device, device_id)
    if device is not None:
        device.last_seen = ts
