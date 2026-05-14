from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.queries import beds_overview, list_wards, open_alerts

router = APIRouter()


def _status_for(row: dict) -> str:
    if row["alert_count"] > 0:
        return "alert"
    if row["patient_id"] is None:
        return "empty"
    target = row["intake_target_ml"] or 0
    if target > 0 and row["intake_ml"] >= target:
        return "ok"
    return "active"


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    ward: str | None = None,
    status: str | None = None,
    q: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    wards = await list_wards(session)
    alerts = await open_alerts(session)
    rows = (
        await beds_overview(session, ward=ward, status=status, search=q)
        if (ward or status or q)
        else []
    )
    for row in rows:
        row["status"] = _status_for(row)

    severity = {"alert": 0, "active": 1, "empty": 2, "ok": 3}
    rows.sort(key=lambda r: (severity.get(r["status"], 99), r["bed_id"]))

    return request.app.state.templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "wards": wards,
            "alerts": alerts,
            "rows": rows,
            "filter_ward": ward or "",
            "filter_status": status or "",
            "filter_q": q or "",
        },
    )
