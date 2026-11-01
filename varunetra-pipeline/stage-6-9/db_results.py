"""Write stage 6-9 results to Neon. One transaction per slick.

Tables used (schema from migrate.py):
    spills            UPDATE  age, origin, origin heatmap path, model version
    snapshots         INSERT  one row per backtrack/AIS run
    spill_ais         INSERT  AIS positions of the top candidates inside the window
    vessel_rankings   INSERT  one row per ranked vessel, plus one 'none' row for P(none)
reported_culprits is not touched: it holds externally reported culprits, not model output.
"""
from __future__ import annotations

import json
import logging
import uuid
from contextlib import closing
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd

log = logging.getLogger("stage6_9.db")

SETTINGS_VERSION = "stage6-9-v1"
MODEL_VERSION = "parcels3_backtrack+rank_vessels_v1"
TOP_N_RANKED = 5
TOP_N_AIS = 5
# Confidence labels are arbitrary display bands on the relative probability. Not calibrated.
CONFIDENCE_BANDS = ((0.5, "high"), (0.2, "medium"))


def confidence_label(p: float) -> str:
    for threshold, label in CONFIDENCE_BANDS:
        if p >= threshold:
            return label
    return "low"


def _iso(t: datetime) -> str:
    return t.replace(tzinfo=timezone.utc).isoformat() if t.tzinfo is None else t.isoformat()


def write_slick_result(database_url: str, *, spill_id: str, t_acq: datetime, result: Dict,
                       ais_df: pd.DataFrame, ais_label: str, origin: Dict, heatmap_path: str,
                       window: tuple, coverage: float, warnings: List[str]) -> None:
    import psycopg2

    run_id = str(uuid.uuid4())
    now = _iso(datetime.now(timezone.utc))
    ranking = result["ranking"]
    top = ranking[:TOP_N_RANKED]
    top_ids = {r["id"] for r in top}
    t0, t1 = window

    with closing(psycopg2.connect(database_url)) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM spills WHERE spill_id = %s FOR UPDATE", (spill_id,))
                if cur.fetchone() is None:
                    raise RuntimeError(f"no spills row for {spill_id}; was db_ingest run?")

                top_age = top[0]["best_age_h"] if top and top[0]["best_age_h"] is not None else None
                sigma = None
                if top and top[0].get("age_range_h"):
                    lo, hi = top[0]["age_range_h"]
                    sigma = (hi - lo) / 2.0
                cur.execute(
                    """
                    UPDATE spills SET
                        age_hours = COALESCE(%s, age_hours),
                        age_sigma_hours = %s,
                        origin_lat = %s, origin_lon = %s, origin_radius_km = %s,
                        origin_source = %s, origin_heatmap_path = %s,
                        model_version = %s
                    WHERE spill_id = %s
                    """,
                    (top_age, sigma, origin["lat"], origin["lon"], origin["radius_km"],
                     origin["source"], heatmap_path, MODEL_VERSION, spill_id),
                )

                cur.execute(
                    """
                    INSERT INTO snapshots (spill_id, created_utc, origin, source, min_lat, max_lat,
                        min_lon, max_lon, window_start_utc, window_end_utc, pad_hours,
                        coverage_fraction, rows_found, vessels_found, warnings)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING snapshot_id
                    """,
                    (spill_id, now, "backtrack", ais_label,
                     result["bbox"][1], result["bbox"][3], result["bbox"][0], result["bbox"][2],
                     _iso(t0), _iso(t1), result["pad_hours"], coverage,
                     int(len(ais_df)), int(ais_df["mmsi"].nunique()) if len(ais_df) else 0,
                     "; ".join(warnings) or None),
                )
                snapshot_id = cur.fetchone()[0]

                rows = []
                for r in top:
                    counts = ais_df[ais_df["mmsi"] == int(r["id"])] if r["id"].isdigit() else ais_df.iloc[0:0]
                    rows.append((
                        run_id, spill_id, snapshot_id, "ranked", r["rank"],
                        int(r["id"]) if r["id"].isdigit() else None, r["name"], None,
                        float(r["P"]), confidence_label(float(r["P"])),
                        r["closest_km"], r["closest_km"], r["best_age_h"], None,
                        int(len(counts)),
                        json.dumps({"P": float(r["P"]), "score": float(r["score"]), "ages_used": int(r["ages_used"]),
                                    "age_range_h": r.get("age_range_h")}),
                        "gap" if any("gap" in s for s in r["reasons"]) else "",
                        json.dumps(r["reasons"]), ais_label, SETTINGS_VERSION, now,
                    ))
                rows.append((
                    run_id, spill_id, snapshot_id, "none", None, None, None, None,
                    result["P_none"], confidence_label(result["P_none"]), None, None, None, None,
                    None, json.dumps({"area_km2": result["area_km2"],
                                      "n_vessels_scored": result["n_vessels_scored"]}),
                    "", json.dumps(["probability that the culprit is not among the AIS vessels"]),
                    ais_label, SETTINGS_VERSION, now,
                ))
                if rows:
                    from psycopg2.extras import execute_values
                    execute_values(cur, """
                        INSERT INTO vessel_rankings (run_id, spill_id, snapshot_id, status, rank, mmsi,
                            name, vessel_category, score, confidence, min_dist_km, effective_dist_km,
                            hours_before_image, gap_at_closest_min, n_reports, components_json, flags,
                            reasons_json, ais_source, settings_version, created_utc)
                        VALUES %s
                    """, rows)

                ais_top = ais_df[ais_df["mmsi"].astype(str).isin(top_ids)] if len(ais_df) else ais_df
                if len(ais_top):
                    from psycopg2.extras import execute_values
                    spill_rows = [(
                        snapshot_id, spill_id, int(m.mmsi), int(m.ts), float(m.lat), float(m.lon),
                        None if pd.isna(m.sog) else float(m.sog),
                        None if pd.isna(m.cog) else float(m.cog),
                        None if pd.isna(m.name) else str(m.name),
                        None if pd.isna(m.vessel_type) else str(int(m.vessel_type)),
                        None if pd.isna(m.length_m) else float(m.length_m),
                        ais_label,
                    ) for m in ais_top.itertuples(index=False)]
                    execute_values(cur, """
                        INSERT INTO spill_ais (snapshot_id, spill_id, mmsi, ts, lat, lon, sog, cog,
                            name, vessel_type, length_m, source)
                        VALUES %s
                        ON CONFLICT (snapshot_id, mmsi, ts) DO NOTHING
                    """, spill_rows, page_size=1000)
            conn.commit()
            log.info("%s: stored run %s (%d ranked rows, snapshot %s)", spill_id, run_id, len(top), snapshot_id)
        except Exception:
            conn.rollback()
            raise


def mark_not_selected(database_url: str, spill_ids: List[str]) -> None:
    """Pending slicks that this run did not rank (top-N limit or region filter) are marked so
    part 2 does not pick them again."""
    if not spill_ids:
        return
    import psycopg2
    from contextlib import closing as _closing
    with _closing(psycopg2.connect(database_url)) as conn, conn.cursor() as cur:
        cur.execute("UPDATE spills SET origin_source = 'not_selected' "
                    "WHERE spill_id = ANY(%s) AND origin_source IS NULL", (list(spill_ids),))
        conn.commit()