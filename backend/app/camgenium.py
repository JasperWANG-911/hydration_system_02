"""Client for the Camgenium Harvester API.

Responsibilities:
    1. Fetch + refresh an OAuth2 access token (client_credentials grant).
    2. Register our public ingest URL as an outgoing webhook so Camgenium
       forwards instrument data to us.
    3. Periodically hit `/webhooks/{id}/keepalive` so the subscription
       isn't reaped by Camgenium.

The whole module is a no-op when credentials are missing — that lets the
backend run locally (with fake_gateway.py POSTing directly to the ingest
endpoint) before Camgenium credentials are provisioned.
"""
import asyncio
import logging
import time

import httpx

from app.config import settings

log = logging.getLogger(__name__)


class CamgeniumClient:
    """Thin async wrapper around the bits of the Harvester API we use."""

    def __init__(self) -> None:
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._webhook_id: str | None = None
        self._http = httpx.AsyncClient(timeout=10.0)

    @property
    def configured(self) -> bool:
        return bool(
            settings.camgenium_base_url
            and settings.camgenium_token_url
            and settings.camgenium_client_id
            and settings.camgenium_client_secret
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def _ensure_token(self) -> str:
        # Refresh 30s before expiry to avoid races with the auth server.
        if self._token and time.time() < self._token_expires_at - 30:
            return self._token

        resp = await self._http.post(
            settings.camgenium_token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": settings.camgenium_client_id,
                "client_secret": settings.camgenium_client_secret,
            },
        )
        resp.raise_for_status()
        body = resp.json()
        self._token = body["access_token"]
        self._token_expires_at = time.time() + int(body.get("expires_in", 300))
        return self._token

    async def _auth_headers(self) -> dict[str, str]:
        token = await self._ensure_token()
        return {"Authorization": f"Bearer {token}"}

    async def register_webhook(self, callback_url: str) -> str:
        """Register `callback_url` as an outgoing webhook. Returns webhook id."""
        headers = await self._auth_headers()
        payload = {
            "callbackUrl": callback_url,
            # Camgenium signs deliveries with this secret so we can verify.
            "secret": settings.ingest_shared_secret,
        }
        resp = await self._http.post(
            f"{settings.camgenium_base_url}/api/v1/harvester/webhooks",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        body = resp.json()
        self._webhook_id = body.get("id") or body.get("webhookId")
        if not self._webhook_id:
            raise RuntimeError(f"webhook registration returned no id: {body}")
        log.info("registered Camgenium webhook id=%s", self._webhook_id)
        return self._webhook_id

    async def keepalive(self) -> None:
        if not self._webhook_id:
            return
        headers = await self._auth_headers()
        resp = await self._http.post(
            f"{settings.camgenium_base_url}"
            f"/api/v1/harvester/webhooks/{self._webhook_id}/keepalive",
            headers=headers,
        )
        resp.raise_for_status()

    async def delete_webhook(self) -> None:
        if not self._webhook_id:
            return
        headers = await self._auth_headers()
        try:
            await self._http.delete(
                f"{settings.camgenium_base_url}"
                f"/api/v1/harvester/webhooks/{self._webhook_id}",
                headers=headers,
            )
        except httpx.HTTPError:
            log.exception("failed to delete webhook %s", self._webhook_id)
        self._webhook_id = None


client = CamgeniumClient()


async def webhook_lifecycle() -> None:
    """Lifespan-scoped task: register, then keepalive on a fixed cadence."""
    if not client.configured:
        log.info("Camgenium credentials not set — skipping webhook registration")
        return
    if not settings.public_ingest_url:
        log.warning(
            "PUBLIC_INGEST_URL not set — cannot register Camgenium webhook"
        )
        return

    callback_url = settings.public_ingest_url.rstrip("/") + "/ingest/measurements"
    try:
        await client.register_webhook(callback_url)
    except Exception:
        log.exception("Camgenium webhook registration failed")
        return

    try:
        while True:
            await asyncio.sleep(settings.webhook_keepalive_seconds)
            try:
                await client.keepalive()
            except Exception:
                log.exception("Camgenium keepalive failed")
    finally:
        await client.delete_webhook()
