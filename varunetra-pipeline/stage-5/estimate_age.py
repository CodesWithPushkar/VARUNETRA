#!/usr/bin/env python3
"""
Stage 5 - Oil slick age estimation (Sentinel-1 SAR pipeline)
============================================================

Reads  ../stage-4/final_slicks.geojson   (wind-filtered slicks, EPSG:4326)
Writes ../stage-5/aged_slicks.geojson    (same features + age properties)

For every slick polygon a *bounded age window* is produced:

    min_age_hr <= est_age_hr <= max_age_hr

Pillar 1 - Meteorological clock  -> T_max
    Oil is mixed into the water column by wind > 11 m/s. Looking backwards
    from the satellite acquisition time T0 (up to 72 h) at ERA5 hourly 10 m
    wind at the slick centroid, the most recent hour exceeding the threshold
    is the hard upper bound on age. No storm -> T_max = 72 h (cap).

Pillar 2 - Morphology / diffusion / Fay spreading -> T_est and T_min
    * Geometry is measured in metres (local equal-area projection by default).
    * Linear slicks (L/W > 4): Okubo-style diffusion of the cross-width,
          t = W^2 / (16 * K_h)
    * Patch slicks (L/W <= 4): Fay (1969) surface-tension/viscous spreading
      regime, inverted for time from the equivalent radius. This regime is
      independent of spilled volume, which is unknown from SAR alone.
    * T_est uses the central parameter value; T_min uses the parameter value
      that gives the *fastest* spreading (youngest possible age).

Pillar 3 - Classification
    L/W > 4 -> "Vessel Wake", otherwise "Drifting Patch".

Properties appended to each feature
    min_age_hr, max_age_hr, est_age_hr, length_m, width_m, slick_type
    (extra, for QA): area_m2, elongation, max_age_basis, age_conflict

Dependencies: shapely>=2, pyproj, requests
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from pyproj import CRS, Transformer
from shapely.geometry import shape
from shapely.ops import transform
from shapely.validation import make_valid

# =============================================================================
# CONFIGURATION
# =============================================================================

# Satellite acquisition time (UTC). Replace with the value parsed from the
# Sentinel-1 product name / manifest when you wire this into the pipeline.
T0 = "2023-10-25T14:30:00Z"

# Paths are resolved relative to this script so it runs from any cwd.
SCRIPT_DIR = Path(__file__).resolve().parent
INPUT_PATH = SCRIPT_DIR / "../stage-4/final_slicks.geojson"
OUTPUT_PATH = SCRIPT_DIR / "../stage-5/aged_slicks.geojson"

# --- Pillar 1: meteorological clock -----------------------------------------
STORM_THRESHOLD_MS = 11.0     # wind speed that destroys a slick (m/s)
LOOKBACK_HOURS = 72           # how far back we search / cap on T_max
ERA5_GRID_DEG = 0.25          # ERA5 resolution; used to share cached requests

OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_LATENCY_DAYS = 7      # ERA5 archive lags real time by ~5 days
HTTP_TIMEOUT_S = 30
HTTP_RETRIES = 4
REQUEST_PAUSE_S = 0.2         # politeness delay between uncached calls

# --- Pillar 2: morphology ---------------------------------------------------
WAKE_ELONGATION = 4.0         # L/W above this -> linear vessel wake
MIN_WIDTH_M = 10.0            # Sentinel-1 IW GRD pixel size; avoids W -> 0

# Okubo diffusion coefficient K_h (m^2/s). Central value as specified;
# the range brackets weak -> strong turbulence and drives T_min.
KH_CENTRAL = 0.5
KH_FAST = 1.0                 # strong mixing  -> youngest plausible age
KH_SLOW = 0.1                 # weak mixing    -> oldest plausible age (info only)

# Fay (1969) surface-tension / viscous regime:  R = K3 * (s^2 t^3 / (rho^2 nu))^(1/4)
FAY_K3 = 2.30
SEAWATER_DENSITY = 1025.0     # kg/m^3
KINEMATIC_VISC = 1.0e-6       # m^2/s (seawater, ~20 C)
SPREAD_COEF_CENTRAL = 0.020   # net spreading coefficient (N/m)
SPREAD_COEF_FAST = 0.030      # faster spreading -> youngest plausible age

# Metric CRS used for geometry:
#   "local_laea" : Lambert azimuthal equal-area centred on each slick (accurate)
#   "EPSG:3857"  : Web Mercator (areas inflate by 1/cos^2(lat); not recommended)
METRIC_CRS = "local_laea"

log = logging.getLogger("stage5")


# =============================================================================
# PILLAR 2 helpers - geometry in metres
# =============================================================================

@dataclass
class Morphology:
    area_m2: float
    length_m: float
    width_m: float
    elongation: float


def project_to_metric(geom_ll):
    """Project a lon/lat geometry into a metric CRS."""
    if METRIC_CRS == "local_laea":
        c = geom_ll.centroid
        dst = CRS.from_proj4(
            f"+proj=laea +lat_0={c.y} +lon_0={c.x} +datum=WGS84 +units=m +no_defs"
        )
    else:
        dst = CRS.from_user_input(METRIC_CRS)
    tf = Transformer.from_crs("EPSG:4326", dst, always_xy=True)
    return transform(tf.transform, geom_ll)


def rotated_rect_dims(geom_m) -> tuple[float, float]:
    """Return (length, width) in metres of the minimum rotated bounding box."""
    rect = geom_m.minimum_rotated_rectangle
    if rect.geom_type != "Polygon":       # degenerate: line or point
        return float(rect.length), 0.0
    xy = list(rect.exterior.coords)
    e1 = math.dist(xy[0], xy[1])
    e2 = math.dist(xy[1], xy[2])
    return max(e1, e2), min(e1, e2)


def analyse_geometry(geom_ll) -> Morphology:
    """Compute area, L, W and elongation for a lon/lat geometry."""
    if not geom_ll.is_valid:
        geom_ll = make_valid(geom_ll)
    geom_m = project_to_metric(geom_ll)
    length, width = rotated_rect_dims(geom_m)
    width = max(width, MIN_WIDTH_M)
    length = max(length, width)
    return Morphology(geom_m.area, length, width, length / width)


def classify(elongation: float) -> str:
    return "Vessel Wake" if elongation > WAKE_ELONGATION else "Drifting Patch"


def wake_age_hr(width_m: float, kh: float) -> float:
    """Okubo diffusion: t = W^2 / (16 K_h), seconds -> hours."""
    return width_m ** 2 / (16.0 * kh) / 3600.0


def patch_age_hr(area_m2: float, spread_coef: float) -> float:
    """
    Invert Fay's surface-tension/viscous spreading law for time.

        R = K3 * (s^2 t^3 / (rho^2 nu))^(1/4)
        =>  t = (R^4 rho^2 nu / (K3^4 s^2))^(1/3)

    R is the radius of the circle with the same area as the slick.
    """
    radius = math.sqrt(area_m2 / math.pi)
    t_s = (radius ** 4 * SEAWATER_DENSITY ** 2 * KINEMATIC_VISC
           / (FAY_K3 ** 4 * spread_coef ** 2)) ** (1.0 / 3.0)
    return t_s / 3600.0


def morphological_ages(m: Morphology) -> tuple[float, float]:
    """Return (t_est_hr, t_min_hr) from geometry alone."""
    if m.elongation > WAKE_ELONGATION:
        return (wake_age_hr(m.width_m, KH_CENTRAL),
                wake_age_hr(m.width_m, KH_FAST))
    return (patch_age_hr(m.area_m2, SPREAD_COEF_CENTRAL),
            patch_age_hr(m.area_m2, SPREAD_COEF_FAST))


# =============================================================================
# PILLAR 1 - Open-Meteo ERA5 wind client
# =============================================================================

class WindClient:
    """Fetches hourly 10 m wind for the lookback window, with grid-cell caching."""

    def __init__(self, t0: datetime):
        self.t0 = t0
        self.session = requests.Session()
        self._cache: dict[tuple[float, float], list[tuple[datetime, float]]] = {}
        age_days = (datetime.now(timezone.utc) - t0).days
        self.use_archive = age_days >= ARCHIVE_LATENCY_DAYS
        if not self.use_archive:
            log.warning("T0 is < %d days old: ERA5 archive may not cover it; "
                        "using the forecast API's past data instead.",
                        ARCHIVE_LATENCY_DAYS)

    @staticmethod
    def _snap(v: float) -> float:
        return round(round(v / ERA5_GRID_DEG) * ERA5_GRID_DEG, 4)

    def get_series(self, lat: float, lon: float) -> list[tuple[datetime, float]]:
        key = (self._snap(lat), self._snap(lon))
        if key in self._cache:
            return self._cache[key]

        start = (self.t0 - timedelta(hours=LOOKBACK_HOURS)).date()
        params = {
            "latitude": key[0],
            "longitude": key[1],
            "start_date": start.isoformat(),
            "end_date": self.t0.date().isoformat(),
            "hourly": "wind_speed_10m",
            "wind_speed_unit": "ms",
            "timezone": "GMT",
        }
        url = OPEN_METEO_ARCHIVE if self.use_archive else OPEN_METEO_FORECAST
        if self.use_archive:
            params["models"] = "era5"

        payload = self._request(url, params)
        hourly = payload["hourly"]
        series = []
        for ts, ws in zip(hourly["time"], hourly["wind_speed_10m"]):
            if ws is None:                       # gaps are returned as null
                continue
            series.append((datetime.fromisoformat(ts).replace(tzinfo=timezone.utc),
                           float(ws)))
        self._cache[key] = series
        time.sleep(REQUEST_PAUSE_S)
        return series

    def _request(self, url: str, params: dict) -> dict:
        """GET with exponential backoff on rate-limit / transient errors."""
        last_err: Exception | None = None
        for attempt in range(HTTP_RETRIES):
            try:
                r = self.session.get(url, params=params, timeout=HTTP_TIMEOUT_S)
                if r.status_code == 429 or r.status_code >= 500:
                    raise requests.HTTPError(f"HTTP {r.status_code}")
                r.raise_for_status()
                return r.json()
            except (requests.RequestException, ValueError) as err:
                last_err = err
                wait = 2 ** attempt
                log.warning("Open-Meteo request failed (%s); retry in %ds", err, wait)
                time.sleep(wait)
        raise RuntimeError(f"Open-Meteo request failed after retries: {last_err}")


def storm_age_hr(series: list[tuple[datetime, float]], t0: datetime) -> float | None:
    """
    Step backwards hour by hour from T0 and return the age (hours before T0) of
    the most recent hour with wind > STORM_THRESHOLD_MS, or None if none found.
    """
    window_start = t0 - timedelta(hours=LOOKBACK_HOURS)
    for ts, ws in reversed(series):              # series is chronological
        if ts > t0 or ts < window_start:
            continue
        if ws > STORM_THRESHOLD_MS:
            return max((t0 - ts).total_seconds() / 3600.0, 0.0)
    return None


# =============================================================================
# Combine the pillars
# =============================================================================

def estimate_feature(feature: dict, winds: WindClient, t0: datetime) -> dict:
    geom = shape(feature["geometry"])
    morph = analyse_geometry(geom)
    t_est_m, t_min_m = morphological_ages(morph)

    # Pillar 1: weather upper bound
    c = geom.centroid
    basis = "no_storm_72h_cap"
    t_max = float(LOOKBACK_HOURS)
    try:
        age = storm_age_hr(winds.get_series(c.y, c.x), t0)
        if age is not None:
            t_max, basis = age, "storm"
    except Exception as err:                     # keep the batch going
        log.error("Wind lookup failed at (%.3f, %.3f): %s", c.y, c.x, err)
        basis = "weather_unavailable_72h_cap"

    # Reconcile morphology with the weather bound
    conflict = t_min_m > t_max                   # slick too "mature" for the storm-free window
    t_min = min(t_min_m, t_max)
    t_est = min(max(t_est_m, t_min), t_max)

    return {
        "min_age_hr": round(t_min, 2),
        "max_age_hr": round(t_max, 2),
        "est_age_hr": round(t_est, 2),
        "length_m": round(morph.length_m, 1),
        "width_m": round(morph.width_m, 1),
        "slick_type": classify(morph.elongation),
        # QA extras
        "area_m2": round(morph.area_m2, 1),
        "elongation": round(morph.elongation, 2),
        "max_age_basis": basis,
        "age_conflict": bool(conflict),
    }


# =============================================================================
# Main
# =============================================================================

def make_spill_id(props: dict) -> str:
    """Stable id shared by stage 5 (spill_id property) and db_ingest. Format: varunetra_<scene>_<slick>."""
    scene = re.sub(r"[^A-Za-z0-9]", "", str(props.get("scene_time", "")))
    slick = re.sub(r"[^A-Za-z0-9]", "", str(props.get("slick_id", "")))
    if not scene or not slick:
        raise ValueError("feature needs scene_time and slick_id to build spill_id")
    return f"varunetra_{scene}_{slick}"


def parse_t0(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def main() -> int:
    ap = argparse.ArgumentParser(description="Stage 5: oil slick age estimation")
    ap.add_argument("--input", type=Path, default=INPUT_PATH)
    ap.add_argument("--output", type=Path, default=OUTPUT_PATH)
    ap.add_argument("--t0", default=T0, help="Satellite acquisition time (UTC, ISO 8601)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    t0 = parse_t0(args.t0)

    with open(args.input, "r", encoding="utf-8") as f:
        fc = json.load(f)
    features = fc.get("features", [])
    log.info("Loaded %d slicks from %s (T0 = %s)", len(features), args.input, t0.isoformat())

    winds = WindClient(t0)
    out_features = []
    for i, feat in enumerate(features, 1):
        if not feat.get("geometry"):
            log.warning("Feature %d has no geometry; skipped", i)
            continue
        feat_props = feat.setdefault("properties", {})
        feat_props.setdefault("spill_id", make_spill_id(feat_props))
        props = estimate_feature(feat, winds, t0)
        feat_props.update(props)
        out_features.append(feat)
        log.info("[%d/%d] %-14s L=%8.0f m W=%7.0f m  age %.1f-%.1f h (est %.1f)%s",
                 i, len(features), props["slick_type"], props["length_m"],
                 props["width_m"], props["min_age_hr"], props["max_age_hr"],
                 props["est_age_hr"], "  [CONFLICT]" if props["age_conflict"] else "")

    fc["features"] = out_features
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(fc, f)
    log.info("Wrote %d aged slicks to %s", len(out_features), args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())