"""Reseed beds, devices, patients, and one open stay per occupied bed.

Idempotent: existing rows with matching ids are skipped. Run as:
    python scripts/seed.py
"""
import asyncio
import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from app.models import Bed, Device, Patient, Stay  # noqa: E402

DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://hydration:hydration@localhost:5432/hydration",
)

BEDS = [
    ("B-101", "Ward A", "101", "A-101-1"),
    ("B-102", "Ward A", "102", "A-102-1"),
    ("B-103", "Ward A", "103", "A-103-1"),
    ("B-201", "Ward B", "201", "B-201-1"),
    ("B-202", "Ward B", "202", "B-202-1"),
]
DEVICES = [
    ("dev-001", "B-101"),
    ("dev-002", "B-102"),
    ("dev-003", "B-103"),
    ("dev-004", "B-201"),
    ("dev-005", "B-202"),
]
PATIENTS = [
    ("P-001", "Alice Chen", 2000),
    ("P-002", "Bob Martinez", 2500),
    ("P-003", "Carol Singh", 1800),
]
STAYS = [("P-001", "B-101"), ("P-002", "B-102"), ("P-003", "B-201")]


async def main() -> None:
    engine = create_async_engine(DB_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as session:
        for bed_id, ward, room, label in BEDS:
            if await session.get(Bed, bed_id) is None:
                session.add(Bed(bed_id=bed_id, ward=ward, room=room, label=label))
        for device_id, bed_id in DEVICES:
            if await session.get(Device, device_id) is None:
                session.add(Device(device_id=device_id, bed_id=bed_id))
        for patient_id, name, target in PATIENTS:
            if await session.get(Patient, patient_id) is None:
                session.add(Patient(patient_id=patient_id, name=name, intake_target_ml=target))
        await session.flush()

        for patient_id, bed_id in STAYS:
            existing = await session.execute(
                select(Stay).where(Stay.bed_id == bed_id, Stay.discharged_at.is_(None))
            )
            if existing.scalar_one_or_none() is None:
                session.add(Stay(patient_id=patient_id, bed_id=bed_id))

        await session.commit()
    await engine.dispose()
    print("seeded")


if __name__ == "__main__":
    asyncio.run(main())
