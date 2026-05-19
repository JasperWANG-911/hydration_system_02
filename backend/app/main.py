import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.ingest import alert_loop, mqtt_loop
from app.routes import admin, beds, corridor, dashboard, sse

logging.basicConfig(level=logging.INFO)

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    mqtt_task = asyncio.create_task(mqtt_loop())
    alert_task = asyncio.create_task(alert_loop())
    try:
        yield
    finally:
        for t in (mqtt_task, alert_task):
            t.cancel()
        for t in (mqtt_task, alert_task):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

app.state.templates = templates

app.include_router(dashboard.router)
app.include_router(beds.router)
app.include_router(admin.router)
app.include_router(corridor.router)
app.include_router(sse.router)
