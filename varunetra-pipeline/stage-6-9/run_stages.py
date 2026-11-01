#!/usr/bin/env python3
"""
Stages 6-9 orchestrator. Runs after db_ingest.py has inserted the stage-5 slicks into spills.

    python stage-6-9/run_stages.py --scene-time 2026-09-18T01:02:38+00:00

For each selected slick (not a false positive, inside SLICK_BBOX if set, highest mean_prob first):
    6  forcing for the scene (fetched once per scene and bounding box, cached)
    7  backtrack the slick polygon from the scene time
    8  AIS for the cloud's area and the backtrack window (Neon or CSV)
    9  rank vessels with the stage-5 age window; write results to Neon

A failing slick is logged and skipped. The exit code is non-zero if any slick failed.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from shapely.geometry import shape

HERE = Path(__file__).resolve().parent
PIPELINE_DIR = HERE.parent
for sub in ("stage-6", "stage-7", "stage-8", "stage-9", "stage-6-9"):
    sys.path.insert(0, str(PIPELINE_DIR / sub))

import ais_source            # noqa: E402  stage 8
import backtrack             # noqa: E402  stage 7
import config                # noqa: E402  stages 6-9 settings
import db_results            # noqa: E402  Neon writes
import fetch_forcing         # noqa: E402  stage 6
import rank_vessels          # noqa: E402  stage 9

log = logging.getLogger("stage6_9")
AGED_GEOJSON = PIPELINE_DIR / "stage-5" / "aged_slicks.geojson"


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(PIPELINE_DIR))
    except ValueError:
        return str(path)


def _cloud_stats(lon: np.ndarray, lat: np.ndarray):
    ok = np.isfinite(lon) & np.isfinite(lat)
    lo, la = lon[ok], lat[ok]
    c_lat, c_lon = float(la.mean()), float(lo.mean())
    dlat = np.radians(la - c_lat)
    dlon = np.radians(lo - c_lon) * np.cos(np.radians(c_lat))
    rms_km = float(np.sqrt(np.mean((dlat ** 2 + dlon ** 2))) * 6371.0088)
    return c_lat, c_lon, rms_km


def _select(features, s: config.Settings):
    picked = []
    for f in features:
        props = f.get("properties", {})
        if props.get("is_false_positive") or not props.get("spill_id"):
            continue
        geom = shape(f["geometry"])
        if s.slick_bbox:
            min_lon, min_lat, max_lon, max_lat = s.slick_bbox
            c = geom.centroid
            if not (min_lon <= c.x <= max_lon and min_lat <= c.y <= max_lat):
                continue
        picked.append(f)
    picked.sort(key=lambda f: float(f["properties"].get("mean_prob") or 0.0), reverse=True)
    return picked[: s.max_slicks]


def _union_box(features, pad: float):
    bounds = [shape(f["geometry"]).bounds for f in features]
    return (min(b[0] for b in bounds) - pad, min(b[1] for b in bounds) - pad,
            max(b[2] for b in bounds) + pad, max(b[3] for b in bounds) + pad)


def process_slick(feat, t_acq: datetime, s: config.Settings, currents: Path, wind: Path) -> None:
    props = feat["properties"]
    spill_id = props["spill_id"]
    out_dir = s.output_dir / spill_id
    out_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []

    prior_min = float(props["min_age_hr"])
    prior_max = min(float(props["max_age_hr"]), float(s.runtime_hours))
    prior_est = float(props["est_age_hr"])
    if prior_min > prior_max:
        warnings.append("stage-5 min age above max age; window reset to 0..max")
        prior_min = 0.0

    geom = shape(feat["geometry"])
    bt = backtrack.run_backtrack(
        geom, t_acq, currents, wind, output_path=out_dir / "backtrack.nc",
        runtime_hours=s.runtime_hours, n_particles=s.n_particles, windage=s.windage,
        kh=s.kh, dt_minutes=s.dt_minutes, seed=s.seed,
    )
    lon, lat, times = bt["lon"], bt["lat"], bt["times"]
    if not np.isfinite(lon).any():
        raise RuntimeError("every particle was lost; forcing or slick position is wrong")

    ok = np.isfinite(lon) & np.isfinite(lat)
    box = (float(lon[ok].min()) - 0.5, float(lat[ok].min()) - 0.5,
           float(lon[ok].max()) + 0.5, float(lat[ok].max()) + 0.5)
    t0 = t_acq - timedelta(hours=s.runtime_hours + 1)
    t1 = t_acq + timedelta(hours=1)
    ais_df = ais_source.load_ais(s.ais_source, box, t0, t1,
                                 database_url=s.database_url, csv_path=s.ais_csv_path,
                                 folder=s.ais_dir, cache_dir=str(s.cache_dir / "ais"))
    vessels = ais_source.to_vessels(ais_df)
    if s.ais_source == "neon":
        ais_label = "neon:ais_positions"
    elif s.ais_source == "dma_folder":
        ais_label = f"dma_daily:{Path(s.ais_dir).name}"
    else:
        ais_label = f"csv:{Path(s.ais_csv_path).name}"
    if not vessels:
        warnings.append("no AIS vessels in the window and box")

    res = rank_vessels.rank_vessels(lon, lat, times, t_acq, prior_min, prior_max, vessels)
    res["bbox"] = box
    res["pad_hours"] = 1.0

    top = res["ranking"][0] if res["ranking"] else None
    if top and top["best_age_h"] is not None:
        age_h, source = float(top["best_age_h"]), "backtrack_top_candidate"
    else:
        age_h, source = prior_est, "backtrack_prior_estimate"
        warnings.append("no candidate vessel; origin uses the stage-5 estimate")
    k = int(np.clip(round(age_h), 0, lon.shape[1] - 1))
    o_lat, o_lon, o_rad = _cloud_stats(lon[:, k], lat[:, k])
    coverage = float(np.isfinite(lon[:, k]).mean())

    heatmap = out_dir / f"origin_{k}h.png"
    _plot(geom, lon[:, k], lat[:, k], spill_id, k, heatmap)
    (out_dir / "ranking.json").write_text(json.dumps(res, default=str, indent=2), encoding="utf-8")

    db_results.write_slick_result(
        s.database_url, spill_id=spill_id, t_acq=t_acq, result=res, ais_df=ais_df,
        ais_label=ais_label,
        origin={"lat": o_lat, "lon": o_lon, "radius_km": o_rad, "source": source},
        heatmap_path=_rel(heatmap), window=(t0, t1),
        coverage=coverage, warnings=warnings,
    )
    log.info("%s: age %.1f h, origin (%.3f, %.3f) r=%.1f km, top=%s (P=%.2f)", spill_id, age_h,
             o_lat, o_lon, o_rad, top["id"] if top else "none", top["P"] if top else res["P_none"])


def _plot(geom, lon, lat, spill_id, k, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    ok = np.isfinite(lon) & np.isfinite(lat)
    fig, ax = plt.subplots(figsize=(7, 6), dpi=130)
    if ok.any():
        ax.hist2d(lon[ok], lat[ok], bins=60, cmap="magma")
    xs, ys = geom.exterior.xy if geom.geom_type == "Polygon" else geom.geoms[0].exterior.xy
    ax.plot(xs, ys, color="cyan", linewidth=1)
    ax.set_title(f"{spill_id}: particle cloud {k} h before scene")
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Stages 6-9: backtrack and rank vessels for each slick")
    ap.add_argument("--scene-time", required=True, help="scene acquisition time, ISO 8601")
    ap.add_argument("--aged-geojson", default=str(AGED_GEOJSON))
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s [STAGE6-9] [%(levelname)s] %(message)s")

    try:
        s = config.load_settings()
    except (RuntimeError, ValueError) as exc:
        log.error("configuration: %s", exc)
        return 2

    t_acq = backtrack.parse_utc_naive(a.scene_time)
    features = json.loads(Path(a.aged_geojson).read_text(encoding="utf-8")).get("features", [])
    picked = _select(features, s)
    picked_ids = {f["properties"]["spill_id"] for f in picked}
    unpicked = [f["properties"]["spill_id"] for f in features
                if f.get("properties", {}).get("spill_id") and f["properties"]["spill_id"] not in picked_ids]
    db_results.mark_not_selected(s.database_url, unpicked)
    if unpicked:
        log.info("%d slick(s) not ranked this run (MAX_SLICKS_PER_SCENE / SLICK_BBOX); marked not_selected", len(unpicked))
    if not picked:
        log.info("no slicks selected (SLICK_BBOX=%s, MAX_SLICKS_PER_SCENE=%d)", s.slick_bbox, s.max_slicks)
        return 0
    log.info("selected %d slick(s) for scene %s", len(picked), t_acq.isoformat())

    try:
        box = _union_box(picked, s.pad_deg)
        currents, wind = fetch_forcing.ensure_forcing(
            box, t_acq, s.runtime_hours, s.cache_dir, s.cmems_dataset_id,
            s.cmems_username, s.cmems_password, s.cds_url, s.cds_key,
            wind_source=s.wind_source,
        )
    except (fetch_forcing.ForcingError, ValueError, OSError) as exc:
        log.error("forcing unavailable, no slick processed: %s", exc)
        return 1

    failures = 0
    for feat in picked:
        sid = feat["properties"]["spill_id"]
        try:
            process_slick(feat, t_acq, s, currents, wind)
        except Exception:
            failures += 1
            log.exception("%s failed; continuing with the next slick", sid)
    if failures:
        log.error("%d of %d slick(s) failed", failures, len(picked))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
