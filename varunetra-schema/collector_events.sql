CREATE TABLE IF NOT EXISTS collector_events (
    id     SERIAL PRIMARY KEY,
    ts     INTEGER NOT NULL,
    event  TEXT NOT NULL,
    detail TEXT
);