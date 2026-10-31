CREATE TABLE IF NOT EXISTS reported_culprits (
    id           SERIAL PRIMARY KEY,
    spill_id     TEXT NOT NULL REFERENCES spills (spill_id) ON DELETE CASCADE,
    mmsi         INTEGER,
    vessel_name  TEXT,
    imo          TEXT,
    source_title TEXT NOT NULL,
    source_url   TEXT,
    report_date  TEXT,
    notes        TEXT
);