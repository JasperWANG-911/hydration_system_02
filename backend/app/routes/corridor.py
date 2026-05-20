from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.queries import bed_priorities

router = APIRouter()


@router.get("/corridor", response_class=HTMLResponse)
async def corridor(request: Request):
    """Self-contained page; pulls data from /api/priorities + /sse."""
    return request.app.state.templates.TemplateResponse(
        request, "corridor.html", {}
    )


@router.get("/api/priorities")
async def api_priorities(session: AsyncSession = Depends(get_session)):
    """Snapshot consumed by corridor.html on load."""
    beds = await bed_priorities(session)
    return {"beds": beds}
