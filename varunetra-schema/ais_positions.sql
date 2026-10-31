CREATE TABLE IF NOT EXISTS ais_positions (
    mmsi       INTEGER NOT NULL,
    ts         INTEGER NOT NULL,
    lat        DOUBLE PRECISION NOT NULL,
    lon        DOUBLE PRECISION NOT NULL,
    sog        DOUBLE PRECISION,
    cog        DOUBLE PRECISION,
    heading    DOUBLE PRECISION,
    nav_status INTEGER,
    msg_type   TEXT,
    ts_source  TEXT,
    PRIMARY KEY (mmsi, ts)
);
