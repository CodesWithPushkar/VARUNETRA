CREATE TABLE IF NOT EXISTS vessels (
    mmsi        INTEGER PRIMARY KEY,
    name        TEXT,
    vessel_type INTEGER,
    length_m    DOUBLE PRECISION,
    imo         TEXT,
    callsign    TEXT,
    updated_ts  INTEGER
);