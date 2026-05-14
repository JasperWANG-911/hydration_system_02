import json

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from app.pubsub import broker

router = APIRouter()


@router.get("/sse")
async def sse():
    async def stream():
        async for msg in broker.subscribe():
            yield {"event": msg.get("kind", "update"), "data": json.dumps(msg)}

    return EventSourceResponse(stream())
