from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Device


async def update_device_heartbeat(
    session: AsyncSession, device_id: str, ts: datetime
) -> None:
    device = await session.get(Device, device_id)
    if device is not None:
        device.last_seen = ts
