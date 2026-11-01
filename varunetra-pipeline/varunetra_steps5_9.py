#!/usr/bin/env python3
"""
VARUNETRA pipeline, steps 5-9.

Given a detected slick (GeoJSON polygon) and its observation time, this script:
  6. builds an ocean forcing file (currents + windage + diffusivity) for Parcels
  7. back-tracks particles seeded inside the slick
  8. matches the backtracked cloud against AIS tracks (hourly interpolation)
  9. estimates slick age, ranks candidate vessels, and persists results to Neon

Pinned stack (Parcels v2 API; v3 changed FieldSet/Field/kernel interfaces):
    parcels==2.4.2, numpy, pandas, xarray, netCDF4, geopandas, shapely>=2.0,
    sqlalchemy>=2.0, psycopg2-binary, matplotlib

Secrets: the database URL is read from NEON_DATABASE_URL only. Never hard-code it.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")  # headless rendering on servers
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import shapely  # noqa: E402
import xarray as xr  # noqa: E402
from parcels import (  # noqa: E402
    AdvectionRK4,
    BrownianMotion2D,
    ErrorCode,
    FieldSet,
    JITParticle,
    ParticleSet,
    Variable,
)
from sqlalchemy import create_engine, text  # noqa: E402

EARTH_RADIUS_KM = 6371.0088
M_PER_DEG_LAT = 111_320.0
KNOTS_PER_KMH = 1.0 / 1.852
SOURCE_TITLE = "AIS_Backtrack_Model"
SPILL_ID_PATTERN = re.compile(r"[A-Za-z0-9_\-]{1,64}")
AIS_COLUMN_MAP = {
    "MMSI": "mmsi",
    "BaseDateTime": "ts",
    "LAT": "lat",
    "LON": "lon",
    "SOG": "sog",
    "VesselName": "vessel_name",
    "IMO": "imo",
}

log = logging.getLogger("varunetra")


class PipelineError(RuntimeError):
    """Data or configuration problem that should stop the run."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class OceanConfig:
    currents_u: str = "uo"
    currents_v: str = "vo"
    wind_u: str = "u10"
    wind_v: str = "v10"
    windage_fraction: float = 0.03  # ~3% of 10 m wind speed is a common oil value
    diffusivity_m2s: float = 10.0   # horizontal eddy diffusivity; tune per region


@dataclass(frozen=True)
class BacktrackConfig:
    n_particles: int = 5000
    max_age_hours: int = 72
    dt_minutes: int = 10
    min_survivors: int = 100
    seed: int = 42


@dataclass(frozen=True)
class MatchConfig:
    overlap_radius_km: float = 2.0       # particle "touches" vessel within this radius
    min_overlap_fraction: float = 0.02   # below this, the vessel is not a candidate
    overlap_saturation: float = 0.25     # overlap at which the overlap score hits 1.0
    distance_sigma_km: float = 3.0
    timing_window_hours: int = 3         # AIS coverage window around the encounter
    ais_max_gap_hours: float = 2.0       # never interpolate across longer gaps
    underway_knots: float = 1.0
    max_speed_knots: float = 40.0        # implied-speed plausibility limit
    # weights: distance, overlap, timing coverage, movement profile
    weights: tuple[float, float, float, float] = (0.35, 0.30, 0.20, 0.15)
    top_n_store: int = 5


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------
@dataclass
class Slick:
    spill_id: str
    polygon: shapely.Geometry
    t_obs: datetime  # naive UTC


@dataclass
class Snapshot:
    hours_before_obs: int
    lon: np.ndarray
    lat: np.ndarray
    pid: np.ndarray


@dataclass
class BacktrackResult:
    slick: Slick
    snapshots: list[Snapshot]


@dataclass
class RankingResult:
    age_hours: float | None
    best_step: int | None
    origin_lat: float | None
    origin_lon: float | None
    origin_radius_km: float | None
    candidates: pd.DataFrame


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------
def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = (np.radians(np.asarray(x, dtype=float))
                              for x in (lat1, lon1, lat2, lon2))
    a = (np.sin((lat2 - lat1) / 2) ** 2
         + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def cloud_stats(lon: np.ndarray, lat: np.ndarray) -> tuple[float, float, float]:
    """Centroid and RMS radius (km). Simple mean is valid away from the dateline."""
    c_lat, c_lon = float(lat.mean()), float(lon.mean())
    rms = float(np.sqrt(np.mean(haversine_km(lat, lon, c_lat, c_lon) ** 2)))
    return c_lat, c_lon, rms


# ---------------------------------------------------------------------------
# Step 5 inputs: slick and ocean forcing
# ---------------------------------------------------------------------------
def load_slick(geojson: Path, spill_id: str, t_obs: datetime) -> Slick:
    if not SPILL_ID_PATTERN.fullmatch(spill_id):
        raise PipelineError(f"Unsafe spill_id: {spill_id!r}")
    if t_obs.tzinfo is None:
        raise PipelineError("t_obs must be timezone-aware (e.g. 2026-10-29T06:30:00+00:00)")
    t_naive = t_obs.astimezone(timezone.utc).replace(tzinfo=None)

    gdf = gpd.read_file(geojson)
    if gdf.crs is None:
        raise PipelineError("GeoJSON has no CRS; expected WGS84 (EPSG:4326)")
    gdf = gdf.to_crs(4326)
    geom = shapely.make_valid(shapely.union_all(gdf.geometry.values))
    if geom.is_empty or geom.geom_type not in ("Polygon", "MultiPolygon"):
        raise PipelineError(f"Slick geometry is unusable: {geom.geom_type}")
    return Slick(spill_id=spill_id, polygon=geom, t_obs=t_naive)


def _standardize(ds: xr.Dataset) -> xr.Dataset:
    rename = {old: new for old, new in
              {"latitude": "lat", "longitude": "lon", "valid_time": "time"}.items()
              if old in ds.variables}
    return ds.rename(rename).sortby(["time", "lat", "lon"])


def build_forcing_file(cfg: OceanConfig, currents_nc: Path, wind_nc: Path,
                       out_path: Path) -> Path:
    """
    Combine currents, windage velocity, and diffusivity into one NetCDF on the
    current grid. Parcels kernels are C-compiled and cannot call numpy/cos, so
    the lat-dependent metre-to-degree conversion is precomputed here.
    """
    with xr.open_dataset(currents_nc) as raw_cur, xr.open_dataset(wind_nc) as raw_wind:
        cur = _standardize(raw_cur)
        wind = _standardize(raw_wind)

        if "depth" in cur.dims:
            cur = cur.isel(depth=0, drop=True)  # surface layer

        for name in (cfg.currents_u, cfg.currents_v):
            if name not in cur:
                raise PipelineError(f"Currents file lacks variable '{name}'")
        for name in (cfg.wind_u, cfg.wind_v):
            if name not in wind:
                raise PipelineError(f"Wind file lacks variable '{name}'")

        uo = cur[cfg.currents_u].transpose("time", "lat", "lon")
        vo = cur[cfg.currents_v].transpose("time", "lat", "lon")

        # Put wind on the current grid; time-interpolate hourly wind to current steps.
        w = wind[[cfg.wind_u, cfg.wind_v]].interp(
            lat=uo["lat"], lon=uo["lon"], time=uo["time"], method="linear")
        if bool(w[cfg.wind_u].isnull().any()):
            raise PipelineError("Wind does not cover the current grid or time range")

        coslat = np.cos(np.deg2rad(uo["lat"])).clip(min=0.05)  # guard the poles
        alpha = cfg.windage_fraction
        uw = alpha * w[cfg.wind_u] / (M_PER_DEG_LAT * coslat)  # deg/s
        vw = alpha * w[cfg.wind_v] / M_PER_DEG_LAT             # deg/s

        kh = xr.full_like(uo, cfg.diffusivity_m2s, dtype=float)

        # NaN over land becomes zero velocity. Particles can still reach coasts,
        # so consider a proper land mask for production runs.
        ds = xr.Dataset({
            "uo": uo.fillna(0.0),
            "vo": vo.fillna(0.0),
            "Uw": uw.transpose("time", "lat", "lon").fillna(0.0),
            "Vw": vw.transpose("time", "lat", "lon").fillna(0.0),
            "Kh_zonal": kh,
            "Kh_meridional": kh.copy(),
        })
        ds.to_netcdf(out_path)
    log.info("Forcing file written: %s", out_path)
    return out_path


def forcing_time_bounds(path: Path) -> tuple[datetime, datetime]:
    with xr.open_dataset(path) as ds:
        times = pd.to_datetime(ds["time"].values)
    return times[0].to_pydatetime(), times[-1].to_pydatetime()


def load_fieldset(forcing_nc: Path) -> FieldSet:
    variables = {"U": "uo", "V": "vo", "Uw": "Uw", "Vw": "Vw",
                 "Kh_zonal": "Kh_zonal", "Kh_meridional": "Kh_meridional"}
    dimensions = {"lon": "lon", "lat": "lat", "time": "time"}
    return FieldSet.from_netcdf(str(forcing_nc), variables, dimensions,
                                allow_time_extrapolation=False)


# ---------------------------------------------------------------------------
# Step 7: backtracking (Parcels)
# ---------------------------------------------------------------------------
class SpillParticle(JITParticle):
    pid = Variable("pid", dtype=np.int32, to_write=False)


def windage_drift(particle, fieldset, time):
    """Wind drift as a fraction of wind speed, precomputed in deg/s on the grid."""
    particle.lon += fieldset.Uw[time, particle.depth, particle.lat, particle.lon] * particle.dt
    particle.lat += fieldset.Vw[time, particle.depth, particle.lat, particle.lon] * particle.dt


def delete_particle(particle, fieldset, time):
    particle.delete()


def seed_particles(polygon: shapely.Geometry, n: int,
                   rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """Rejection-sample n points uniformly inside the polygon (holes respected)."""
    minx, miny, maxx, maxy = polygon.bounds
    lons, lats, got = [], [], 0
    for _ in range(50):
        m = 4 * (n - got)
        x = rng.uniform(minx, maxx, m)
        y = rng.uniform(miny, maxy, m)
        keep = shapely.contains_xy(polygon, x, y)
        lons.append(x[keep])
        lats.append(y[keep])
        got += int(keep.sum())
        if got >= n:
            break
    else:
        raise PipelineError("Could not seed particles: polygon too small or invalid")
    return np.concatenate(lons)[:n], np.concatenate(lats)[:n]


def _snapshot(pset: ParticleSet, hours_before: int) -> Snapshot:
    if len(pset) == 0:
        empty = np.empty(0)
        return Snapshot(hours_before, empty, empty, np.empty(0, dtype=np.int32))
    rows = np.array([(p.lon, p.lat, p.pid) for p in pset], dtype=np.float64)
    return Snapshot(hours_before, rows[:, 0], rows[:, 1], rows[:, 2].astype(np.int32))


def run_backtrack(slick: Slick, forcing_nc: Path, ocean_t0: datetime,
                  cfg: BacktrackConfig) -> BacktrackResult:
    t_rel_obs = (slick.t_obs - ocean_t0).total_seconds()
    if t_rel_obs - cfg.max_age_hours * 3600 < 0:
        raise PipelineError("Ocean forcing starts after the required backtrack window")

    # Parcels' BrownianMotion2D draws from numpy's global RNG, so seed it too.
    np.random.seed(cfg.seed)
    rng = np.random.default_rng(cfg.seed)
    lons, lats = seed_particles(slick.polygon, cfg.n_particles, rng)

    fieldset = load_fieldset(forcing_nc)
    pset = ParticleSet(fieldset=fieldset, pclass=SpillParticle,
                       lon=lons, lat=lats, time=t_rel_obs,
                       pid=np.arange(len(lons), dtype=np.int32))

    kernels = (pset.Kernel(AdvectionRK4)
               + pset.Kernel(windage_drift)
               + pset.Kernel(BrownianMotion2D))
    recovery = {ErrorCode.ErrorOutOfBounds: delete_particle}
    dt_back = -timedelta(minutes=cfg.dt_minutes)  # negative dt = backward in time

    # Run in 1-hour chunks so each hourly cloud snapshot is captured exactly.
    snapshots = [_snapshot(pset, 0)]
    for h in range(1, cfg.max_age_hours + 1):
        pset.execute(kernels, runtime=timedelta(hours=1), dt=dt_back,
                     recovery=recovery, output_file=None)
        snap = _snapshot(pset, h)
        snapshots.append(snap)
        if len(snap.lon) < cfg.min_survivors:
            log.warning("Only %d particles survive at %d h; stopping backtrack",
                        len(snap.lon), h)
            break
    log.info("Backtrack complete: %d steps, %d particles at step 0 -> %d at last step",
             len(snapshots) - 1, len(snapshots[0].lon), len(snapshots[-1].lon))
    return BacktrackResult(slick=slick, snapshots=snapshots)


# ---------------------------------------------------------------------------
# Step 8: AIS loading and interpolation
# ---------------------------------------------------------------------------
def load_ais_window(path: Path, bbox: tuple[float, float, float, float],
                    t_start: pd.Timestamp, t_end: pd.Timestamp,
                    chunksize: int = 1_000_000) -> pd.DataFrame:
    """Stream a NOAA-style AIS CSV, keeping only the time window and bounding box."""
    lon_min, lat_min, lon_max, lat_max = bbox
    parts = []
    reader = pd.read_csv(path, usecols=lambda c: c in AIS_COLUMN_MAP,
                         chunksize=chunksize, low_memory=False)
    for chunk in reader:
        chunk = chunk.rename(columns=AIS_COLUMN_MAP)
        chunk["ts"] = (pd.to_datetime(chunk["ts"], utc=True, errors="coerce")
                       .dt.tz_localize(None))  # AIS BaseDateTime is UTC
        chunk = chunk.dropna(subset=["mmsi", "ts", "lat", "lon", "sog"])
        mask = (chunk["ts"].between(t_start, t_end)
                & chunk["lat"].between(lat_min, lat_max)
                & chunk["lon"].between(lon_min, lon_max)
                & chunk["sog"].between(0, 50))  # discard impossible speeds
        parts.append(chunk.loc[mask])
    columns = ["mmsi", "ts", "lat", "lon", "sog", "vessel_name", "imo"]
    if not parts:
        return pd.DataFrame(columns=columns)
    df = pd.concat(parts, ignore_index=True).reindex(columns=columns)
    df["mmsi"] = df["mmsi"].astype("int64")
    log.info("AIS window: %d position reports, %d vessels",
             len(df), df["mmsi"].nunique())
    return df


def _interp_track(t, lat, lon, sog, tq, max_gap_s):
    """Linear interpolation onto query epochs; refuses to bridge long gaps."""
    idx = np.clip(np.searchsorted(t, tq, side="right") - 1, 0, len(t) - 2)
    t0, t1 = t[idx], t[idx + 1]
    gap = t1 - t0
    inside = (tq >= t[0]) & (tq <= t[-1])
    ok = inside & (gap > 0) & (gap <= max_gap_s)
    frac = np.clip((tq - t0) / np.where(gap > 0, gap, 1.0), 0.0, 1.0)
    lat_q = lat[idx] + frac * (lat[idx + 1] - lat[idx])
    lon_q = lon[idx] + frac * (lon[idx + 1] - lon[idx])
    sog_q = 0.5 * (sog[idx] + sog[idx + 1])
    return lat_q, lon_q, sog_q, ok


# ---------------------------------------------------------------------------
# Steps 8-9: age estimation and vessel ranking
# ---------------------------------------------------------------------------
def rank_vessels(bt: BacktrackResult, ais_csv: Path, cfg: MatchConfig) -> RankingResult:
    n_steps = len(bt.snapshots)
    K = n_steps - 1
    t_obs = bt.slick.t_obs
    epoch_obs = pd.Timestamp(t_obs).value / 1e9
    query_epoch = epoch_obs - np.arange(n_steps) * 3600.0

    cen_lat = np.full(n_steps, np.nan)
    cen_lon = np.full(n_steps, np.nan)
    for k, s in enumerate(bt.snapshots):
        if len(s.lon):
            cen_lat[k], cen_lon[k], _ = cloud_stats(s.lon, s.lat)
    if np.all(np.isnan(cen_lat)):
        raise PipelineError("Particle cloud is empty at every step")

    all_lon = np.concatenate([s.lon for s in bt.snapshots if s.lon.size])
    all_lat = np.concatenate([s.lat for s in bt.snapshots if s.lat.size])
    buf = 0.5
    bbox = (all_lon.min() - buf, all_lat.min() - buf,
            all_lon.max() + buf, all_lat.max() + buf)
    pad = pd.Timedelta(hours=cfg.ais_max_gap_hours + 1)
    ais = load_ais_window(ais_csv, bbox,
                          pd.Timestamp(t_obs) - pd.Timedelta(hours=K) - pad,
                          pd.Timestamp(t_obs) + pad)

    max_gap_s = cfg.ais_max_gap_hours * 3600
    rows = []
    for mmsi, g in ais.groupby("mmsi", sort=False):
        g = g.sort_values("ts").drop_duplicates("ts")
        if len(g) < 2:
            continue
        t = g["ts"].to_numpy().astype("datetime64[s]").astype("int64").astype(float)
        lat_s = g["lat"].to_numpy(float)
        lon_s = g["lon"].to_numpy(float)
        sog_s = g["sog"].to_numpy(float)

        lat_q, lon_q, sog_q, ok = _interp_track(t, lat_s, lon_s, sog_s,
                                                query_epoch, max_gap_s)
        if not ok.any():
            continue

        frac = np.full(n_steps, np.nan)
        dist = np.full(n_steps, np.nan)
        for k in np.flatnonzero(ok):
            if np.isnan(cen_lat[k]):
                continue
            s = bt.snapshots[k]
            d_pv = haversine_km(s.lat, s.lon, lat_q[k], lon_q[k])
            frac[k] = np.mean(d_pv <= cfg.overlap_radius_km)  # overlap fraction
            dist[k] = haversine_km(lat_q[k], lon_q[k], cen_lat[k], cen_lon[k])

        if np.all(np.isnan(frac)) or np.nanmax(frac) < cfg.min_overlap_fraction:
            continue

        # Age estimation: the step of peak overlap (ties resolve to most recent).
        k_star = int(np.nanargmax(frac))
        d_min = float(np.nanmin(dist))
        f_max = float(frac[k_star])

        w = cfg.timing_window_hours
        win = np.arange(max(0, k_star - w), min(K, k_star + w) + 1)
        timing_coverage = float(ok[win].mean())

        near = np.arange(max(0, k_star - 2), min(K, k_star + 2) + 1)
        near = near[ok[near]]
        underway = float(np.mean(sog_q[near] > cfg.underway_knots)) if near.size else 0.0
        seg_km = haversine_km(lat_s[:-1], lon_s[:-1], lat_s[1:], lon_s[1:])
        seg_h = np.diff(t) / 3600.0
        valid = seg_h > 0
        speed_kn = seg_km[valid] / seg_h[valid] * KNOTS_PER_KMH
        plausible = float(np.mean(speed_kn <= cfg.max_speed_knots)) if speed_kn.size else 0.0

        name = g["vessel_name"].dropna()
        imo = g["imo"].dropna()
        rows.append({
            "mmsi": int(mmsi),
            "vessel_name": str(name.iloc[-1]) if len(name) else None,
            "imo": str(imo.iloc[-1]) if len(imo) else None,
            "encounter_step": k_star,
            "encounter_time_utc": (t_obs - timedelta(hours=k_star)).isoformat(),
            "min_distance_km": d_min,
            "max_overlap_frac": f_max,
            "timing_coverage": timing_coverage,
            "movement_score": underway * plausible,
            "s_dist": math.exp(-d_min / cfg.distance_sigma_km),
            "s_ovl": min(1.0, f_max / cfg.overlap_saturation),
        })

    cand = pd.DataFrame(rows)
    if cand.empty:
        log.warning("No vessel reached the minimum overlap threshold")
        return RankingResult(None, None, None, None, None, cand)

    w = np.asarray(cfg.weights, dtype=float)
    w = w / w.sum()
    comps = cand[["s_dist", "s_ovl", "timing_coverage", "movement_score"]].to_numpy()
    cand["raw_score"] = comps @ w
    total = max(float(cand["raw_score"].sum()), 1e-12)
    cand["match_probability_pct"] = 100.0 * cand["raw_score"] / total
    cand = cand.sort_values("raw_score", ascending=False).reset_index(drop=True)

    best_step = int(cand.iloc[0]["encounter_step"])
    o_lat, o_lon, o_rad = cloud_stats(bt.snapshots[best_step].lon,
                                      bt.snapshots[best_step].lat)
    return RankingResult(age_hours=float(best_step), best_step=best_step,
                         origin_lat=o_lat, origin_lon=o_lon,
                         origin_radius_km=o_rad, candidates=cand)


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------
def save_heatmap(bt: BacktrackResult, step: int, out_dir: Path) -> Path:
    s = bt.snapshots[step]
    fig, ax = plt.subplots(figsize=(7, 6), dpi=150)
    h = ax.hist2d(s.lon, s.lat, bins=80, cmap="magma")
    fig.colorbar(h[3], ax=ax, label="particles per cell")
    gpd.GeoSeries([bt.slick.polygon], crs=4326).boundary.plot(
        ax=ax, color="cyan", linewidth=1)
    ax.set_title(f"{bt.slick.spill_id}: particle cloud {step} h before T_obs")
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    path = out_dir / f"{bt.slick.spill_id}_origin_heatmap_{step}h.png"
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def save_to_neon_db(spill_id: str, ranking: RankingResult,
                    cfg: MatchConfig, database_url: str | None = None) -> None:
    """Atomic write: spills update, previous model culprits replaced, top-N inserted."""
    url = database_url or os.environ.get("NEON_DATABASE_URL")
    if not url:
        raise PipelineError("NEON_DATABASE_URL is not set")

    engine = create_engine(url, pool_pre_ping=True, pool_size=1, max_overflow=0)
    top = ranking.candidates.head(cfg.top_n_store)
    try:
        with engine.begin() as conn:
            found = conn.execute(
                text("SELECT 1 FROM spills WHERE id = :sid FOR UPDATE"),
                {"sid": spill_id}).first()
            if found is None:
                raise PipelineError(f"No spills row for {spill_id}; refusing to proceed")

            conn.execute(text("""
                UPDATE spills
                   SET age_hours = :age,
                       origin_lat = :olat,
                       origin_lon = :olon,
                       origin_radius_km = :orad
                 WHERE id = :sid
            """), {"age": ranking.age_hours, "olat": ranking.origin_lat,
                   "olon": ranking.origin_lon, "orad": ranking.origin_radius_km,
                   "sid": spill_id})

            conn.execute(text("DELETE FROM reported_culprits WHERE spill_id = :sid"),
                         {"sid": spill_id})

            if not top.empty:
                rows = []
                for r in top.itertuples(index=False):
                    notes = json.dumps({
                        "match_probability_pct": round(float(r.match_probability_pct), 2),
                        "min_distance_km": round(float(r.min_distance_km), 3),
                        "max_overlap_fraction": round(float(r.max_overlap_frac), 4),
                        "timing_coverage": round(float(r.timing_coverage), 3),
                        "movement_score": round(float(r.movement_score), 3),
                        "encounter_time_utc": r.encounter_time_utc,
                        "model": SOURCE_TITLE,
                    })
                    rows.append({"sid": spill_id, "mmsi": int(r.mmsi),
                                 "vname": r.vessel_name, "imo": r.imo,
                                 "src": SOURCE_TITLE, "notes": notes})
                conn.execute(text("""
                    INSERT INTO reported_culprits
                        (spill_id, mmsi, vessel_name, imo, source_title, notes)
                    VALUES (:sid, :mmsi, :vname, :imo, :src, :notes)
                """), rows)
        log.info("Saved %d culprit rows for %s", len(top), spill_id)
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def _observation_time_from_geojson(path: Path) -> datetime:
    """Read the scene time used by the Steps 1-4 GeoJSON output."""
    try:
        with path.open(encoding="utf-8") as source:
            data = json.load(source)
    except (OSError, json.JSONDecodeError) as exc:
        raise PipelineError(f"Could not read GeoJSON observation time: {exc}") from exc

    features = data.get("features", []) if isinstance(data, dict) else []
    properties = features[0].get("properties", {}) if features else {}
    value = properties.get("scene_time_utc") or properties.get("scene_time")
    if not value:
        raise PipelineError(
            "GeoJSON has no scene_time_utc/scene_time; provide --t_obs explicitly"
        )

    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(str(value), "%Y%m%dT%H%M%S")
        except ValueError as exc:
            raise PipelineError(f"Unrecognized GeoJSON scene time: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="VARUNETRA steps 5-9")
    p.add_argument("--geojson_path", "--geojson", dest="geojson_path",
                   type=Path, required=True, help="Slick polygon GeoJSON")
    p.add_argument("--spill_id", "--spill-id", dest="spill_id", required=True)
    p.add_argument(
        "--forcing_nc", type=Path, required=True,
        help="NetCDF with uo, vo, u10, and v10 variables",
    )
    p.add_argument(
        "--ais_data", "--ais-csv", dest="ais_data", type=Path, required=True,
        help="Historical AIS CSV with NOAA-style columns",
    )
    p.add_argument(
        "--t_obs", "--t-obs", dest="t_obs",
        help="Optional ISO 8601 observation time; defaults to GeoJSON scene time",
    )
    p.add_argument("--out_dir", "--out-dir", dest="out_dir",
                   type=Path, default=Path("outputs"))
    p.add_argument("--dry-run", action="store_true", help="skip database writes")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ocean_cfg, bt_cfg, match_cfg = OceanConfig(), BacktrackConfig(), MatchConfig()

    try:
        try:
            t_obs = (
                datetime.fromisoformat(args.t_obs.replace("Z", "+00:00"))
                if args.t_obs else _observation_time_from_geojson(args.geojson_path)
            )
        except ValueError as exc:
            raise PipelineError(f"Invalid --t_obs value: {args.t_obs!r}") from exc
        slick = load_slick(args.geojson_path, args.spill_id, t_obs)
        work = args.out_dir / slick.spill_id
        work.mkdir(parents=True, exist_ok=True)

        forcing = build_forcing_file(ocean_cfg, args.forcing_nc, args.forcing_nc,
                                     work / "forcing.nc")
        t0, t_end = forcing_time_bounds(forcing)
        if not (t0 <= slick.t_obs <= t_end):
            raise PipelineError(f"T_obs {slick.t_obs} outside forcing range {t0}..{t_end}")

        bt = run_backtrack(slick, forcing, t0, bt_cfg)
        ranking = rank_vessels(bt, args.ais_data, match_cfg)

        heatmap = None
        if ranking.best_step is not None:
            heatmap = save_heatmap(bt, ranking.best_step, work)
        ranking.candidates.to_csv(work / "candidates.csv", index=False)

        if ranking.age_hours is not None:
            log.info("Estimated age: %.1f h; origin (%.4f, %.4f), radius %.2f km",
                     ranking.age_hours, ranking.origin_lat,
                     ranking.origin_lon, ranking.origin_radius_km)
            log.info("Top candidate MMSI %s at %.1f%%",
                     ranking.candidates.iloc[0]["mmsi"],
                     ranking.candidates.iloc[0]["match_probability_pct"])
        else:
            log.info("No candidate vessel found; database age/origin will be cleared")

        if args.dry_run:
            log.info("Dry run: database not modified. Outputs in %s", work)
            return 0

        save_to_neon_db(slick.spill_id, ranking, match_cfg)
        return 0
    except PipelineError as exc:
        log.error("Pipeline stopped: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())