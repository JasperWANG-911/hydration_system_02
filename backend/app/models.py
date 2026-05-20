from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Bed(Base):
    __tablename__ = "beds"
    bed_id: Mapped[str] = mapped_column(String, primary_key=True)
    ward: Mapped[str] = mapped_column(String)
    room: Mapped[str] = mapped_column(String)
    label: Mapped[str] = mapped_column(String)


class Device(Base):
    __tablename__ = "devices"
    device_id: Mapped[str] = mapped_column(String, primary_key=True)
    bed_id: Mapped[str] = mapped_column(ForeignKey("beds.bed_id"), unique=True)
    last_seen: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class Patient(Base):
    __tablename__ = "patients"
    patient_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    intake_target_ml: Mapped[int] = mapped_column(Integer, default=2000)


class Stay(Base):
    __tablename__ = "stays"
    stay_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.patient_id"))
    bed_id: Mapped[str] = mapped_column(ForeignKey("beds.bed_id"))
    admitted_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    discharged_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class Event(Base):
    __tablename__ = "events"
    ts: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.device_id"), primary_key=True)
    type: Mapped[str] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(JSONB)
    intake_delta_ml: Mapped[int | None] = mapped_column(Integer)


class Measurement(Base):
    __tablename__ = "measurements"
    ts: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), primary_key=True)
    device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.device_id"), primary_key=True
    )
    weight_g: Mapped[float] = mapped_column(Float)
    cup_present: Mapped[bool | None] = mapped_column(Boolean)


class Alert(Base):
    __tablename__ = "alerts"
    alert_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    bed_id: Mapped[str] = mapped_column(ForeignKey("beds.bed_id"))
    kind: Mapped[str] = mapped_column(String)
    raised_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
