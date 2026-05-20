"""Client for the Camgenium Harvester API.

Responsibilities:
    1. Exchange the long-lived refresh token for short-lived access
       tokens via the SoftSilicon Keycloak realm (OAuth2 refresh_token
       grant on a public client — no client_secret).
    2. Register our public ingest URL as an outgoing webhook so
       Camgenium forwards instrument data to us.
    3. Periodically hit `/webhooks/{id}/keepalive` so the subscription
       isn't reaped by Camgenium.

The whole module is a no-op when `CAMGENIUM_REFRESH_TOKEN` is missing —
that lets the backend run locally (with fake_gateway.py POSTing
directly to the ingest endpoint) before credentials are provisioned.
"""
import asyncio
import logging
import re
import time

import httpx

from app.config import ENV_FILE_PATH, settings

log = logging.getLogger(__name__)

# Refresh `expires_in - SAFETY_MARGIN_S` so a token never expires
# mid-request. Camgenium access tokens last 600s (10 min).
SAFETY_MARGIN_S = 30


def _persist_rotated_refresh_token(new_token: str) -> None:
    """Rewrite the CAMGENIUM_REFRESH_TOKEN line in .env in place.

    SoftSilicon's Keycloak realm has refresh-token rotation on, so every
    successful refresh invalidates the previous refresh token. If we
    don't write the new one to .env, the next backend restart reads a
    stale value and the system 401s. The .env file is gitignored, and
    docker-compose mounts it from the host so this write survives
    container restarts.
    """
    if not ENV_FILE_PATH.exists():
        log.warning(
            ".env not found at %s — cannot persist rotated refresh token. "
            "If you restart the backend, expect auth to fail. Re-login "
            "and recreate .env from .env.example.",
            ENV_FILE_PATH,
        )
        return
    src = ENV_FILE_PATH.read_text()
    new_src, n = re.subn(
        r"^CAMGENIUM_REFRESH_TOKEN=.*$",
        f"CAMGENIUM_REFRESH_TOKEN={new_token}",
        src,
        flags=re.MULTILINE,
    )
    if n == 0:
        log.warning(
            "no CAMGENIUM_REFRESH_TOKEN line in .env — cannot persist "
            "rotated token; appending it"
        )
        new_src = src.rstrip() + f"\nCAMGENIUM_REFRESH_TOKEN={new_token}\n"
    ENV_FILE_PATH.write_text(new_src)
    # Reflect the new value in the in-memory settings so subsequent
    # refreshes use it without reloading the process.
    settings.camgenium_refresh_token = new_token


class CamgeniumClient:
    """Thin async wrapper around the bits of the Harvester API we use."""

    def __init__(self) -> None:
        self._access_token: str | None = None
        self._access_expires_at: float = 0.0
        self._webhook_id: str | None = None
        self._http = httpx.AsyncClient(timeout=10.0)

    @property
    def configured(self) -> bool:
        return bool(
            settings.camgenium_refresh_token
            and settings.camgenium_token_url
            and settings.camgenium_client_id
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def _ensure_access_token(self) -> str:
        if (
            self._access_token
            and time.time() < self._access_expires_at - SAFETY_MARGIN_S
        ):
            return self._access_token

        resp = await self._http.post(
            settings.camgenium_token_url,
            data={
                "grant_type": "refresh_token",
                "client_id": settings.camgenium_client_id,
                "refresh_token": settings.camgenium_refresh_token,
            },
        )
        resp.raise_for_status()
        body = resp.json()
        self._access_token = body["access_token"]
        self._access_expires_at = time.time() + int(body.get("expires_in", 600))
        # SoftSilicon's Keycloak rotates the refresh token on every
        # successful exchange. Persist the rotated value so the next
        # backend restart doesn't 401.
        rotated = body.get("refresh_token")
        if rotated and rotated != settings.camgenium_refresh_token:
            _persist_rotated_refresh_token(rotated)
            log.info("rotated refresh_token written to .env")
        return self._access_token

    async def _auth_headers(self) -> dict[str, str]:
        token = await self._ensure_access_token()
        return {"Authorization": f"Bearer {token}"}

    async def register_webhook(
        self, callback_url: str, instrument_ids: list[str]
    ) -> str:
        """Register `callback_url` as an outgoing webhook subscribed to
        the listed instruments. Returns the webhook id.

        NOTE: despite what the `WebhookRegistrationRequest` schema in
        the Harvester swagger claims, `instrumentIdentifiers` must be a
        comma-separated STRING, not a JSON array. Sending an array
        returns 400 with an empty body. We discovered this by probing
        against existing webhooks where the GET response shape was
        string-typed.
        """
        if not instrument_ids:
            raise RuntimeError(
                "cannot register webhook with no instrument ids"
            )
        headers = await self._auth_headers()
        payload = {
            "callbackUrl": callback_url,
            "instrumentIdentifiers": ",".join(instrument_ids),
            "mode": settings.camgenium_webhook_mode,
            "enableKeepAlive": False,
            "keepAliveIntervalSeconds": settings.webhook_keepalive_seconds,
            "summaryIntervalSeconds": 10,
        }
        # Some accounts reject the `secret` field; only include it when
        # the user explicitly configured one.
        if settings.ingest_shared_secret and settings.ingest_shared_secret != "change-me":
            payload["secret"] = settings.ingest_shared_secret
        resp = await self._http.post(
            f"{settings.camgenium_base_url}/api/v1/harvester/webhooks",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        body = resp.json()
        self._webhook_id = body.get("id") or body.get("webhookId")
        if not self._webhook_id:
            raise RuntimeError(
                f"webhook registration returned no id: {body}"
            )
        log.info(
            "registered Camgenium webhook id=%s for instruments=%s",
            self._webhook_id,
            instrument_ids,
        )
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


def _parsed_instrument_ids() -> list[str]:
    return [
        s.strip()
        for s in settings.camgenium_instrument_ids.split(",")
        if s.strip()
    ]


async def webhook_lifecycle() -> None:
    """Lifespan-scoped task: register, then keepalive on a fixed cadence."""
    if not client.configured:
        log.info(
            "CAMGENIUM_REFRESH_TOKEN not set — skipping webhook registration"
        )
        return
    if not settings.public_ingest_url:
        log.warning(
            "PUBLIC_INGEST_URL not set — cannot register Camgenium webhook"
        )
        return
    instrument_ids = _parsed_instrument_ids()
    if not instrument_ids:
        log.warning(
            "CAMGENIUM_INSTRUMENT_IDS not set — webhook registration skipped. "
            "Backend will still accept /ingest/measurements from "
            "fake_gateway.py. Fill the env var once devices are "
            "registered in Camgenium."
        )
        return

    callback_url = (
        settings.public_ingest_url.rstrip("/") + "/ingest/measurements"
    )
    try:
        await client.register_webhook(callback_url, instrument_ids)
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
