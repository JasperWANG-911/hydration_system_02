# Hydration Monitoring System — Architecture

## 1. System overview

```
┌─────────────────────────┐
│   EDGE (per bed)        │
│  ESP32                  │
│   ├─ HX711 + load cell  │
│   └─ button             │
└──────────┬──────────────┘
           │ MQTT (publish events + heartbeat)
           ▼
┌─────────────────────────┐
│   Mosquitto MQTT broker │
└──────────┬──────────────┘
           │ subscribe
           ▼
┌─────────────────────────┐      ┌──────────────────────────┐
│ Ingestion worker        │─────▶│ Postgres + TimescaleDB   │
│ (FastAPI, async)        │      │  - events (hypertable)   │
│  - parses events        │      │  - devices, beds         │
│  - resolves bed→stay    │      │  - patients, stays       │
│  - fires alerts         │      │  - alerts                │
└─────────────────────────┘      └──────────┬───────────────┘
                                            │
                                 ┌──────────▼───────────────┐
                                 │ Web server (FastAPI)     │
                                 │  - Jinja2 HTML           │
                                 │  - SSE for live updates  │
                                 └──────────┬───────────────┘
                                            │
                                 ┌──────────▼───────────────┐
                                 │ Nurse-station browser    │
                                 │  HTML + ~10 lines JS     │
                                 └──────────────────────────┘
```

Two backend processes share the DB so heavy MQTT traffic doesn't starve the dashboard.

## 2. Layer responsibilities

**Edge (ESP32 firmware)**
- Sample load cell at 1 Hz, smooth (moving average)
- Detect events (not raw weight):
  - `drink` — sustained weight drop ≥ threshold
  - `refill` — sustained weight gain
  - `removed` / `placed` — cup off/on scale
  - `button` — debounced press
- Publish events + 30s heartbeat over MQTT
- Buffer events locally if offline; flush on reconnect

**Broker**
- Mosquitto. Topics keyed by device:
  - `hydration/<device_id>/event`
  - `hydration/<device_id>/heartbeat`
  - `hydration/<device_id>/status` (last-will → `offline`)

**Ingestion worker**
- Subscribes to `hydration/+/+`
- Resolves `device_id → bed_id → current open stay → patient_id`
- Writes raw event + computed intake delta to DB
- Runs alert rules on each event
- Stateless across restarts (state lives in DB)

**Web server**
- Reads DB for dashboard queries
- Pushes updates to browsers via SSE channel
- Renders HTML with Jinja2

**Frontend**
- Server-rendered HTML
- One `<script>` block opens an `EventSource`, swaps DOM nodes on push
- No build step, no framework

## 3. Data model

```sql
beds      (bed_id PK, ward, room, label)
devices   (device_id PK, bed_id FK UNIQUE)
patients  (patient_id PK, name, intake_target_ml)
stays     (stay_id PK, patient_id FK, bed_id FK,
           admitted_at, discharged_at NULL)
events    (ts, device_id FK, type, payload JSONB,
           intake_delta_ml)        -- hypertable on ts
alerts    (alert_id PK, bed_id FK, kind, raised_at,
           resolved_at NULL)
```

Key invariants:
- Exactly one `stays` row per bed with `discharged_at IS NULL` at any time
- Events are attributed to a patient via the open stay for their bed at event time
- Intake is *computed* from events (never stored as a running total) — lets you recompute history if rules change

## 4. Event flow (a drink)

1. Patient drinks → cup weight drops 150g
2. ESP32 firmware confirms the drop is sustained (not noise) → publishes
   `{"type":"drink","ts":...,"delta_g":-150}` to `hydration/dev-042/event`
3. Ingestion worker receives → looks up `dev-042` → `bed B-12` → open stay → patient `P-77`
4. Inserts into `events`; updates today's intake aggregate
5. Checks alert rules (target progress, last-drink gap)
6. Publishes a change event on an internal pub/sub
7. Web server's SSE channel pushes a minimal update to any browser viewing bed B-12 or its ward

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

Empty grid by default — nurse picks a filter. Attention panel is always visible.

## 6. Suggested repo layout

```
hydration_system/
├── firmware/                # PlatformIO / Arduino-ESP32
│   └── src/main.cpp
├── backend/
│   ├── app/
│   │   ├── main.py          # FastAPI web app
│   │   ├── ingest.py        # MQTT subscriber entrypoint
│   │   ├── models.py        # SQLAlchemy
│   │   ├── events.py        # event parsing + intake logic
│   │   ├── alerts.py        # alert rules
│   │   ├── routes/          # dashboard, bed detail, admit/discharge
│   │   ├── templates/       # Jinja2
│   │   └── static/          # css, the ~10 lines of JS
│   ├── migrations/          # Alembic
│   └── pyproject.toml
├── docker-compose.yml       # mosquitto + postgres + ingest + web
└── docs/
```

## 7. Tech stack summary

| Concern | Choice |
|---|---|
| MCU | ESP32 |
| Sensor | HX711 + load cell |
| Transport | MQTT (Mosquitto) |
| Backend | Python, FastAPI, async |
| DB | Postgres + TimescaleDB |
| ORM / migrations | SQLAlchemy + Alembic |
| Frontend | Jinja2 HTML + SSE + small vanilla JS |
| Deployment | docker-compose (uni project scale) |

## 8. Open decisions

- **Button function** — deferred. Candidates: call nurse (short) + tare (long), "I drank" confirmation, acknowledge alarm.
- **Admit/discharge UX** — nurse-managed page on the dashboard vs. seeded from CSV/external system.
- **Intake target reset cadence** — daily at midnight vs. per-stay.
- **Alert rule set** — e.g. "no drink in 3h during waking hours," "intake <50% of target by 6pm," "device offline >10min," "button pressed."

## 9. Build order (first slice)

1. Data model + Alembic migrations
2. MQTT event contract + a fake-device script that publishes plausible traffic
3. Ingestion worker (subscribe → resolve → write)
4. Dashboard read path (attention panel + filtered grid)
5. SSE push for live updates
6. Real firmware on ESP32
