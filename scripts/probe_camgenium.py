"""Probe a handful of Camgenium endpoints to map out what our account
can do without going through the BLE → phone path.

Run this when:
    - webhook registration keeps 400'ing and we want to know why
    - we want to know if we can POST data directly to instruments (no BLE)
    - we want to know what mode / endpoint variants the server actually
      accepts

The script auto-refreshes the Camgenium access token using the value in
.env and persists any rotated refresh_token back, same as the backend
does at runtime. Tokens are never printed.
"""
import base64
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def load_env() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


def write_refresh_token(new_token: str) -> None:
    src = ENV_PATH.read_text()
    new_src, n = re.subn(
        r"^CAMGENIUM_REFRESH_TOKEN=.*$",
        f"CAMGENIUM_REFRESH_TOKEN={new_token}",
        src,
        flags=re.MULTILINE,
    )
    if n:
        ENV_PATH.write_text(new_src)


def refresh_access_token(env: dict[str, str]) -> str:
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "client_id": env["CAMGENIUM_CLIENT_ID"],
        "refresh_token": env["CAMGENIUM_REFRESH_TOKEN"],
    }).encode()
    req = urllib.request.Request(
        env["CAMGENIUM_TOKEN_URL"],
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            blob = json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"!! token refresh failed HTTP {e.code}")
        print(e.read().decode())
        sys.exit(1)
    rotated = blob.get("refresh_token")
    if rotated and rotated != env["CAMGENIUM_REFRESH_TOKEN"]:
        write_refresh_token(rotated)
        env["CAMGENIUM_REFRESH_TOKEN"] = rotated
    return blob["access_token"]


def probe(
    label: str,
    method: str,
    url: str,
    token: str,
    body: dict | None = None,
) -> None:
    print()
    print("=" * 78)
    print(f"{label}")
    print(f"  {method} {url}")
    if body is not None:
        print(f"  body: {json.dumps(body)}")
    print("-" * 78)

    headers = {"Authorization": f"Bearer {token}", "Accept": "*/*"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            print(f"  HTTP {r.status}")
            raw = r.read().decode()
            try:
                parsed = json.loads(raw)
                pretty = json.dumps(parsed, indent=2)
                # truncate huge bodies
                if len(pretty) > 2000:
                    pretty = pretty[:2000] + "\n  ... (truncated)"
                print(pretty)
            except Exception:
                print(raw[:1000])
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code}")
        err_body = e.read().decode()
        if err_body:
            print(err_body[:500])
        else:
            print("  (empty response body)")
    except Exception as e:
        print(f"  !! exception: {e}")


def list_webhooks(env: dict[str, str], token: str) -> list[dict]:
    base = env["CAMGENIUM_BASE_URL"].rstrip("/")
    req = urllib.request.Request(
        f"{base}/api/v1/harvester/webhooks",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def delete_webhook(env: dict[str, str], token: str, webhook_id: str) -> None:
    base = env["CAMGENIUM_BASE_URL"].rstrip("/")
    req = urllib.request.Request(
        f"{base}/api/v1/harvester/webhooks/{webhook_id}",
        headers={"Authorization": f"Bearer {token}"},
        method="DELETE",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            print(f"  deleted {webhook_id}  HTTP {r.status}")
    except urllib.error.HTTPError as e:
        print(f"  delete {webhook_id} failed  HTTP {e.code}")


def cleanup_httpbin_webhooks() -> None:
    """Delete every webhook whose callbackUrl points at httpbin."""
    env = load_env()
    token = refresh_access_token(env)
    hooks = list_webhooks(env, token)
    targets = [h for h in hooks if "httpbin.org" in h.get("callbackUrl", "")]
    print(f"found {len(targets)} httpbin webhook(s) to delete")
    for h in targets:
        delete_webhook(env, token, h["webhookId"])


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "cleanup":
        cleanup_httpbin_webhooks()
        return

    env = load_env()
    base = env["CAMGENIUM_BASE_URL"].rstrip("/")
    instrument_id = "018d7ae542914998"

    print("refreshing access token...")
    token = refresh_access_token(env)
    print(f"got token (len {len(token)})")

    # 1. List webhooks — what does our account have, if anything?
    probe(
        "LIST WEBHOOKS for our account",
        "GET",
        f"{base}/api/v1/harvester/webhooks",
        token,
    )

    # 2. Direct data POST — the big one. If this works, we skip BLE entirely.
    sample_bytes = bytes(range(16))  # 16-byte filler payload
    probe(
        "DIRECT DATA POST (variant 1: data as base64)",
        "POST",
        f"{base}/api/v1/harvester/instruments/{instrument_id}/data",
        token,
        body={
            "data": base64.b64encode(sample_bytes).decode(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "packetType": 0,
        },
    )

    # 3. Direct data POST — variant where `data` is a byte-array
    probe(
        "DIRECT DATA POST (variant 2: data as byte array)",
        "POST",
        f"{base}/api/v1/harvester/instruments/{instrument_id}/data",
        token,
        body={
            "data": list(sample_bytes),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "packetType": 0,
        },
    )

    # 4. Service-mode data POST (the schema with userId/organisationId)
    # userId from JWT sub: 1de9f0f6-f5e8-4882-ac2e-bd03fb45f4f0
    probe(
        "DIRECT DATA POST (service variant with userId)",
        "POST",
        f"{base}/api/v1/harvester/instruments/{instrument_id}/data/service",
        token,
        body={
            "data": base64.b64encode(sample_bytes).decode(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "packetType": 0,
            "userId": "1de9f0f6-f5e8-4882-ac2e-bd03fb45f4f0",
            "organisationId": "",
        },
    )

    # 5. Webhook variant via /webhooks/service with userId
    probe(
        "WEBHOOK REGISTER via /webhooks/service (with userId)",
        "POST",
        f"{base}/api/v1/harvester/webhooks/service",
        token,
        body={
            "callbackUrl": "https://httpbin.org/post",
            "instrumentIdentifiers": [instrument_id],
            "userId": "1de9f0f6-f5e8-4882-ac2e-bd03fb45f4f0",
        },
    )

    # 6. Minimal webhook reg one more time (confirm 400 reproduces)
    probe(
        "WEBHOOK REGISTER minimal body (sanity check)",
        "POST",
        f"{base}/api/v1/harvester/webhooks",
        token,
        body={
            "callbackUrl": "https://httpbin.org/post",
            "instrumentIdentifiers": [instrument_id],
        },
    )

    # 7. SCHEMA WORKAROUND: instrumentIdentifiers as STRING (matching the
    # shape GET returns), not the array the OpenAPI schema claims.
    probe(
        "WEBHOOK REGISTER with instrumentIdentifiers as STRING",
        "POST",
        f"{base}/api/v1/harvester/webhooks",
        token,
        body={
            "callbackUrl": "https://httpbin.org/post",
            "instrumentIdentifiers": instrument_id,
        },
    )

    # 8. Full body matching the shape of existing successful webhooks.
    probe(
        "WEBHOOK REGISTER with full body (string id, mode 0, intervals)",
        "POST",
        f"{base}/api/v1/harvester/webhooks",
        token,
        body={
            "callbackUrl": "https://httpbin.org/post",
            "instrumentIdentifiers": instrument_id,
            "mode": 0,
            "enableKeepAlive": False,
            "keepAliveIntervalSeconds": 300,
            "summaryIntervalSeconds": 10,
        },
    )

    # 9. Same again but with a clearly unique callback URL, to rule out
    # any "this callbackUrl already exists" dedup logic.
    probe(
        "WEBHOOK REGISTER with unique callback URL",
        "POST",
        f"{base}/api/v1/harvester/webhooks",
        token,
        body={
            "callbackUrl": "https://httpbin.org/anything/hydration-probe-001",
            "instrumentIdentifiers": instrument_id,
            "mode": 0,
            "enableKeepAlive": False,
            "keepAliveIntervalSeconds": 300,
            "summaryIntervalSeconds": 10,
        },
    )


if __name__ == "__main__":
    main()
