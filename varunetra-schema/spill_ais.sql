CREATE TABLE IF NOT EXISTS spill_ais (
    id          SERIAL PRIMARY KEY,
    snapshot_id INTEGER NOT NULL REFERENCES snapshots (snapshot_id) ON DELETE CASCADE,
    spill_id    TEXT NOT NULL REFERENCES spills (spill_id) ON DELETE CASCADE,
    mmsi        INTEGER NOT NULL,
    ts          INTEGER NOT NULL,
    lat         DOUBLE PRECISION NOT NULL,
    lon         DOUBLE PRECISION NOT NULL,
    sog         DOUBLE PRECISION,
    cog         DOUBLE PRECISION,
    name        TEXT,
    vessel_type TEXT,
    length_m    DOUBLE PRECISION,
    source      TEXT NOT NULL
);