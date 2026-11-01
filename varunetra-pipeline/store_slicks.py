#!/usr/bin/env python3
"""
PART 1 (ingest): store the stage-4 slicks in Neon table `spills`.

Reads stage-4/final_slicks.geojson and inserts one row per slick that is not a false positive.
Stores stage 4's lat/lon directly as centroid_lat/centroid_lon.
Age and origin are not computed here: age_hours is left NULL and origin_source is left NULL,
which marks the row as pending for part2_run.py.

Safe to re-run: a spill_id that already exists is skipped (ON CONFLICT DO NOTHING).

    python store_slicks.py                       # uses stage-4/final_slicks.geojson
    python store_slicks.py --dry-run             # shows what would be stored, writes nothing
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

PIPELINE_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = PIPELINE_DIR / "stage-4" / "final_slicks.geojson"
log = logging.getLogger("store_slicks")

EXISTS_SQL = "SELECT 1 FROM spills WHERE spill_id = %(spill_id)s"

INSERT_SQL = """
    INSERT INTO spills (
        spill_id, polygon_geojson, centroid_lat, centroid_lon, area_km2,
        detection_confidence, kind, image_time_utc, origin_lat, origin_lon,
        origin_radius_km, window_start_utc, window_end_utc, created_utc
    ) VALUES (
        %(spill_id)s, %(polygon_geojson)s, %(centroid_lat)s, %(centroid_lon)s, %(area_km2)s,
        %(detection_confidence)s, 'new', %(image_time_utc)s, 0, 0,
        0, %(image_time_utc)s, %(image_time_utc)s, %(created_utc)s
    )
"""


def make_spill_id(props: dict) -> str:
    """Same format as stage 5 and part 2: varunetra_<scene>_<slick>."""
    scene = re.sub(r"[^A-Za-z0-9]", "", str(props.get("scene_time", "")))
    slick = re.sub(r"[^A-Za-z0-9]", "", str(props.get("slick_id", "")))
    if not scene or not slick:
        raise ValueError("feature needs scene_time and slick_id")
    return f"varunetra_{scene}_{slick}"


def _centroid(props: dict) -> tuple[float, float]:
    """Centroid = stage 4 lat/lon, inserted as is. No recalculation."""
    lat, lon = props.get("lat"), props.get("lon")
    if lat is None or lon is None:
        raise ValueError("stage-4 feature has no lat/lon for its centroid")
    return float(lat), float(lon)


def build_row(feature: dict, created: str) -> dict | None:
    props = feature.get("properties") or {}
    if props.get("is_false_positive") or not feature.get("geometry"):
        return None
    image_time = props.get("scene_time_utc")
    if not image_time:
        raise ValueError("feature has no scene_time_utc")
    centroid_lat, centroid_lon = _centroid(props)
    return {
        "spill_id": props.get("spill_id") or make_spill_id(props),
        "polygon_geojson": json.dumps(feature["geometry"]),
        "centroid_lat": centroid_lat,
        "centroid_lon": centroid_lon,
        "area_km2": float(props.get("area_km2") or 0.0),
        "detection_confidence": float(props.get("mean_prob") or 0.5),
        "image_time_utc": image_time,
        "created_utc": created,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Part 1: store stage-4 slicks in Neon")
    ap.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if not a.input.is_file():
        log.error("stage-4 output not found: %s (run the detection steps first)", a.input)
        return 1
    features = json.loads(a.input.read_text(encoding="utf-8")).get("features", [])
    created = datetime.now(timezone.utc).isoformat()
    rows = [r for r in (build_row(f, created) for f in features) if r]
    log.info("%d features in %s, %d to store (false positives skipped)", len(features), a.input.name, len(rows))
    if a.dry_run:
        for r in rows[:5]:
            log.info("would store %s (scene %s, area %.2f km2)", r["spill_id"], r["image_time_utc"], r["area_km2"])
        return 0

    load_dotenv(PIPELINE_DIR / ".env")
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        log.error("DATABASE_URL is not set in .env")
        return 2
    import psycopg2
    conn = psycopg2.connect(url, connect_timeout=15)
    try:
        inserted = 0
        with conn.cursor() as cur:
            for r in rows:
                # Check first instead of ON CONFLICT: the live spills table may have no
                # unique constraint on spill_id, which makes ON CONFLICT fail.
                cur.execute(EXISTS_SQL, r)
                if cur.fetchone():
                    continue
                cur.execute(INSERT_SQL, r)
                inserted += 1
        conn.commit()
    finally:
        conn.close()
    log.info("stored %d new spill(s); %d already existed", inserted, len(rows) - inserted)
    return 0


if __name__ == "__main__":
    sys.exit(main())