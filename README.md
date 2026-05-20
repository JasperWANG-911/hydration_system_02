# Hydration Monitoring System

A nurse-facing dashboard backed by load-cell coasters that estimate
patient fluid intake.

```
Pi Pico + HX711 + load cell  ─BLE─▶  phone gateway  ─HTTPS─▶
    Camgenium Harvester  ─webhook─▶  FastAPI backend  ─SSE─▶  corridor screen
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design.

## Quick start (local demo, no real hardware)

Prereqs: Docker, Python 3.11+, `uv` or `pip`.

```bash
# 1. Start Postgres + the backend
docker compose up --build

# 2. (optional) Seed extra demo data
python scripts/seed.py

# 3. Drive fake coaster data
python scripts/fake_gateway.py

# 4. Open the dashboard
open http://localhost:8000          # bed grid + filter
open http://localhost:8000/corridor # nurse-station view
```

`fake_gateway.py` simulates five coasters posting raw weight samples at
10 Hz. After ~10 s of warm-up (the classifier's stability buffer needs
to fill), the dashboard will start showing drink/refill events.

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

- `firmware/` — Raspberry Pi Pico MicroPython (HX711 driver, sampling
  loop, BLE transmit stub).
- `backend/app/` — FastAPI app, classifier, alerts, dashboard.
- `backend/app/interactions/` — platform state machine + session
  aggregator + per-device runner registry.
- `scripts/` — `seed.py`, `fake_gateway.py`.
- `db/init.sql` — Timescale schema + seed.
- `platform_interaction_model_spec.txt` — design notes the classifier
  is built against.
