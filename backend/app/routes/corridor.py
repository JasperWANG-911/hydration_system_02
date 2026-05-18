from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.queries import corridor_ranking

router = APIRouter()


@router.get("/corridor", response_class=HTMLResponse)
async def corridor(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    rows = await corridor_ranking(session, limit=20)
    return request.app.state.templates.TemplateResponse(
        "corridor.html", {"request": request, "rows": rows}
    )
