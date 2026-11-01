#!/usr/bin/env python3
"""
filter_false_positives.py - Stage 4: wind + spatial-cluster false-positive filter.

Reads Stage-3 detections (GeoJSON preferred, CSV also works), then:
  1. fetches 10 m wind speed (m/s) from Open-Meteo at each detection's location and the
     scene time (linearly interpolated between the two surrounding hours, UTC);
  2. flags calm-water detections (wind < --min-wind, default 1.0 m/s);
  3. flags large, dispersed clusters of blobs (weather fronts / calm patches);
  4. flags low-confidence / tiny detections (optional thresholds);
  5. writes survivors to final_slicks.geojson + final_slicks.csv, plus an audit CSV
     containing every detection with its flags and reject reasons.

Library use:
    from filter_false_positives import run_filter
    slicks = run_filter("detections_median_fill.geojson")

CLI:
    python filter_false_positives.py --input detections_median_fill.geojson
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import geopandas as gpd
import numpy as np
import pandas as pd
import requests

log = logging.getLogger("filter_false_positives")

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_MIN_AGE_DAYS = 7  # ERA5 lags real time by a few days


# --------------------------------------------------------------------------- #
# Time helpers
# --------------------------------------------------------------------------- #
def parse_scene_time(value) -> Optional[datetime]:
    """'20260918T010238' -> timezone-aware UTC datetime (None if unparseable)."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    s = str(value).strip()
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        ts = pd.Timestamp(s)
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        return ts.to_pydatetime()
    except Exception:
        return None


def _hour_key(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:00")


# --------------------------------------------------------------------------- #
# Open-Meteo client (batched, cached, rate-limit friendly)
# --------------------------------------------------------------------------- #
class OpenMeteoWind:
    def __init__(self, cache_path: Optional[str] = "wind_cache.json", batch_size: int = 50,
                 delay: float = 0.6, timeout: float = 60.0, max_retries: int = 6):
        self.batch_size = batch_size
        self.delay = delay
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "oil-slick-pipeline/1.0 (stage4 wind filter)"
        self.cache_path = Path(cache_path) if cache_path else None
        self.cache: dict[str, list] = {}
        if self.cache_path and self.cache_path.is_file():
            try:
                self.cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                log.warning("Ignoring unreadable wind cache %s", self.cache_path)

    # -- low level ---------------------------------------------------------- #
    def _request(self, url: str, params: dict):
        for attempt in range(1, self.max_retries + 1):
            try:
                r = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                wait = min(2 ** attempt, 60)
                log.warning("Network error (%s); retry %d/%d in %ds", exc, attempt, self.max_retries, wait)
                time.sleep(wait)
                continue
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429 or r.status_code >= 500:
                retry_after = r.headers.get("Retry-After")
                wait = float(retry_after) if retry_after and retry_after.isdigit() else \
                    (20 * attempt if r.status_code == 429 else min(5 * 2 ** (attempt - 1), 120))
                log.warning("HTTP %d from Open-Meteo; retry %d/%d in %.0fs",
                            r.status_code, attempt, self.max_retries, wait)
                time.sleep(wait)
                continue
            raise RuntimeError(f"Open-Meteo HTTP {r.status_code}: {r.text[:300]}")
        raise RuntimeError("Open-Meteo: retries exhausted")

    def _fetch(self, source: str, t0: datetime, t1: datetime, pts: list[tuple[float, float]]):
        params = {
            "latitude": ",".join(f"{la:.4f}" for la, _ in pts),
            "longitude": ",".join(f"{lo:.4f}" for _, lo in pts),
            "hourly": "wind_speed_10m",
            "wind_speed_unit": "ms",
            "timezone": "UTC",
            "start_date": t0.date().isoformat(),
            "end_date": t1.date().isoformat(),
        }
        data = self._request(ARCHIVE_URL if source == "archive" else FORECAST_URL, params)
        if isinstance(data, dict):  # single location -> dict, several -> list
            data = [data]
        k0, k1 = _hour_key(t0), _hour_key(t1)
        out = []
        for d in data:
            h = d.get("hourly", {})
            lookup = dict(zip(h.get("time", []), h.get("wind_speed_10m", [])))
            out.append((lookup.get(k0), lookup.get(k1)))
        if len(out) != len(pts):
            raise RuntimeError(f"Open-Meteo returned {len(out)} locations for {len(pts)} requested")
        return out

    # -- public ------------------------------------------------------------- #
    @staticmethod
    def _interp(pair, frac: float) -> Optional[float]:
        w0, w1 = pair
        if w0 is None:
            return w1
        if w1 is None or frac == 0:
            return w0
        return w0 * (1 - frac) + w1 * frac

    def get_scene_winds(self, when: datetime, pts: list[tuple[float, float]]) -> dict:
        """Return {(lat, lon): (wind_speed_ms, source)} for the points that could be resolved."""
        t0 = when.replace(minute=0, second=0, microsecond=0)
        t1 = t0 + timedelta(hours=1)
        frac = (when - t0).total_seconds() / 3600.0
        age_days = (datetime.now(timezone.utc) - when).total_seconds() / 86400.0
        order = ["archive", "forecast"] if age_days > ARCHIVE_MIN_AGE_DAYS else ["forecast", "archive"]

        result: dict = {}
        for source in order:
            need = []
            for p in pts:
                if p in result:
                    continue
                key = f"{source}|{_hour_key(t0)}|{p[0]:.4f}|{p[1]:.4f}"
                if key in self.cache:
                    result[p] = (self._interp(self.cache[key], frac), f"open-meteo-{source}")
                else:
                    need.append(p)
            for i in range(0, len(need), self.batch_size):
                chunk = need[i:i + self.batch_size]
                try:
                    pairs = self._fetch(source, t0, t1, chunk)
                except Exception as exc:  # fall through to next source / unavailable
                    log.warning("%s fetch failed for %d points: %s", source, len(chunk), exc)
                    continue
                for p, pair in zip(chunk, pairs):
                    if pair[0] is None and pair[1] is None:
                        continue
                    self.cache[f"{source}|{_hour_key(t0)}|{p[0]:.4f}|{p[1]:.4f}"] = list(pair)
                    result[p] = (self._interp(pair, frac), f"open-meteo-{source}")
                self._save_cache()
                time.sleep(self.delay)  # space requests out
            if all(p in result for p in pts):
                break
        return result

    def _save_cache(self) -> None:
        if self.cache_path:
            try:
                self.cache_path.write_text(json.dumps(self.cache), encoding="utf-8")
            except OSError as exc:
                log.warning("Could not write wind cache: %s", exc)


# --------------------------------------------------------------------------- #
# Spatial cluster filter (single-linkage == DBSCAN with min_samples=1, no sklearn needed)
# --------------------------------------------------------------------------- #
def connected_components(xy: np.ndarray, radius: float) -> np.ndarray:
    """Label points; two points are linked if within `radius` (same units as xy)."""
    n = len(xy)
    parent = list(range(n))

    def find(i: int) -> int:
        root = i
        while parent[root] != root:
            root = parent[root]
        while parent[i] != root:
            parent[i], i = root, parent[i]
        return root

    keys = np.floor(xy / radius).astype(np.int64)
    cells: dict = defaultdict(list)
    for i, (cx, cy) in enumerate(keys):
        cells[(int(cx), int(cy))].append(i)

    r2 = radius * radius
    for i, (cx, cy) in enumerate(keys):
        cand: list[int] = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                cand.extend(cells.get((int(cx) + dx, int(cy) + dy), ()))
        cand_arr = np.asarray(cand, dtype=np.int64)
        d2 = ((xy[cand_arr] - xy[i]) ** 2).sum(axis=1)
        for j in cand_arr[(d2 <= r2) & (cand_arr > i)]:
            ri, rj = find(i), find(int(j))
            if ri != rj:
                parent[rj] = ri

    roots = np.fromiter((find(i) for i in range(n)), dtype=np.int64, count=n)
    _, labels = np.unique(roots, return_inverse=True)
    return labels


def flag_clusters(gdf: gpd.GeoDataFrame, radius_km: float, min_count: int,
                  min_extent_km: float) -> None:
    """Adds cluster_id, cluster_size, cluster_extent_km, weather_cluster (per scene)."""
    gdf["cluster_id"] = ""
    gdf["cluster_size"] = 1
    gdf["cluster_extent_km"] = 0.0
    gdf["weather_cluster"] = False

    for scene, sub in gdf.groupby("scene_time"):
        sub = sub[np.isfinite(sub["lat"]) & np.isfinite(sub["lon"])]
        if sub.empty:
            continue
        lat0, lon0 = sub["lat"].mean(), sub["lon"].mean()
        x = (sub["lon"].to_numpy() - lon0) * 111.320 * math.cos(math.radians(lat0))
        y = (sub["lat"].to_numpy() - lat0) * 110.574
        labels = connected_components(np.column_stack([x, y]), radius_km)

        tmp = pd.DataFrame({"lab": labels, "x": x, "y": y})
        g = tmp.groupby("lab")
        size = g["x"].transform("size").to_numpy()
        extent = np.hypot(g["x"].transform("max") - g["x"].transform("min"),
                          g["y"].transform("max") - g["y"].transform("min")).to_numpy()

        gdf.loc[sub.index, "cluster_id"] = [f"{scene}_C{l:04d}" for l in labels]
        gdf.loc[sub.index, "cluster_size"] = size
        gdf.loc[sub.index, "cluster_extent_km"] = np.round(extent, 2)
        gdf.loc[sub.index, "weather_cluster"] = (size >= min_count) & (extent >= min_extent_km)


# --------------------------------------------------------------------------- #
# I/O
# --------------------------------------------------------------------------- #
def load_detections(path: str | Path) -> gpd.GeoDataFrame:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Input not found: {path}")
    if path.suffix.lower() in (".geojson", ".json"):
        gdf = gpd.read_file(path)
        if gdf.crs is None:
            gdf = gdf.set_crs(4326)
        elif gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs(4326)
    elif path.suffix.lower() == ".csv":
        df = pd.read_csv(path, dtype={"scene_time": str})
        if not {"lat", "lon"}.issubset(df.columns):
            raise ValueError("CSV needs 'lat' and 'lon' columns")
        gdf = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df["lon"], df["lat"]), crs=4326)
        log.warning("CSV input has no polygons - output geometries will be centroid points. "
                    "Use the Stage-3 GeoJSON to keep polygon shapes.")
    else:
        raise ValueError(f"Unsupported input type: {path.suffix}")

    if "scene_time" not in gdf.columns:
        raise ValueError("Input has no 'scene_time' column")
    gdf = gdf.reset_index(drop=True)
    gdf["scene_time"] = gdf["scene_time"].astype(str)

    if "lat" not in gdf.columns or "lon" not in gdf.columns or gdf[["lat", "lon"]].isna().any().any():
        pts = gdf.geometry.representative_point()
        gdf["lon"] = gdf["lon"].fillna(pts.x) if "lon" in gdf else pts.x
        gdf["lat"] = gdf["lat"].fillna(pts.y) if "lat" in gdf else pts.y
    gdf["lat"] = pd.to_numeric(gdf["lat"], errors="coerce")
    gdf["lon"] = pd.to_numeric(gdf["lon"], errors="coerce")

    if "detection_id" not in gdf.columns:
        gdf.insert(0, "detection_id", range(len(gdf)))
    return gdf


def write_outputs(gdf: gpd.GeoDataFrame, out_geojson: Path, out_csv: Path) -> None:
    out_geojson.parent.mkdir(parents=True, exist_ok=True)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out_geojson.write_text(gdf.to_json(na="null"), encoding="utf-8")
    pd.DataFrame(gdf.drop(columns="geometry")).to_csv(out_csv, index=False)


# --------------------------------------------------------------------------- #
# Main logic
# --------------------------------------------------------------------------- #
def run_filter(
    input_path: str | Path = "detections_median_fill.geojson",
    out_geojson: str | Path = "final_slicks.geojson",
    out_csv: str | Path = "final_slicks.csv",
    audit_csv: str | Path = "filter_audit.csv",
    *,
    min_wind: float = 1.0,
    max_wind: Optional[float] = None,
    cluster_radius_km: float = 10.0,
    cluster_min_count: int = 15,
    cluster_min_extent_km: float = 5.0,
    min_mean_prob: float = 0.5,
    min_area_km2: float = 0.0,
    flag_only: bool = False,
    drop_unknown_wind: bool = False,
    grid_res: float = 0.1,
    cache_path: Optional[str] = "wind_cache.json",
    batch_size: int = 50,
    request_delay: float = 0.6,
) -> gpd.GeoDataFrame:
    gdf = load_detections(input_path)
    n_in = len(gdf)
    log.info("Loaded %d detections from %s", n_in, input_path)

    out_geojson, out_csv, audit_csv = Path(out_geojson), Path(out_csv), Path(audit_csv)
    if n_in == 0:
        write_outputs(gdf, out_geojson, out_csv)
        pd.DataFrame(gdf.drop(columns="geometry")).to_csv(audit_csv, index=False)
        log.warning("No detections - wrote empty outputs")
        return gdf

    # ---- 1. spatial cluster filter (uses all detections, before any dropping) ----
    flag_clusters(gdf, cluster_radius_km, cluster_min_count, cluster_min_extent_km)

    # ---- 2. wind lookup ------------------------------------------------------------
    client = OpenMeteoWind(cache_path=cache_path, batch_size=batch_size, delay=request_delay)
    gdf["wind_speed_ms"] = np.nan
    gdf["wind_source"] = ""
    gdf["scene_time_utc"] = ""

    def snap(v: float) -> float:
        return round(round(v / grid_res) * grid_res, 4)

    for scene, sub in gdf.groupby("scene_time"):
        when = parse_scene_time(scene)
        if when is None:
            log.warning("Cannot parse scene_time '%s' - wind unavailable for %d detections", scene, len(sub))
            continue
        gdf.loc[sub.index, "scene_time_utc"] = when.isoformat()
        sub = sub[np.isfinite(sub["lat"]) & np.isfinite(sub["lon"])]
        keys = {i: (snap(la), snap(lo)) for i, la, lo in zip(sub.index, sub["lat"], sub["lon"])}
        unique = sorted(set(keys.values()))
        log.info("Scene %s: %d detections -> %d unique wind grid points", scene, len(sub), len(unique))
        winds = client.get_scene_winds(when, unique)
        for i, k in keys.items():
            if k in winds and winds[k][0] is not None:
                gdf.loc[i, "wind_speed_ms"] = round(float(winds[k][0]), 2)
                gdf.loc[i, "wind_source"] = winds[k][1]

    gdf["wind_verified"] = gdf["wind_speed_ms"].notna()
    if (~gdf["wind_verified"]).any():
        log.warning("%d detections have no wind data", int((~gdf["wind_verified"]).sum()))

    # ---- 3. apply rules ----------------------------------------------------------
    w = gdf["wind_speed_ms"]
    checks: dict[str, pd.Series] = {
        "calm_wind": w < min_wind,
        "weather_cluster": gdf["weather_cluster"],
    }
    if max_wind is not None:
        checks["high_wind"] = w > max_wind
    if drop_unknown_wind:
        checks["wind_unavailable"] = ~gdf["wind_verified"]
    if "mean_prob" in gdf.columns and min_mean_prob > 0:
        checks["low_confidence"] = pd.to_numeric(gdf["mean_prob"], errors="coerce") < min_mean_prob
    if "area_km2" in gdf.columns and min_area_km2 > 0:
        checks["too_small"] = pd.to_numeric(gdf["area_km2"], errors="coerce") < min_area_km2

    parts = pd.DataFrame({k: np.where(v.fillna(False), k, "") for k, v in checks.items()},
                         index=gdf.index)
    gdf["reject_reason"] = parts.apply(lambda r: ";".join(x for x in r if x), axis=1)
    gdf["is_false_positive"] = gdf["reject_reason"] != ""

    for name, mask in checks.items():
        log.info("  flagged by %-16s %d", name, int(mask.fillna(False).sum()))

    # ---- 4. outputs ----------------------------------------------------------------
    audit_csv.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(gdf.drop(columns="geometry")).to_csv(audit_csv, index=False)

    final = gdf if flag_only else gdf[~gdf["is_false_positive"]].copy()
    sort_cols = ["scene_time"] + (["mean_prob"] if "mean_prob" in final.columns else [])
    final = final.sort_values(sort_cols, ascending=[True] + [False] * (len(sort_cols) - 1))
    final = final.reset_index(drop=True)
    final.insert(0, "slick_id", [f"S{i + 1:05d}" for i in range(len(final))])
    write_outputs(final, out_geojson, out_csv)

    log.info("Kept %d / %d detections (%d rejected). Wrote %s, %s, %s",
             int((~gdf["is_false_positive"]).sum()), n_in, int(gdf["is_false_positive"].sum()),
             out_geojson, out_csv, audit_csv)
    return final


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Stage 4: wind + cluster false-positive filter.")
    p.add_argument("--input", default="detections_median_fill.geojson",
                   help="Stage-3 GeoJSON (keeps polygons) or CSV")
    p.add_argument("--out-geojson", default="final_slicks.geojson")
    p.add_argument("--out-csv", default="final_slicks.csv")
    p.add_argument("--audit-csv", default="filter_audit.csv",
                   help="Every detection with flags/reasons (no geometry)")
    p.add_argument("--min-wind", type=float, default=1.0, help="Reject if wind < this (m/s)")
    p.add_argument("--max-wind", type=float, default=None,
                   help="Optional: reject if wind > this (m/s); off by default")
    p.add_argument("--cluster-radius-km", type=float, default=10.0,
                   help="Detections closer than this are linked into one cluster")
    p.add_argument("--cluster-min-count", type=int, default=15,
                   help="Clusters with at least this many blobs...")
    p.add_argument("--cluster-min-extent-km", type=float, default=5.0,
                   help="...and spanning at least this many km are flagged as weather")
    p.add_argument("--min-mean-prob", type=float, default=0.5, help="0 disables")
    p.add_argument("--min-area-km2", type=float, default=0.0, help="0 disables")
    p.add_argument("--flag-only", action="store_true",
                   help="Keep everything in final outputs; just add flag columns")
    p.add_argument("--drop-unknown-wind", action="store_true",
                   help="Reject detections whose wind could not be fetched (default: keep, wind_verified=false)")
    p.add_argument("--grid-res", type=float, default=0.1, help="Degrees; coordinates snapped to this grid for wind lookups")
    p.add_argument("--cache", default="wind_cache.json", help="Wind cache file ('' to disable)")
    p.add_argument("--batch-size", type=int, default=50, help="Locations per API request")
    p.add_argument("--request-delay", type=float, default=0.6, help="Seconds between API requests")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    a = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    try:
        run_filter(
            a.input, a.out_geojson, a.out_csv, a.audit_csv,
            min_wind=a.min_wind, max_wind=a.max_wind,
            cluster_radius_km=a.cluster_radius_km, cluster_min_count=a.cluster_min_count,
            cluster_min_extent_km=a.cluster_min_extent_km,
            min_mean_prob=a.min_mean_prob, min_area_km2=a.min_area_km2,
            flag_only=a.flag_only, drop_unknown_wind=a.drop_unknown_wind,
            grid_res=a.grid_res, cache_path=a.cache or None,
            batch_size=a.batch_size, request_delay=a.request_delay)
    except (FileNotFoundError, ValueError) as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())