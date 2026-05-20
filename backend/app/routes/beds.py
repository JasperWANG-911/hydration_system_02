from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.queries import bed_detail

router = APIRouter(prefix="/beds")


@router.get("/{bed_id}", response_class=HTMLResponse)
async def bed_view(
    bed_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    detail = await bed_detail(session, bed_id)
    if detail is None:
        raise HTTPException(404, "bed not found")
    return request.app.state.templates.TemplateResponse(
        request,
        "bed_detail.html",
        detail,
    )
