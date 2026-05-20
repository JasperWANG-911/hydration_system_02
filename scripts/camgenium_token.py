"""Print a fresh Camgenium access token to stdout.

Caches the access token in `.access_token.cache` (gitignored, repo root)
so back-to-back CLI calls reuse the same 10-minute access token instead
of burning a refresh on every invocation. SoftSilicon's Keycloak has
refresh-token rotation on and rate-limits reuse — calling this script
naively from a curl loop will exhaust the reuse window in seconds.

Behaviour:
    - Cache hit (token not expired, 30s safety margin) → print, exit.
    - Cache miss → exchange refresh_token for new access_token;
      cache it; if Keycloak rotated the refresh token, persist the
      new value back to .env so the next process restart still works.

Usage:
    python scripts/camgenium_token.py
    TOKEN=$(python scripts/camgenium_token.py)
    curl -H "Authorization: Bearer $TOKEN" https://apisoftdev.l2s2.com/...
"""
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"
CACHE_FILE = REPO_ROOT / ".access_token.cache"
SAFETY_MARGIN_S = 30


def load_env() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def read_cache() -> dict | None:
    if not CACHE_FILE.exists():
        return None
    try:
        return json.loads(CACHE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def write_cache(access_token: str, expires_at: float) -> None:
    CACHE_FILE.write_text(
        json.dumps({"access_token": access_token, "expires_at": expires_at})
    )


def persist_rotated_refresh_token(new_token: str) -> None:
    """Rewrite CAMGENIUM_REFRESH_TOKEN in .env in place."""
    src = ENV_FILE.read_text()
    new_src, n = re.subn(
        r"^CAMGENIUM_REFRESH_TOKEN=.*$",
        f"CAMGENIUM_REFRESH_TOKEN={new_token}",
        src,
        flags=re.MULTILINE,
    )
    if n == 0:
        new_src = src.rstrip() + f"\nCAMGENIUM_REFRESH_TOKEN={new_token}\n"
    ENV_FILE.write_text(new_src)


def refresh(env: dict[str, str]) -> tuple[str, float]:
    body = urllib.parse.urlencode(
        {
            "grant_type": "refresh_token",
            "client_id": env["CAMGENIUM_CLIENT_ID"],
            "refresh_token": env["CAMGENIUM_REFRESH_TOKEN"],
        }
    ).encode()
    req = urllib.request.Request(env["CAMGENIUM_TOKEN_URL"], data=body)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode(errors="replace")
        print(f"HTTP {e.code}: {err_body}", file=sys.stderr)
        if "invalid_grant" in err_body:
            print(
                "\nThe refresh token in .env is no longer valid. Re-login "
                "via the Swagger UI / token endpoint and update "
                "CAMGENIUM_REFRESH_TOKEN in .env.",
                file=sys.stderr,
            )
        raise SystemExit(1)

    access = payload["access_token"]
    expires_at = time.time() + int(payload.get("expires_in", 600))
    rotated = payload.get("refresh_token")
    if rotated and rotated != env["CAMGENIUM_REFRESH_TOKEN"]:
        persist_rotated_refresh_token(rotated)
        print(
            "(rotated refresh_token saved to .env)", file=sys.stderr
        )
    return access, expires_at


def main() -> int:
    cache = read_cache()
    if cache and cache.get("expires_at", 0) > time.time() + SAFETY_MARGIN_S:
        print(cache["access_token"])
        return 0

    env = load_env()
    for key in (
        "CAMGENIUM_TOKEN_URL",
        "CAMGENIUM_CLIENT_ID",
        "CAMGENIUM_REFRESH_TOKEN",
    ):
        if not env.get(key):
            print(f"missing or empty {key} in {ENV_FILE}", file=sys.stderr)
            return 2

    access, expires_at = refresh(env)
    write_cache(access, expires_at)
    print(access)
    return 0


if __name__ == "__main__":
    sys.exit(main())
