from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Bed, Patient, Stay

router = APIRouter()


@router.get("/beds/{bed_id}/admit", response_class=HTMLResponse)
async def admit_form(
    bed_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    bed = await session.get(Bed, bed_id)
    if bed is None:
        raise HTTPException(404)
    return request.app.state.templates.TemplateResponse(
        request,
        "admit.html",
        {"bed": bed},
    )


@router.post("/beds/{bed_id}/admit")
async def admit(
    bed_id: str,
    name: str = Form(...),
    intake_target_ml: int = Form(2000),
    patient_id: str = Form(...),
    session: AsyncSession = Depends(get_session),
):
    bed = await session.get(Bed, bed_id)
    if bed is None:
        raise HTTPException(404)

    existing = await session.execute(
        select(Stay).where(Stay.bed_id == bed_id, Stay.discharged_at.is_(None))
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(409, "bed already occupied")

    patient = await session.get(Patient, patient_id)
    if patient is None:
        patient = Patient(patient_id=patient_id, name=name, intake_target_ml=intake_target_ml)
        session.add(patient)
    else:
        patient.name = name
        patient.intake_target_ml = intake_target_ml

    session.add(Stay(patient_id=patient_id, bed_id=bed_id))
    await session.commit()
    return RedirectResponse(f"/beds/{bed_id}", status_code=303)


@router.post("/stays/{stay_id}/discharge")
async def discharge(stay_id: int, session: AsyncSession = Depends(get_session)):
    stay = await session.get(Stay, stay_id)
    if stay is None or stay.discharged_at is not None:
        raise HTTPException(404)
    stay.discharged_at = datetime.now(timezone.utc)
    await session.commit()
    return RedirectResponse(f"/beds/{stay.bed_id}", status_code=303)
