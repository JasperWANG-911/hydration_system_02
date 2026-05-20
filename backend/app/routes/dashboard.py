from fastapi import APIRouter
from fastapi.responses import RedirectResponse

router = APIRouter()


@router.get("/")
async def index():
    """The corridor view is the only nurse-facing page we ship today."""
    return RedirectResponse("/corridor", status_code=307)
