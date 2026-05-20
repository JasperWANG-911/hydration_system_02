"""Background tasks running inside the FastAPI process.

For a school-project sized deployment we don't need a separate worker
service — periodic alert evaluation runs as an asyncio task spawned in
`main.py`'s lifespan.
"""
import asyncio
import logging

from app.alerts import evaluate_periodic_alerts
from app.db import SessionLocal
from app.pubsub import broker

log = logging.getLogger(__name__)


async def alert_loop() -> None:
    """Re-evaluate periodic alert rules once a minute."""
    while True:
        try:
            async with SessionLocal() as session:
                raised = await evaluate_periodic_alerts(session)
            for bed_id, kind in raised:
                await broker.publish(
                    {"kind": "alert", "bed_id": bed_id, "alert_kind": kind}
                )
        except Exception:
            log.exception("alert evaluation failed")
        await asyncio.sleep(60)
