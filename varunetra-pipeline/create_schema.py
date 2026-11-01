#!/usr/bin/env python3
"""
Create the Neon tables that are missing. Schema only: copies no data, drops nothing.
Same SQL as create_schema() in neon/migrate.py. Every statement is CREATE ... IF NOT EXISTS.

    python create_schema.py
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT = Path(__file__).resolve().parent
# Step 1: the live spills table may have no unique key on spill_id. The tables below
# reference spills(spill_id), and PostgreSQL requires a unique key on that column.
# Added only when missing. Fails with a clear message if existing rows are duplicated.
PRE_SQL = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_index i
        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
        WHERE i.indrelid = 'public.spills'::regclass
          AND i.indisunique
          AND i.indnatts = 1
          AND a.attname = 'spill_id'
    ) THEN
        ALTER TABLE spills ALTER COLUMN spill_id SET NOT NULL;
        ALTER TABLE spills ADD CONSTRAINT spills_spill_id_key UNIQUE (spill_id);
    END IF;
END $$;
"""

SCHEMA_SQL = """
        CREATE TABLE IF NOT EXISTS spills (
            spill_id             TEXT PRIMARY KEY,
            kind                 TEXT NOT NULL CHECK (kind IN ('past', 'new')),
            image_time_utc       TEXT NOT NULL,
            scene_name           TEXT,
            polygon_geojson      TEXT NOT NULL,
            centroid_lat         DOUBLE PRECISION,
            centroid_lon         DOUBLE PRECISION,
            area_km2             DOUBLE PRECISION,
            detection_confidence DOUBLE PRECISION,
            model_version        TEXT,
            age_hours            DOUBLE PRECISION,
            age_sigma_hours      DOUBLE PRECISION,
            origin_lat           DOUBLE PRECISION NOT NULL,
            origin_lon           DOUBLE PRECISION NOT NULL,
            origin_radius_km     DOUBLE PRECISION NOT NULL,
            origin_source        TEXT,
            origin_heatmap_path  TEXT,
            window_start_utc     TEXT NOT NULL,
            window_end_utc       TEXT NOT NULL,
            window_source        TEXT,
            data_source          TEXT,
            notes                TEXT,
            created_utc          TEXT NOT NULL
        );

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

        CREATE TABLE IF NOT EXISTS vessels (
            mmsi        INTEGER PRIMARY KEY,
            name        TEXT,
            vessel_type INTEGER,
            length_m    DOUBLE PRECISION,
            imo         TEXT,
            callsign    TEXT,
            updated_ts  INTEGER
        );

        CREATE TABLE IF NOT EXISTS collector_events (
            id     SERIAL PRIMARY KEY,
            ts     INTEGER NOT NULL,
            event  TEXT NOT NULL,
            detail TEXT
        );

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

        CREATE TABLE IF NOT EXISTS vessel_rankings (
            id                  SERIAL PRIMARY KEY,
            run_id              TEXT NOT NULL,
            spill_id            TEXT NOT NULL REFERENCES spills (spill_id) ON DELETE CASCADE,
            snapshot_id         INTEGER REFERENCES snapshots (snapshot_id) ON DELETE CASCADE,
            status              TEXT NOT NULL,
            rank                INTEGER,
            mmsi                INTEGER,
            name                TEXT,
            vessel_category     TEXT,
            score               DOUBLE PRECISION,
            confidence          TEXT,
            min_dist_km         DOUBLE PRECISION,
            effective_dist_km   DOUBLE PRECISION,
            hours_before_image  DOUBLE PRECISION,
            gap_at_closest_min  DOUBLE PRECISION,
            n_reports           INTEGER,
            components_json     TEXT,
            flags               TEXT,
            reasons_json        TEXT,
            ais_source          TEXT,
            settings_version    TEXT,
            created_utc         TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_reported_culprits_spill
            ON reported_culprits (spill_id);

        CREATE INDEX IF NOT EXISTS idx_ais_positions_ts
            ON ais_positions (ts);

        CREATE INDEX IF NOT EXISTS idx_ais_positions_lat_lon
            ON ais_positions (lat, lon);

        CREATE INDEX IF NOT EXISTS idx_events_ts
            ON collector_events (ts);

        CREATE INDEX IF NOT EXISTS idx_snapshots_spill
            ON snapshots (spill_id);

        CREATE UNIQUE INDEX IF NOT EXISTS idx_spill_ais_unique_v2
            ON spill_ais (snapshot_id, mmsi, ts);

        CREATE INDEX IF NOT EXISTS idx_rankings_spill
            ON vessel_rankings (spill_id, run_id);
        """


def main() -> int:
    load_dotenv(PROJECT / ".env")
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        print("DATABASE_URL is not set in .env")
        return 2
    import psycopg2
    conn = psycopg2.connect(url, connect_timeout=15)
    try:
        with conn.cursor() as cur:
            cur.execute(PRE_SQL)
            cur.execute(SCHEMA_SQL)
        conn.commit()
    except Exception as exc:
        conn.rollback()
        print("schema creation failed:", exc)
        return 1
    finally:
        conn.close()
    print("schema ready (existing tables and data untouched; spills.spill_id is now unique)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
