#!/usr/bin/env python3
"""
PART 2: age, forcing, backtrack, AIS and vessel ranking for slicks stored by part 1.

Runs independently of part 1. It works on rows in Neon where origin_source IS NULL and
spill_id has the varunetra format (new rows from store_slicks.py).

For each scene (grouped by image_time_utc):
  5    stage-5/estimate_age.py in this environment; writes age_hours to spills
  6-9  stage-6-9/run_stages.py in the Parcels environment (STAGE_6_9_PYTHON)

    python part2_run.py                                   # all pending scenes
    python part2_run.py --scene-time 2026-09-18T01:02:38+00:00   # one scene only

Exit code 0 if every scene finished, 1 if any scene failed, 2 for configuration errors.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

PIPELINE_DIR = Path(__file__).resolve().parent
WORK_DIR = PIPELINE_DIR / "stage-5" / "part2"
log = logging.getLogger("part2")

MARK_OUTSIDE_SQL = r"""
    UPDATE spills SET origin_source = 'outside_region'
    WHERE origin_source IS NULL
      AND kind = 'new'
      AND spill_id ~ '^varunetra_[0-9]{8}T?[0-9]{6}_'
      AND NOT COALESCE(
            centroid_lon BETWEEN %(min_lon)s AND %(max_lon)s
        AND centroid_lat BETWEEN %(min_lat)s AND %(max_lat)s, FALSE)
"""

PENDING_SQL = r"""
    SELECT spill_id, polygon_geojson, detection_confidence, area_km2, image_time_utc
    FROM spills
    WHERE origin_source IS NULL
      AND kind = 'new'
      AND spill_id ~ '^varunetra_[0-9]{8}T?[0-9]{6}_'
      AND (%(scene)s::text IS NULL OR image_time_utc = %(scene)s::text)
    ORDER BY image_time_utc, spill_id
"""


def _region_box():
    """SLICK_BBOX=min_lon,min_lat,max_lon,max_lat. Must match the AIS coverage you use (Danish
    AIS covers Danish and nearby waters, so the Gulf of Oman / Hormuz slicks are excluded)."""
    raw = os.getenv("SLICK_BBOX", "").strip()
    if not raw:
        return None
    parts = [float(x) for x in raw.split(",")]
    if len(parts) != 4 or parts[0] >= parts[2] or parts[1] >= parts[3]:
        raise ValueError("SLICK_BBOX must be min_lon,min_lat,max_lon,max_lat")
    return tuple(parts)


def compact(iso: str) -> str:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S")


def row_to_feature(row) -> dict:
    spill_id, polygon, conf, area, image_time = row
    slick = spill_id.rsplit("_", 1)[1]
    return {
        "type": "Feature",
        "geometry": polygon if isinstance(polygon, dict) else json.loads(polygon),
        "properties": {
            "spill_id": spill_id,
            "slick_id": slick,
            "scene_time": compact(image_time),
            "scene_time_utc": image_time,
            "is_false_positive": False,
            "mean_prob": float(conf) if conf is not None else 0.5,
            "area_km2": float(area) if area is not None else 0.0,
        },
    }


def run_step(label: str, command: list[str]) -> int:
    log.info("[%s] %s", label, " ".join(command[:3]) + " ...")
    return subprocess.run(command, cwd=PIPELINE_DIR).returncode


def process_scene(conn, image_time: str, features: list[dict]) -> bool:
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    tag = compact(image_time)
    in_path = WORK_DIR / f"{tag}_input.geojson"
    out_path = WORK_DIR / f"{tag}_aged.geojson"
    in_path.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")

    rc = run_step("stage 5 age", [sys.executable, str(PIPELINE_DIR / "stage-5" / "estimate_age.py"),
                                  "--input", str(in_path), "--output", str(out_path), "--t0", image_time])
    if rc != 0:
        log.error("stage 5 failed for scene %s", image_time)
        return False

    aged = json.loads(out_path.read_text(encoding="utf-8")).get("features", [])
    with conn.cursor() as cur:
        for f in aged:
            props = f["properties"]
            cur.execute("UPDATE spills SET age_hours = %s WHERE spill_id = %s",
                        (float(props["est_age_hr"]), props["spill_id"]))
    conn.commit()
    log.info("scene %s: stored stage-5 age for %d slick(s)", image_time, len(aged))

    stage_python = os.getenv("STAGE_6_9_PYTHON", "").strip() or sys.executable
    rc = run_step("stages 6-9", [stage_python, str(PIPELINE_DIR / "stage-6-9" / "run_stages.py"),
                                 "--scene-time", image_time, "--aged-geojson", str(out_path)])
    if rc != 0:
        log.error("stages 6-9 finished with errors for scene %s (see the log above)", image_time)
        return False
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Part 2: age, backtrack and rank pending slicks")
    ap.add_argument("--scene-time", default=None, help="process only this scene (ISO 8601, UTC)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    load_dotenv(PIPELINE_DIR / ".env")
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        log.error("DATABASE_URL is not set in .env")
        return 2
    scene = None
    if a.scene_time:
        scene = datetime.fromisoformat(a.scene_time.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()

    import psycopg2
    conn = psycopg2.connect(url, connect_timeout=15)
    try:
        box = _region_box()
        if box:
            with conn.cursor() as cur:
                cur.execute(MARK_OUTSIDE_SQL, dict(zip(("min_lon", "min_lat", "max_lon", "max_lat"), box)))
                log.info("%d pending slick(s) outside SLICK_BBOX marked outside_region", cur.rowcount)
            conn.commit()
        with conn.cursor() as cur:
            cur.execute(PENDING_SQL, {"scene": scene})
            rows = cur.fetchall()
        if not rows:
            log.info("no pending slicks; nothing to do")
            return 0
        scenes: "OrderedDict[str, list[dict]]" = OrderedDict()
        for row in rows:
            scenes.setdefault(row[4], []).append(row_to_feature(row))
        log.info("%d pending slick(s) in %d scene(s)", len(rows), len(scenes))

        failed = 0
        for image_time, features in scenes.items():
            try:
                if not process_scene(conn, image_time, features):
                    failed += 1
            except Exception:
                conn.rollback()
                log.exception("scene %s failed; continuing", image_time)
                failed += 1
    finally:
        conn.close()

    if failed:
        log.error("%d scene(s) failed", failed)
        return 1
    log.info("part 2 finished for all scenes")
    return 0


if __name__ == "__main__":
    sys.exit(main())