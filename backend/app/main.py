import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.camgenium import client as camgenium_client, webhook_lifecycle
from app.routes import admin, beds, corridor, dashboard, ingest, sse
from app.tasks import alert_loop

logging.basicConfig(level=logging.INFO)

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = [
        asyncio.create_task(alert_loop()),
        asyncio.create_task(webhook_lifecycle()),
    ]
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        await camgenium_client.close()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

app.state.templates = templates

app.include_router(dashboard.router)
app.include_router(beds.router)
app.include_router(admin.router)
app.include_router(corridor.router)
app.include_router(sse.router)
app.include_router(ingest.router)
