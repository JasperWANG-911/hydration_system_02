# Integration Notes

A running log of the decisions, discoveries, and current state of the
hydration monitoring system. Companion to [ARCHITECTURE.md](ARCHITECTURE.md)
(which describes the *intended* design) — this file documents what is
actually built, what works end-to-end, what is stubbed, and the
non-obvious things learned along the way that future contributors will
want to know.

For a one-screen "what is this and how do I run it" intro, see
[README.md](README.md).

---

## 1. Implementation status

### ✅ Done and working end-to-end

| Component | Notes |
|---|---|
| Postgres schema + seed | `db/init.sql`. Plain Postgres 16 (TimescaleDB removed for school-project simplicity). |
| FastAPI backend skeleton | Lifespan-managed background tasks; SQLAlchemy async + asyncpg. |
| HTTP ingest endpoint | `POST /ingest/measurements`. Routes between local `fake_gateway.py` format and the real Camgenium webhook format based on payload shape. |
| Camgenium OAuth2 client | `client_credentials` flow refactored into `refresh_token` flow once we learned the issued credential type. Rotated refresh tokens persist back to `.env`. |
| Camgenium webhook registration | `POST /api/v1/harvester/webhooks` on backend startup; correct body shape established (see §4). |
| Camgenium webhook ingest | Adapter in `backend/app/routes/ingest.py` parses the real delivery shape (`{webhookId, instrumentIdentifier, timestamp, data: [...]}`). |
| Per-device classifier registry | `backend/app/interactions/` — wraps teammate's `PlatformInteractionClassifier` + `SessionManager` so multiple devices can run concurrently. |
| Alert evaluator | `app/alerts.py`, run once a minute by `app/tasks.py`. Rules: `device_offline`, `no_drink`, `behind_target`. |
| Dashboard + corridor view | Server-rendered Jinja2 + SSE for live updates. |
| Local fake gateway | `scripts/fake_gateway.py` simulates 5 coasters posting to `/ingest/measurements`. Used for offline dev without BLE / Camgenium. |
| BLE "fake Pico" | `scripts/fake_pico_ble.py` (uses `bleak`) pretends to be the Pico, writes to the phone's GATT characteristic. Bypasses the missing BLE radio on the base Pi Pico. |
| Camgenium probing tool | `scripts/probe_camgenium.py` runs auth + a battery of API probes for diagnosing webhook reg / data-flow issues. Has a `cleanup` subcommand to delete leftover httpbin test webhooks. |

### 🟡 Partial / placeholder

| Component | What's there | What's missing |
|---|---|---|
| Camgenium webhook handler | Persists one placeholder `measurements` row per record + updates `device.last_seen` so the dashboard shows the device as online. | Decoding the base64 `dataValue` (raw BLE frame bytes) into actual weight readings. Format is not documented; we'd need the L2S2 / Camgenium Pico SDK reference. |
| Pi Pico firmware | `firmware/main.py` has the sampling loop + tare logic; `firmware/hx711.py` is the PIO-driver from MicroPython. | The `transmit()` function is a stub that prints to stdout — base Pico has no BLE radio. See §5. |
| Webhook signature verification | `_verify_signature()` in `routes/ingest.py` does HMAC-SHA256 against `INGEST_SHARED_SECRET` if set. | Camgenium may not actually send a signature header in this form — we didn't test against the live signed delivery because secret-verification is disabled in dev (`INGEST_SHARED_SECRET=change-me`). |
| Device → bed mapping | `_ensure_device_for_camgenium()` auto-creates a placeholder bed under "Unassigned" ward on first webhook delivery for an unknown instrument. | Admin UI flow to *reassign* such a device to a proper bed + open a stay. The HTML/admin route exists but isn't exercised. |

### ❌ Not done

| Component | Why |
|---|---|
| Real BLE on Pi Pico | Base Pico has no radio. Either swap to Pico W (pin-compatible) or add an external BLE module (HM-10 etc.). Decision pending on hardware procurement. |
| Decoding `dataValue` to a weight reading | Frame layout not yet known. Without it, the classifier can't see real net-weight changes and won't produce drink/refill events from real data. |
| Production deployment | Demo path is `docker compose up` + `ngrok http 8000`. No CI, no real public hosting, no TLS cert management. |
| ESP32 / hardware alternatives | Not pursued — would require rewriting the HX711 driver since `firmware/hx711.py` is RP2040 PIO-specific. |
| Test suite beyond `test_session_manager.py` | Teammate-written unit tests for the session manager are passing under the new import path. No integration tests, no route-level tests. |

---

## 2. End-to-end data flow (as currently implemented)

```
┌────────────────────────────────────────────────────────────────────────┐
│  Path A — local dev (no BLE, no Camgenium)                            │
│  ────────────────────────────────────────                              │
│  scripts/fake_gateway.py                                              │
│    │ HTTP POST /ingest/measurements                                   │
│    │ {"device_id": "dev-001", "samples": [...]}                       │
│    ▼                                                                   │
│  FastAPI /ingest/measurements                                         │
│    → IngestPayload model                                              │
│    → registry.get(device_id).process_sample(weight_g, ts)             │
│    → write measurements + events to DB                                │
│    → broker.publish() → SSE → dashboard                               │
└────────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────────┐
│  Path B — real demo (BLE + Camgenium)                                 │
│  ────────────────────────────────────                                  │
│  scripts/fake_pico_ble.py  (or future real Pi Pico W firmware)        │
│    │ BLE GATT write to characteristic ...4a02                         │
│    ▼                                                                   │
│  Android phone running "Pico Relay" app                               │
│    │ HTTPS POST to Camgenium NBA URL                                  │
│    ▼                                                                   │
│  Camgenium harvester (apisoftdev.l2s2.com)                            │
│    │ Webhook POST to our public URL (ngrok)                           │
│    │ {"webhookId": "...", "instrumentIdentifier": "018d7ae...",       │
│    │  "timestamp": "...", "data": [{...},{...}]}                      │
│    ▼                                                                   │
│  FastAPI /ingest/measurements                                         │
│    → CamgeniumWebhookPayload model                                    │
│    → _ingest_camgenium(): persist placeholder measurements,           │
│      update device.last_seen, broker.publish() for SSE                │
└────────────────────────────────────────────────────────────────────────┘
```

Both paths land at the same `/ingest/measurements` endpoint. The handler
peeks at the JSON body and dispatches to the right adapter:

```
peek = json.loads(raw)
if peek.get("test") is True:               → Camgenium verification ping → 200
elif "instrumentIdentifier" in peek and "data" in peek:  → Camgenium real delivery
else:                                       → internal IngestPayload (fake_gateway)
```

---

## 3. Key architectural decisions

### 3.1 MQTT → HTTP webhook ingest

Original `ARCHITECTURE.md` described an MQTT-based ingest: ESP32 →
Mosquitto → FastAPI subscriber. We removed all of that early on because
the actual deployment target uses Camgenium's Harvester API, which
delivers via HTTP webhooks. The change touched:

- Deleted `mosquitto/`, `aiomqtt` dep, MQTT subscribe loop, `mqtt_loop`
- Added `backend/app/camgenium.py` (OAuth + webhook lifecycle)
- Added `backend/app/routes/ingest.py` (HTTP POST endpoint)
- Renamed `scripts/fake_device.py` → `scripts/fake_gateway.py`, now
  posts via HTTP

### 3.2 Classifier lives in the backend, not the firmware

The Pico firmware emits **raw weight samples** (or BLE bytes that are
de-facto raw). The platform state machine (cup-on / cup-removed /
settling / net-change) runs in `app/interactions/classifier.py`.

Rationale: easier to tune thresholds in Python without reflashing the
MCU; the firmware stays simple enough to write in 50 LoC; same
classifier code can replay historical data from `measurements` after a
rule change.

### 3.3 Per-device runner registry

The teammate's `PlatformInteractionClassifier` is stateful — it
remembers `pre_removal_weight`, `last_stable_weight`, etc. To handle N
devices concurrently the ingest path goes through `registry.get(device_id)`,
which lazily creates one `(classifier, session)` pair per device. State
lives in memory; lost on backend restart (acceptable for a school
deployment; reconstruction takes ~2 seconds once samples start flowing
again).

### 3.4 Auto-create devices on first Camgenium delivery

When Camgenium pushes data for an instrument we have no record of, the
handler creates a placeholder `beds` row (`bed_id = CG-<instrument_id[:8]>`,
ward = `Unassigned`) and a matching `devices` row. This keeps the FK
constraint happy and surfaces the device on the dashboard so a nurse
can re-assign it via the admin UI. No silent drops.

### 3.5 Polling left as a fallback option (not active)

Mid-debug we considered abandoning webhook for polling
(`GET /instruments/{id}/data?since_date=...`) when registration kept
400'ing. The fix turned out to be a schema mismatch (§4.3), so we
stayed on webhook. The polling path was never implemented.

---

## 4. Camgenium integration — the hard part

Most of the integration headaches came from this layer. Everything in
this section was discovered by probing, not documented.

### 4.1 Auth: OAuth2 `refresh_token` flow on a public client

- Identity provider: SoftSilicon Keycloak realm
  `https://keycloaksoftdev.l2s2.com/realms/SoftSilicon`
- Client ID: `cg-harvester-public-api` (public client — **no client_secret**)
- Credentials issued: one access token (10 min) + one refresh token (30 days)
- Grant type for backend: `refresh_token`
- Token endpoint:
  `https://keycloaksoftdev.l2s2.com/realms/SoftSilicon/protocol/openid-connect/token`

Token refresh request:

```
POST <token_url>
Content-Type: application/x-www-form-urlencoded

grant_type=refresh_token&client_id=cg-harvester-public-api&refresh_token=<rt>
```

Implementation: `_ensure_access_token()` in `app/camgenium.py` caches the
access token in memory until `expires_in - 30s` before refreshing.

### 4.2 Refresh token rotation IS enabled

Every successful refresh issues a **new** refresh token; the old one is
immediately invalidated. This was discovered the hard way — the first
manual refresh during diagnostics killed the value in `.env`.

Mitigation: `_persist_rotated_refresh_token()` rewrites `.env` whenever
the response carries a new refresh token. `docker-compose.yml` mounts
`./.env` into the container so writes survive restarts.

If the backend is offline long enough that nobody refreshes within 30
days, the refresh token in `.env` expires and someone must log in again
and update `.env` manually.

### 4.3 Webhook `POST /webhooks` body — the schema lies

The Harvester swagger declares `instrumentIdentifiers` as an array
(`string[]`). It is not.

```
POST /api/v1/harvester/webhooks
Content-Type: application/json

{
  "callbackUrl": "https://abc123.ngrok-free.dev/ingest/measurements",
  "instrumentIdentifiers": "018d7ae542914998,4779fbb9b035ce55",  // STRING, comma-separated
  "mode": 0,
  "enableKeepAlive": false,
  "keepAliveIntervalSeconds": 300,
  "summaryIntervalSeconds": 10
}
```

Submitting `instrumentIdentifiers: ["018d7ae542914998"]` returns
`400 Bad Request` with **an empty body** — no error message, nothing
in headers. The only way to discover this was to call `GET /webhooks`
and look at the shape of existing successful webhook records, then
mirror it on `POST`.

This is captured in `register_webhook()` which joins our list with
`,` before sending.

### 4.4 Webhook delivery payload

Camgenium delivers to our callback URL with this shape:

```json
{
  "webhookId": "6765e9a7-57a8-4f71-8f5f-cebc57e176a7",
  "instrumentIdentifier": "018d7ae542914998",
  "timestamp": "2026-05-20T12:28:23.4919101Z",
  "data": [
    {
      "deviceDataId": 1846728,
      "uniqueId": "4a69e824-ff0b-4acb-8117-daf0b63c7f12",
      "dataValue": "AYQTteUhngEAAABhkMCMQQph0ptzQg==",
      "timestamp": "2026-05-13T15:12:44.5766667",
      "timeHandled": "2026-05-13T15:12:44.5766667",
      "packetType": 0,
      "instrumentType": null,
      "networkId": 7,
      "status": 1
    },
    ...
  ]
}
```

`dataValue` is base64-encoded — the raw bytes that the BLE central
wrote to the phone's GATT characteristic. Decoding it requires
knowing the on-the-wire frame layout (currently unknown — see §6).

### 4.5 Verification ping

When a webhook is registered, Camgenium sends an immediate test request
to verify reachability:

```json
{"test": true, "timestamp": "2026-05-20T12:28:22.4047617Z"}
```

The handler short-circuits this with a 200 response. (Camgenium seems
to register the webhook regardless of the verification result — we
returned 400 the first time and registration still succeeded — but
returning 200 is the right thing.)

### 4.6 Backfill on registration

Right after a webhook is registered, Camgenium fires a single large
delivery containing **all historical data** for the subscribed
instrument, going back days (~32 KB body containing records from
5+ days ago in our case). Subsequent deliveries are incremental.

The ingest handler must be idempotent: it is, thanks to
`ON CONFLICT DO NOTHING` on the composite primary key
`(ts, device_id)` for both `measurements` and `events`.

### 4.7 Other Harvester endpoints touched

| Endpoint | Result |
|---|---|
| `GET /api/v1/harvester/webhooks` | ✅ Lists our account's webhooks. Useful to see leftovers. |
| `POST /api/v1/harvester/webhooks` | ✅ Returns 201 + webhook id, given correct body shape. |
| `DELETE /api/v1/harvester/webhooks/{id}` | ✅ Used by cleanup tooling. |
| `POST /api/v1/harvester/webhooks/{id}/keepalive` | Untested in practice (`enableKeepAlive=false` works fine). |
| `GET /api/v1/harvester/instruments/{id}/data` | ✅ Returns historical records. Useful for verifying the instrument actually exists in Camgenium before registering a webhook. |
| `GET /api/v1/harvester/instruments/data` | Requires `?instruments=<comma-separated>` query param. Not a "list all" endpoint. |
| `POST /api/v1/harvester/instruments/{id}/data` | Accepts `{data: <base64>, timestamp, packetType}`. **Direction is downlink** (sending data *to* the instrument), not what we wanted. Confirmed via the response message: `"Client data queued for delivery to instrument"`. |
| `POST .../instruments/{id}/data/service` | `403 Forbidden` — requires elevated role. |
| `POST .../webhooks/service` | `403 Forbidden` — same. |

---

## 5. Phone gateway + BLE notes

### 5.1 Phone app: "Pico Relay" (from L2S2)

Provided by the supervisor; we don't write a custom app.
Responsibilities:

- Open BLE Peripheral with name `L2S2R-<device_uid>`, service UUID
  `8a3e4d2f-1b6c-4f9e-a7d8-3e5b2c1f4a01`
- Accept GATT writes from a BLE central (the Pico, or our laptop
  running `fake_pico_ble.py`)
- POST received bytes to its assigned NBA URL on Camgenium
  (`https://apisoftdev.l2s2.com/api/v1/nba/<8-char-id>`)
- Local queue if NBA URL is unreachable; auto-flush on reconnect

App fields:

- **NBA Status** must show "Connected" (green dot). If "retrying", the
  phone can't reach the Camgenium NBA endpoint — check wifi / eduroam.
- **BLE Peripheral** must show "Advertising — waiting for central"
  before a central can connect. If status is "Idle", tap **Start**.
- **DEVICE UID** is the instrument identifier in Camgenium. Persistent
  per Pico Relay installation.
- **OUT MTU** starts at 16 bytes (default link-layer) and renegotiates
  to ~510 bytes after a central connects.
- **BYTES IN / OUT** is the wire counter. May show 0 even when writes
  are succeeding (cosmetic UI bug, sometimes).

### 5.2 Discovered GATT layout

By introspecting after a successful `bleak` connect:

```
service 8a3e4d2f-1b6c-4f9e-a7d8-3e5b2c1f4a01
  char  ...4a02   props=[write, write-without-response]   ← Pico → phone (sensor data uplink)
  char  ...4a03   props=[notify, read]                    ← phone → Pico (downlink, purpose TBD)
  char  ...4a04   props=[notify]                          ← phone → Pico (second downlink)
```

`...4a02` is the only writable characteristic, and that's what we
write into.

### 5.3 macOS BLE quirks

- **`write-without-response` is unreliable on macOS.** Writes complete
  locally but never reach the peripheral, with no error. Switching to
  `write` (with response) fixed this in `fake_pico_ble.py`.
- macOS exposes BLE device addresses as UUIDs, not MAC addresses (a
  privacy feature). The phone sees the Mac as a regular MAC like
  `1C:1D:D3:E5:10:D4`.
- First-time BLE access prompts for permission on Terminal.app /
  iTerm.app under System Settings → Privacy → Bluetooth. Without it
  scans return empty silently.

### 5.4 Pi Pico hardware status

The base Raspberry Pi Pico (RP2040) **has no radio** — no WiFi, no BLE.
For BLE on the Pico, options are:

1. **Pico W** (RP2040 + Infineon CYW43439). Pin-compatible. MicroPython
   has a built-in `bluetooth` module. **Recommended**.
2. External BLE module (HM-10, nRF52, …) wired via UART. More wiring,
   more firmware (AT-command bridge), worse power.
3. Different MCU (ESP32). Would require rewriting `firmware/hx711.py`
   since it depends on RP2040 PIO.

Decision pending. Until then, `fake_pico_ble.py` from a laptop with
`bleak` substitutes for the Pico in demos.

---

## 6. Open questions / unknowns

### 6.1 BLE frame format (Pico → phone characteristic)

The phone forwards whatever bytes we write into `...4a02` as `dataValue`
in webhook deliveries. We don't know:

- How many bytes per frame the phone / Camgenium expects (it accepts
  arbitrary lengths up to MTU, but possibly clips at some boundary)
- Whether the phone or Camgenium adds a wrapper around our bytes (the
  base64-decoded `dataValue` in some examples is 22 bytes long; a
  16-byte write from us implies a 6-byte wrapper, but this isn't
  confirmed — those 22-byte samples may be from prior tenants of the
  same instrument)
- Whether there's a required header (version byte, frame type, sequence
  number) that the Camgenium devkit's parser expects

**Action**: ask the supervisor for the L2S2 Pico SDK / sample firmware
to see what bytes a "real" coaster sends.

### 6.2 WebhookMode enum

`mode` is documented as `WebhookMode: integer$int32, Enum: [0, 1]`. No
names, no descriptions. We default to `0`; existing webhooks on the
account also use `0`. `1` may correspond to summary mode (given the
sibling field `summaryIntervalSeconds`) but unconfirmed.

### 6.3 Where do instrument identifiers come from?

The instrument `018d7ae542914998` matched the Pico Relay app's
**Device UID** on this particular phone. We don't know:

- Whether installing a fresh Pico Relay on a new phone auto-creates a
  new instrument in Camgenium (probably yes — that's the "provisioner"
  audience in the OAuth token)
- Whether instruments can be created via the API (no obvious endpoint)
- How instrument IDs are recycled / unbound from old phones

For demos we only need this one instrument, so the question is parked.

### 6.4 Webhook signature secret

We send `INGEST_SHARED_SECRET` in the webhook reg body (if set), but
Camgenium hasn't been observed signing deliveries. The signing scheme
(HMAC-SHA256 of the body, included in some `X-Camgenium-Signature`
header) is a guess based on convention. Verification is disabled by
default (`change-me` placeholder) so this hasn't bitten us.

---

## 7. Operational notes

### 7.1 First-time setup

```bash
git clone <repo>
cd hydration_system_02
cp .env.example .env
# Edit .env: add CAMGENIUM_REFRESH_TOKEN, PUBLIC_INGEST_URL, CAMGENIUM_INSTRUMENT_IDS
docker compose up --build
```

### 7.2 Local-only demo (no Camgenium, no phone, no BLE)

```bash
docker compose up
# In another terminal:
python scripts/fake_gateway.py
# Open http://localhost:8000/corridor
```

`PUBLIC_INGEST_URL` can be empty for this mode — webhook registration
silently skips.

### 7.3 Full demo path (BLE → phone → Camgenium → backend)

```bash
# Terminal 1 — public tunnel
ngrok http 8000
# Copy the https://*.ngrok-free.dev URL into .env as PUBLIC_INGEST_URL

# Terminal 2 — backend (this will register the Camgenium webhook on startup)
docker compose up --build

# Terminal 3 — push BLE data (Mac standing in for the Pico)
pip install bleak     # one-time
python scripts/fake_pico_ble.py

# Phone:
#   1. Open Pico Relay
#   2. Verify NBA Status = Connected
#   3. Tap "Start" under BLE Peripheral; status becomes "Advertising"
```

Open `http://localhost:8000/corridor` once data starts flowing.

### 7.4 Camgenium diagnostics

```bash
# General probing — what does our account see?
python scripts/probe_camgenium.py

# Clean up leftover httpbin test webhooks
python scripts/probe_camgenium.py cleanup
```

### 7.5 Resetting Camgenium webhooks

If too many test webhooks accumulate on the account, `GET /webhooks`
shows them all. Use `scripts/probe_camgenium.py cleanup` to delete any
whose `callbackUrl` contains `httpbin.org`. For other URLs, copy the
`webhookId` and `DELETE /api/v1/harvester/webhooks/{id}` via Swagger.

### 7.6 Rotating Camgenium credentials

If `.env`'s `CAMGENIUM_REFRESH_TOKEN` becomes stale (e.g., backend
hasn't run for ~30 days, or someone refreshed manually outside the
backend):

1. Log into the Camgenium UI / Swagger
2. Obtain a new refresh token
3. Paste into `.env`'s `CAMGENIUM_REFRESH_TOKEN` line
4. Restart the backend

The backend will pick up the new value and resume rotating it.

### 7.7 Common failure modes

| Symptom | Likely cause |
|---|---|
| `Camgenium webhook registration failed` on startup | `CAMGENIUM_REFRESH_TOKEN` stale (>30 days) or empty. Re-login, update `.env`, restart. |
| `400 Bad Request` returned to Camgenium delivery | Payload shape mismatch — check the `ingest body` log line for the actual JSON. Camgenium changed the format, our adapter needs updating. |
| BLE script can't find `L2S2R-...` | Phone is locked, Pico Relay is backgrounded, or BLE Peripheral isn't started. On the phone tap "Start" under BLE Peripheral. |
| `bleak` writes succeed but BYTES IN stays at 0 | macOS dropped `write-without-response`. Switch to `response=True`. |
| Dashboard empty even after `fake_gateway.py` runs | Classifier needs ~20 samples to fill its stability window. Wait ~3 seconds. |
| `instrumentIdentifiers must be a STRING` errors | The OpenAPI schema is wrong — pass a comma-separated string, not an array, on `POST /webhooks`. Already fixed in `camgenium.py`. |

---

## 8. Glossary

| Term | Meaning |
|---|---|
| **NBA** | "Native Bridge Agent" — Camgenium's term for the relay (the phone running Pico Relay). |
| **Instrument** | A registered sensor stream in Camgenium. Identified by `instrumentIdentifier`. In our setup, mapped 1:1 to a Pico Relay installation (= one phone). |
| **Device** (in our DB) | One row in `devices`. `device_id` matches `instrumentIdentifier` for Camgenium-sourced data, or `dev-001`/etc. for local fake data. |
| **Coaster** | The physical platform with the load cell on it. Becomes a "device" once registered. |
| **Bed** | Physical bed in the hospital. Has zero or one open `stay`. Devices are bed-bound (1:1). |
| **Stay** | One admission of a patient to a bed. Open while `discharged_at IS NULL`. |
| **Drink event** | Inferred by the classifier when a `CUP_REMOVED → ... → SETTLING_AFTER_RETURN` cycle ends with net negative weight. |
| **Refill event** | Same cycle but with net positive weight beyond `meaningful_change_threshold`. |
| **`dataValue`** | base64-encoded raw bytes of one BLE frame, as relayed by Camgenium. |
| **Webhook** | Camgenium subscription that pushes new `data[]` records to our `callbackUrl`. |
| **Verification ping** | `{"test": true}` body Camgenium fires once when a webhook is created, to check the URL is reachable. |
| **`ingest_shared_secret`** | Optional secret used to HMAC-sign webhook deliveries; verification skipped when blank/`change-me`. |
| **Rotation** (refresh token) | Keycloak invalidates old refresh tokens on each refresh. We persist the new value back to `.env`. |
