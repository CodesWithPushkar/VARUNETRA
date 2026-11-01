CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id       SERIAL PRIMARY KEY,
    spill_id          TEXT NOT NULL REFERENCES spills (spill_id) ON DELETE CASCADE,
    created_utc       TEXT NOT NULL,
    origin            TEXT NOT NULL,
    source            TEXT NOT NULL,
    min_lat           DOUBLE PRECISION,
    max_lat           DOUBLE PRECISION,
    min_lon           DOUBLE PRECISION,
    max_lon           DOUBLE PRECISION,
    window_start_utc  TEXT,
    window_end_utc    TEXT,
    pad_hours         DOUBLE PRECISION,
    coverage_fraction DOUBLE PRECISION,
    rows_found        INTEGER NOT NULL DEFAULT 0,
    vessels_found     INTEGER NOT NULL DEFAULT 0,
    warnings          TEXT
);