CREATE TABLE beds (
    bed_id   TEXT PRIMARY KEY,
    ward     TEXT NOT NULL,
    room     TEXT NOT NULL,
    label    TEXT NOT NULL
);

CREATE TABLE devices (
    device_id TEXT PRIMARY KEY,
    bed_id    TEXT NOT NULL UNIQUE REFERENCES beds(bed_id),
    last_seen TIMESTAMPTZ
);

CREATE TABLE patients (
    patient_id       TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    intake_target_ml INT  NOT NULL DEFAULT 2000
);

CREATE TABLE stays (
    stay_id       BIGSERIAL PRIMARY KEY,
    patient_id    TEXT NOT NULL REFERENCES patients(patient_id),
    bed_id        TEXT NOT NULL REFERENCES beds(bed_id),
    admitted_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    discharged_at TIMESTAMPTZ
);

-- One open stay per bed
CREATE UNIQUE INDEX one_open_stay_per_bed
    ON stays(bed_id) WHERE discharged_at IS NULL;

CREATE TABLE events (
    ts              TIMESTAMPTZ NOT NULL,
    device_id       TEXT NOT NULL REFERENCES devices(device_id),
    type            TEXT NOT NULL,
    payload         JSONB NOT NULL,
    intake_delta_ml INT,
    PRIMARY KEY (ts, device_id)
);

CREATE INDEX ON events (device_id, ts DESC);

-- Raw load-cell readings (one row per sample sent by the gateway).
-- Audited so the classifier output can always be recomputed from source.
CREATE TABLE measurements (
    ts          TIMESTAMPTZ NOT NULL,
    device_id   TEXT NOT NULL REFERENCES devices(device_id),
    weight_g    REAL NOT NULL,
    cup_present BOOLEAN,
    PRIMARY KEY (ts, device_id)
);

CREATE INDEX ON measurements (device_id, ts DESC);

CREATE TABLE alerts (
    alert_id    BIGSERIAL PRIMARY KEY,
    bed_id      TEXT NOT NULL REFERENCES beds(bed_id),
    kind        TEXT NOT NULL,
    raised_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ
);
CREATE INDEX ON alerts (bed_id) WHERE resolved_at IS NULL;

-- Seed: a small ward so the app boots with content
INSERT INTO beds (bed_id, ward, room, label) VALUES
    ('B-101', 'Ward A', '101', 'A-101-1'),
    ('B-102', 'Ward A', '102', 'A-102-1'),
    ('B-103', 'Ward A', '103', 'A-103-1'),
    ('B-201', 'Ward B', '201', 'B-201-1'),
    ('B-202', 'Ward B', '202', 'B-202-1');

INSERT INTO devices (device_id, bed_id) VALUES
    ('dev-001', 'B-101'),
    ('dev-002', 'B-102'),
    ('dev-003', 'B-103'),
    ('dev-004', 'B-201'),
    ('dev-005', 'B-202');

INSERT INTO patients (patient_id, name, intake_target_ml) VALUES
    ('P-001', 'Alice Chen',    2000),
    ('P-002', 'Bob Martinez',  2500),
    ('P-003', 'Carol Singh',   1800);

INSERT INTO stays (patient_id, bed_id) VALUES
    ('P-001', 'B-101'),
    ('P-002', 'B-102'),
    ('P-003', 'B-201');
