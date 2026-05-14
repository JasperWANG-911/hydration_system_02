from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Alert, Bed, Device, Event, Patient, Stay


async def raise_alert(session: AsyncSession, bed_id: str, kind: str) -> Alert | None:
    """Raise an alert if there isn't already an open one of this kind for this bed."""
    existing = await session.execute(
        select(Alert).where(
            Alert.bed_id == bed_id,
            Alert.kind == kind,
            Alert.resolved_at.is_(None),
        )
    )
    if existing.scalar_one_or_none() is not None:
        return None
    alert = Alert(bed_id=bed_id, kind=kind)
    session.add(alert)
    return alert


async def resolve_alerts(session: AsyncSession, bed_id: str, kind: str) -> None:
    await session.execute(
        Alert.__table__.update()
        .where(
            Alert.bed_id == bed_id,
            Alert.kind == kind,
            Alert.resolved_at.is_(None),
        )
        .values(resolved_at=func.now())
    )


async def evaluate_periodic_alerts(session: AsyncSession) -> list[tuple[str, str]]:
    """Run periodically. Returns list of (bed_id, kind) for newly raised alerts."""
    raised: list[tuple[str, str]] = []
    now = datetime.now(timezone.utc)

    # 1. Device offline
    offline_cutoff = now - timedelta(minutes=settings.device_offline_minutes)
    offline_devices = await session.execute(
        select(Device.device_id, Device.bed_id).where(
            (Device.last_seen.is_(None)) | (Device.last_seen < offline_cutoff)
        )
    )
    online_beds: set[str] = set()
    for _, bed_id in offline_devices:
        if await raise_alert(session, bed_id, "device_offline"):
            raised.append((bed_id, "device_offline"))
    # Resolve previously offline devices that are now seen
    fresh = await session.execute(
        select(Device.bed_id).where(Device.last_seen >= offline_cutoff)
    )
    for (bed_id,) in fresh:
        online_beds.add(bed_id)
        await resolve_alerts(session, bed_id, "device_offline")

    # 2. No drink for N waking hours
    if settings.waking_start_hour <= now.hour < settings.waking_end_hour:
        cutoff = now - timedelta(hours=settings.no_drink_alert_hours)
        open_stays = await session.execute(
            select(Stay.bed_id).where(Stay.discharged_at.is_(None))
        )
        for (bed_id,) in open_stays:
            last_drink = await session.execute(
                select(func.max(Event.ts))
                .join(Device, Device.device_id == Event.device_id)
                .where(Device.bed_id == bed_id, Event.type == "drink")
            )
            last_ts = last_drink.scalar()
            if last_ts is None or last_ts < cutoff:
                if await raise_alert(session, bed_id, "no_drink"):
                    raised.append((bed_id, "no_drink"))
            else:
                await resolve_alerts(session, bed_id, "no_drink")

    # 3. Behind target by evening
    if now.hour >= settings.evening_check_hour:
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        rows = await session.execute(
            select(
                Stay.bed_id,
                Patient.intake_target_ml,
                func.coalesce(func.sum(Event.intake_delta_ml), 0).label("intake"),
            )
            .join(Patient, Patient.patient_id == Stay.patient_id)
            .join(Bed, Bed.bed_id == Stay.bed_id)
            .join(Device, Device.bed_id == Bed.bed_id)
            .outerjoin(
                Event,
                and_(Event.device_id == Device.device_id, Event.ts >= today_start),
            )
            .where(Stay.discharged_at.is_(None))
            .group_by(Stay.bed_id, Patient.intake_target_ml)
        )
        for bed_id, target, intake in rows:
            if intake < target * settings.evening_min_target_fraction:
                if await raise_alert(session, bed_id, "behind_target"):
                    raised.append((bed_id, "behind_target"))
            else:
                await resolve_alerts(session, bed_id, "behind_target")

    await session.commit()
    return raised
