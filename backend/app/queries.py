from datetime import datetime, timezone

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Alert, Bed, Device, Event, Patient, Stay

# Higher = more urgent. Tunable.
ALERT_SEVERITY: dict[str, int] = {
    "button": 100,
    "no_drink": 40,
    "device_offline": 30,
    "behind_target": 20,
}


async def list_wards(session: AsyncSession) -> list[str]:
    rows = await session.execute(select(Bed.ward).distinct().order_by(Bed.ward))
    return [r[0] for r in rows]


async def open_alerts(session: AsyncSession) -> list[dict]:
    rows = await session.execute(
        select(Alert.alert_id, Alert.bed_id, Alert.kind, Alert.raised_at, Bed.ward, Bed.label)
        .join(Bed, Bed.bed_id == Alert.bed_id)
        .where(Alert.resolved_at.is_(None))
        .order_by(Alert.raised_at.desc())
    )
    return [dict(r._mapping) for r in rows]


def _today_start() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


async def beds_overview(
    session: AsyncSession,
    ward: str | None = None,
    status: str | None = None,
    search: str | None = None,
) -> list[dict]:
    """Per-bed snapshot for the dashboard grid."""
    today = _today_start()

    intake_subq = (
        select(
            Device.bed_id.label("bed_id"),
            func.coalesce(func.sum(Event.intake_delta_ml), 0).label("intake_ml"),
            func.max(Event.ts).filter(Event.type == "drink").label("last_drink"),
        )
        .select_from(Device)
        .outerjoin(
            Event,
            and_(Event.device_id == Device.device_id, Event.ts >= today),
        )
        .group_by(Device.bed_id)
        .subquery()
    )

    alert_subq = (
        select(
            Alert.bed_id.label("bed_id"),
            func.count(Alert.alert_id).label("alert_count"),
        )
        .where(Alert.resolved_at.is_(None))
        .group_by(Alert.bed_id)
        .subquery()
    )

    stmt = (
        select(
            Bed.bed_id,
            Bed.ward,
            Bed.label,
            Patient.patient_id,
            Patient.name,
            Patient.intake_target_ml,
            Device.last_seen,
            func.coalesce(intake_subq.c.intake_ml, 0).label("intake_ml"),
            intake_subq.c.last_drink,
            func.coalesce(alert_subq.c.alert_count, 0).label("alert_count"),
        )
        .select_from(Bed)
        .join(Device, Device.bed_id == Bed.bed_id, isouter=True)
        .join(
            Stay,
            and_(Stay.bed_id == Bed.bed_id, Stay.discharged_at.is_(None)),
            isouter=True,
        )
        .join(Patient, Patient.patient_id == Stay.patient_id, isouter=True)
        .join(intake_subq, intake_subq.c.bed_id == Bed.bed_id, isouter=True)
        .join(alert_subq, alert_subq.c.bed_id == Bed.bed_id, isouter=True)
    )

    if ward:
        stmt = stmt.where(Bed.ward == ward)
    if search:
        like = f"%{search}%"
        stmt = stmt.where((Bed.label.ilike(like)) | (Patient.name.ilike(like)))

    stmt = stmt.order_by(Bed.ward, Bed.bed_id)
    rows = await session.execute(stmt)
    out: list[dict] = [dict(r._mapping) for r in rows]

    if status:
        out = [r for r in out if _classify(r) == status]
    return out


def _classify(row: dict) -> str:
    if row["alert_count"] > 0:
        return "alert"
    if row["patient_id"] is None:
        return "empty"
    target = row["intake_target_ml"] or 0
    if target > 0 and row["intake_ml"] >= target:
        return "ok"
    return "active"


async def corridor_ranking(session: AsyncSession, limit: int = 20) -> list[dict]:
    """Beds with open alerts, ranked by severity then alert age."""
    today = _today_start()

    intake_subq = (
        select(
            Device.bed_id.label("bed_id"),
            func.coalesce(func.sum(Event.intake_delta_ml), 0).label("intake_ml"),
            func.max(Event.ts).filter(Event.type == "drink").label("last_drink"),
        )
        .select_from(Device)
        .outerjoin(
            Event, and_(Event.device_id == Device.device_id, Event.ts >= today)
        )
        .group_by(Device.bed_id)
        .subquery()
    )

    stmt = (
        select(
            Alert.alert_id,
            Alert.kind,
            Alert.raised_at,
            Bed.bed_id,
            Bed.label,
            Bed.ward,
            Bed.room,
            Patient.name.label("patient_name"),
            Patient.intake_target_ml,
            func.coalesce(intake_subq.c.intake_ml, 0).label("intake_ml"),
            intake_subq.c.last_drink,
        )
        .select_from(Alert)
        .join(Bed, Bed.bed_id == Alert.bed_id)
        .outerjoin(
            Stay, and_(Stay.bed_id == Bed.bed_id, Stay.discharged_at.is_(None))
        )
        .outerjoin(Patient, Patient.patient_id == Stay.patient_id)
        .outerjoin(intake_subq, intake_subq.c.bed_id == Bed.bed_id)
        .where(Alert.resolved_at.is_(None))
    )
    rows = [dict(r._mapping) for r in await session.execute(stmt)]

    # Group by bed: keep highest-severity (then oldest) alert per bed
    by_bed: dict[str, dict] = {}
    for r in rows:
        sev = ALERT_SEVERITY.get(r["kind"], 0)
        r["severity"] = sev
        cur = by_bed.get(r["bed_id"])
        if (
            cur is None
            or sev > cur["severity"]
            or (sev == cur["severity"] and r["raised_at"] < cur["raised_at"])
        ):
            by_bed[r["bed_id"]] = r

    ranked = sorted(
        by_bed.values(), key=lambda r: (-r["severity"], r["raised_at"])
    )
    return ranked[:limit]


async def bed_priorities(session: AsyncSession) -> list[dict]:
    """Snapshot used by the corridor display.

    One entry per occupied bed (stay open, patient assigned). Priority:
        1 (red)   — `button` or `no_drink` open alert
        2 (amber) — `device_offline` or `behind_target` open alert
        3 (green) — no open alerts
    Beds without a patient are omitted.
    """
    open_alerts_subq = (
        select(
            Alert.bed_id.label("bed_id"),
            func.array_agg(Alert.kind).label("kinds"),
        )
        .where(Alert.resolved_at.is_(None))
        .group_by(Alert.bed_id)
        .subquery()
    )

    stmt = (
        select(
            Bed.bed_id,
            Bed.ward,
            Bed.label,
            open_alerts_subq.c.kinds,
        )
        .select_from(Bed)
        .join(
            Stay,
            and_(Stay.bed_id == Bed.bed_id, Stay.discharged_at.is_(None)),
        )
        .join(Patient, Patient.patient_id == Stay.patient_id)
        .outerjoin(open_alerts_subq, open_alerts_subq.c.bed_id == Bed.bed_id)
        .order_by(Bed.ward, Bed.bed_id)
    )
    rows = await session.execute(stmt)

    p1 = {"button", "no_drink"}
    p2 = {"device_offline", "behind_target"}

    out: list[dict] = []
    for bed_id, ward, label, kinds in rows:
        kinds_set = set(kinds or [])
        if kinds_set & p1:
            priority = 1
        elif kinds_set & p2:
            priority = 2
        else:
            priority = 3
        out.append(
            {
                "bed_id": bed_id,
                "ward": ward,
                "label": label,
                "priority": priority,
            }
        )
    return out


async def bed_detail(session: AsyncSession, bed_id: str) -> dict | None:
    bed = await session.get(Bed, bed_id)
    if bed is None:
        return None
    stay = (
        await session.execute(
            select(Stay, Patient)
            .join(Patient, Patient.patient_id == Stay.patient_id)
            .where(Stay.bed_id == bed_id, Stay.discharged_at.is_(None))
        )
    ).first()
    device = (
        await session.execute(select(Device).where(Device.bed_id == bed_id))
    ).scalar_one_or_none()

    today = _today_start()
    events: list[dict] = []
    intake = 0
    if device is not None:
        rows = await session.execute(
            select(Event.ts, Event.type, Event.payload, Event.intake_delta_ml)
            .where(Event.device_id == device.device_id, Event.ts >= today)
            .order_by(Event.ts.desc())
            .limit(50)
        )
        events = [dict(r._mapping) for r in rows]
        intake_row = await session.execute(
            select(func.coalesce(func.sum(Event.intake_delta_ml), 0))
            .where(Event.device_id == device.device_id, Event.ts >= today)
        )
        intake = intake_row.scalar() or 0

    return {
        "bed": bed,
        "stay": stay[0] if stay else None,
        "patient": stay[1] if stay else None,
        "device": device,
        "events": events,
        "intake_ml": intake,
    }
