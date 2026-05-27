# DRIP Hydration Monitoring System

A nurse-facing dashboard backed by DRIP bedside button boxes. Each unit
has three buttons (+ / − / sleep), a reminder LED, and an LCD; patients
log their own intake by pressing the buttons.

```
Pi Pico W (3 buttons + LED + LCD)  ─BLE─▶  Camgenium relay  ─webhook─▶
    FastAPI backend  ─SSE─▶  corridor screen
```

## Quick start (local demo, no real hardware)

Prereqs: Docker, Python 3.11+, `uv` or `pip`.

```bash
# 1. Start Postgres + the backend
docker compose up --build

# 2. (optional) Seed extra demo data
python scripts/seed.py

# 3. Drive fake button-box data
python scripts/fake_gateway.py

# 4. Open the dashboard
open http://localhost:8000          # bed grid + filter
open http://localhost:8000/corridor # nurse-station view
```

`fake_gateway.py` simulates five button boxes POSTing aggregated intake
events on a realistic schedule (heavier at mealtimes). Drink events show
up on the dashboard as they arrive.

## Connecting to Camgenium (for real-hardware demos)

Camgenium has to be able to reach your backend, so expose port 8000 via
a tunnel:

```bash
ngrok http 8000
# copy the https://*.ngrok.io URL it prints
```

Copy `.env.example` to `.env` and fill in:

```
CAMGENIUM_CLIENT_ID=<from your supervisor>
CAMGENIUM_CLIENT_SECRET=<from your supervisor>
PUBLIC_INGEST_URL=https://abc123.ngrok.io
INGEST_SHARED_SECRET=<random string, must match what Camgenium signs with>
```

Restart the backend; on lifespan startup it will register
`PUBLIC_INGEST_URL + /ingest/measurements` as an outgoing webhook on
Camgenium and start sending keepalives.

## Tests

```bash
cd backend
pytest
```

## Repo map

- `firmware/` — Raspberry Pi Pico W MicroPython (button box: 3-button
  sampling loop, LED, LCD, BLE transmit).
- `protocol/` — shared BLE wire format (`event_codec` + `framing`) used
  by both the firmware sender and the backend ingest decoder.
- `backend/app/` — FastAPI app, alerts, dashboard, and the DRIP
  bedside pipeline (buttons → session → alert → LED/display).
- `backend/app/interactions/` — session aggregator + per-device runner
  registry used by the ingest route.
- `scripts/` — `seed.py`, `fake_gateway.py` (HTTP sim),
  `fake_pico_ble.py` (BLE relay sim), Camgenium helpers.
- `db/init.sql` — Postgres schema + seed.
