import asyncio
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession


class Broker:
    """In-process pub/sub. One queue per subscriber; publish fans out."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict]] = set()

    async def publish(self, message: dict) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                pass

    async def subscribe(self) -> AsyncIterator[dict]:
        q: asyncio.Queue[dict] = asyncio.Queue(maxsize=100)
        self._subscribers.add(q)
        try:
            while True:
                yield await q.get()
        finally:
            self._subscribers.discard(q)


broker = Broker()


async def publish_priority_snapshot(session: AsyncSession) -> None:
    """Push a full corridor snapshot via the broker.

    Called after any event/alert change so the corridor display (which
    subscribes to /sse) re-renders without polling. Imported lazily to
    keep this module free of route-layer deps.
    """
    from app.queries import bed_priorities

    beds = await bed_priorities(session)
    await broker.publish({"kind": "priority", "beds": beds})
