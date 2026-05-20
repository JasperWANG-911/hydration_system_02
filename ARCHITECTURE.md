# Hydration Monitoring System — Architecture

## 1. System overview

```
┌──────────────────────────────────┐
│ EDGE (per bed coaster)           │
│  Load cell + HX711 ADC + MCU     │
│  (Raspberry Pi Pico, MicroPython)│
└─────────────┬────────────────────┘
              │ BLE GATT (raw weight samples, 10 Hz)
              ▼
┌──────────────────────────────────┐
│ Phone (BLE → HTTPS gateway)      │
│  Camgenium-supplied relay app —  │
│  we do not write a custom app.   │
└─────────────┬────────────────────┘
              │ HTTPS POST
              ▼
┌──────────────────────────────────┐
│ Camgenium Harvester (cloud)      │
│  /api/v1/harvester/instruments   │
│  /api/v1/harvester/webhooks      │
└─────────────┬────────────────────┘
              │ Webhook POST  (HMAC-signed)
              ▼
┌──────────────────────────────────┐    ┌──────────────────────────┐
│ FastAPI backend                  │───▶│ Postgres + TimescaleDB   │
│  /ingest/measurements            │    │  - measurements (hyper)  │
│  per-device classifier registry  │    │  - events (hyper)        │
│  → DrinkEvent / RefillEvent      │    │  - alerts                │
│  alert_loop, lifespan webhook    │    │  - beds/devices/stays/…  │
└─────────────┬────────────────────┘    └──────────┬───────────────┘
              │ SSE                                │
              ▼                                    ▼
┌─────────────────────────────┐         (dashboard read queries)
│ Nurse corridor screen       │
│ Jinja2 HTML + ~10 lines JS  │
└─────────────────────────────┘
```

A single FastAPI process owns the ingest endpoint, the periodic alert
evaluator, and the dashboard / SSE server. For a school-project sized
deployment a separate worker buys nothing.

## 2. Layer responsibilities

**Edge — Raspberry Pi Pico firmware** (`firmware/`)

- Sample the HX711 at 10 Hz, apply `CALIBRATION_FACTOR`.
- Emit *raw* samples only (`weight_g`, `cup_present`, `ts_ms`); the
  state-machine that infers `drink` / `refill` lives in the backend.
  Keeping the MCU dumb means thresholds are tuned by editing Python
  config, not reflashing firmware.
- BLE transport is a stub today. The base Pico has no radio; either
  swap to a Pico W (pin-compatible, MicroPython has built-in
  `bluetooth`) or wire an external BLE module over UART. Until then
  `transmit()` prints to stdout.

**Phone gateway**

- Not in our scope. Camgenium supplies a relay app that pairs over BLE
  and forwards readings to its Harvester API. We treat the phone as a
  pass-through link.

**Camgenium Harvester (cloud)**

- Stores instrument data and fans it out via outgoing webhooks.
- Endpoints we call:
  - `POST /api/v1/harvester/webhooks` — register our public ingest URL
    on backend startup
  - `POST /api/v1/harvester/webhooks/{id}/keepalive` — every 5 minutes
- Auth: OAuth2 `client_credentials` against the configured Keycloak
  realm.

**FastAPI backend** (`backend/app/`)

- `routes/ingest.py` — `POST /ingest/measurements`. Verifies HMAC
  signature, persists raw samples to `measurements`, feeds them through
  the per-device classifier registry, persists derived events.
- `interactions/` — owns the state machine and the session aggregator
  (originally implemented in the root of the repo by a teammate; moved
  here). `registry.DeviceRegistry` holds one `(classifier, session)`
  pair per device.
- `camgenium.py` — OAuth token cache + webhook lifecycle task.
- `tasks.py` — `alert_loop`, runs once a minute. Stateless; rebuilds
  current alert state from the DB.
- `alerts.py` — `device_offline`, `no_drink`, `behind_target`, plus
  immediate `button` raises from the ingest path (button support is
  TBD, see "Open decisions").
- `routes/dashboard.py`, `routes/corridor.py`, `routes/beds.py`,
  `routes/admin.py` — read-side. Server-rendered Jinja2 + one
  EventSource on each page.

**Frontend**

- One `<script>` per page that opens `/sse` and replaces nodes when a
  relevant event arrives. No bundler, no framework.

## 3. Data model

```sql
beds          (bed_id PK, ward, room, label)
devices       (device_id PK, bed_id FK UNIQUE, last_seen)
patients      (patient_id PK, name, intake_target_ml)
stays         (stay_id PK, patient_id FK, bed_id FK,
               admitted_at, discharged_at NULL)
measurements  (ts, device_id FK, weight_g, cup_present)         -- hypertable on ts
events        (ts, device_id FK, type, payload JSONB,
               intake_delta_ml)                                  -- hypertable on ts
alerts        (alert_id PK, bed_id FK, kind, raised_at,
               resolved_at NULL)
```

Invariants:

- One open `stays` row per bed (`UNIQUE` partial index).
- Events are attributed to a patient via the open stay for their bed at
  event time.
- Intake is computed from `events.intake_delta_ml` — never stored as a
  running total. That lets us recompute history when classifier
  thresholds change, by replaying `measurements`.

## 4. Sample flow (a drink)

1. Patient lifts cup, sips, replaces.
2. Firmware emits ~50 raw weight samples covering: stable-with-cup,
   weight drops below empty threshold, settling, new stable weight.
3. Phone → Camgenium → webhook POST hits `/ingest/measurements` with a
   batch of samples for `dev-042`.
4. Backend persists all samples to `measurements` and replays them
   through the `dev-042` runner:
   - Classifier transitions `CUP_PRESENT_STABLE → WAITING_FOR_RETURN
     → SETTLING_AFTER_RETURN → NET_WEIGHT_LOSS`.
   - SessionManager records a `DrinkEvent(volume_ml=...)`.
5. The runner is drained; one `drink` row is inserted into `events`
   with `intake_delta_ml` set.
6. `update_device_heartbeat` bumps `devices.last_seen`.
7. The in-process pub/sub broker publishes a `{kind: "event", ...}`;
   any browser viewing this bed's ward picks it up via SSE and refreshes.
8. Next `alert_loop` tick re-evaluates `behind_target` /
   `device_offline` / `no_drink`.

## 5. Dashboard layout

```
┌────────────────────────────────────────────────────────┐
│ ATTENTION (8)   [alerts list — bed, kind, age]         │
├────────────────────────────────────────────────────────┤
│ Filter: [ward ▼] [floor ▼] [status ▼]  Search: [____]  │
├────────────────────────────────────────────────────────┤
│ Grid of bed cards (filtered, sorted by severity)       │
│ Each card: bed#, patient name, intake/target bar,      │
│            last drink, status badge                    │
└────────────────────────────────────────────────────────┘
```

Empty grid by default — nurse picks a filter. Attention panel always
visible. The dedicated corridor view at `/corridor` shows the same
attention data scaled for a wall-mounted screen.

## 6. Repo layout

```
hydration_system/
├── firmware/                 # MicroPython for Pi Pico
│   ├── main.py               # sampling loop, transmit() stub
│   ├── hx711.py              # PIO-based HX711 driver
│   └── calibrate.py          # tare + raw-value stream
├── backend/
│   ├── app/
│   │   ├── main.py           # FastAPI app + lifespan
│   │   ├── camgenium.py      # OAuth + webhook lifecycle
│   │   ├── tasks.py          # alert_loop
│   │   ├── routes/
│   │   │   ├── ingest.py     # POST /ingest/measurements
│   │   │   ├── dashboard.py
│   │   │   ├── corridor.py
│   │   │   ├── beds.py
│   │   │   ├── admin.py
│   │   │   └── sse.py
│   │   ├── interactions/
│   │   │   ├── classifier.py # platform state machine
│   │   │   ├── session.py    # drink / refill aggregation
│   │   │   └── registry.py   # per-device runner registry
│   │   ├── models.py         # SQLAlchemy
│   │   ├── events.py         # device heartbeat helper
│   │   ├── alerts.py         # periodic alert rules
│   │   ├── queries.py        # dashboard reads
│   │   ├── db.py
│   │   ├── pubsub.py         # in-process broker for SSE
│   │   ├── config.py         # pydantic Settings
│   │   ├── templates/        # Jinja2
│   │   └── static/
│   ├── tests/
│   │   └── test_session_manager.py
│   └── pyproject.toml
├── db/init.sql               # schema + hypertables + seed
├── scripts/
│   ├── seed.py               # reseed beds/devices/patients/stays
│   └── fake_gateway.py       # simulate coasters posting to backend
├── docker-compose.yml
├── platform_interaction_model_spec.txt   # design notes for classifier
└── ARCHITECTURE.md
```

## 7. Tech stack

| Concern | Choice |
|---|---|
| MCU | Raspberry Pi Pico (RP2040), MicroPython |
| Sensor | HX711 + load cell |
| Edge → cloud | BLE → phone → Camgenium Harvester API |
| Cloud → backend | Camgenium outgoing webhook (HMAC-signed) |
| Backend | Python 3.11+, FastAPI, async, httpx |
| DB | Postgres + TimescaleDB |
| Frontend | Jinja2 + SSE + vanilla JS |
| Deployment | docker-compose; ngrok for the demo public URL |

## 8. Open decisions

- **MCU radio** — base Pico has no BLE. Pico W (pin-compatible) is the
  default recommendation; external HM-10 over UART is the fallback.
- **Camgenium webhook payload shape** — once credentials arrive, sniff
  the real body and update the alias in `routes/ingest.py:IngestPayload`
  if Camgenium's field names differ from `instrumentIdentifier`.
- **Button hardware** — not wired. Candidate functions: nurse call,
  manual "I drank" confirmation, alert acknowledge.
- **Session persistence across restarts** — classifier state lives in
  memory. After a backend restart, samples for the first ~2 seconds
  flow through but produce no events while the stability buffer fills.
  Acceptable for a school deployment.
- **Intake target reset cadence** — currently per-day midnight UTC.
  May want per-stay if multi-day patients drift across boundaries.

## 9. Build order

1. Schema + Alembic-free `init.sql` ✅
2. Camgenium HTTP ingest + HMAC verification ✅
3. Per-device classifier registry + DB write-through ✅
4. Dashboard + corridor read path + SSE ✅
5. Real firmware on Pico (once BLE hardware finalised)
