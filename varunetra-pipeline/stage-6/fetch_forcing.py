#!/usr/bin/env python3
"""
Stage 6 - historical forcing for backtracking.

    currents : Copernicus Marine (CMEMS), dataset named by CMEMS_DATASET_ID   -> uo, vo
    wind     : ERA5 10 m wind from the Copernicus Climate Data Store (CDS)     -> u10, v10

Both files are normalised to dims (time, latitude, longitude) with naive UTC times, which is
the layout stage 7 expects. Nothing here generates synthetic data. Any failure raises
ForcingError so the caller can stop that slick instead of producing a result from fake
currents.

Results are cached per (scene time, bounding box) under the cache directory.
"""
from __future__ import annotations

import json
import logging
import math
import re
import tempfile
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Tuple

import numpy as np
import pandas as pd
import xarray as xr

log = logging.getLogger("stage6.forcing")


class ForcingError(RuntimeError):
    """Forcing could not be fetched or does not cover the requested window."""


def forcing_window(t_acq: datetime, runtime_hours: int, pad_hours: float = 6.0) -> Tuple[datetime, datetime]:
    return t_acq - timedelta(hours=runtime_hours + pad_hours), t_acq + timedelta(hours=1)


def _rename_standard(ds: xr.Dataset) -> xr.Dataset:
    ren = {}
    if "valid_time" in ds.variables and "time" not in ds.variables:
        ren["valid_time"] = "time"
    if "lat" in ds.variables and "latitude" not in ds.variables:
        ren["lat"] = "latitude"
    if "lon" in ds.variables and "longitude" not in ds.variables:
        ren["lon"] = "longitude"
    ds = ds.rename(ren)
    ds = ds.drop_vars([v for v in ("expver", "number") if v in ds.variables], errors="ignore")
    if "depth" in ds.dims:
        ds = ds.isel(depth=0, drop=True)            # surface layer
    return ds


def _write(ds: xr.Dataset, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ds.to_netcdf(out_path)
    log.info("wrote %s", out_path)


def check_coverage(path: Path, t_start: datetime, t_end: datetime, label: str) -> None:
    with xr.open_dataset(path) as ds:
        t = ds["time"].values.astype("datetime64[ns]")
    lo, hi = t.min(), t.max()
    if lo > pd.Timestamp(t_start) or hi < pd.Timestamp(t_end):
        raise ForcingError(f"{label} covers {lo}..{hi}, need {t_start}..{t_end}")


# --------------------------------------------------------------------------- currents
def fetch_currents(raw_out: Path, bbox, t0: datetime, t1: datetime, dataset_id: str,
                   username: str, password: str) -> None:
    try:
        import copernicusmarine
    except ImportError as exc:
        raise ForcingError("copernicusmarine is not installed") from exc
    min_lon, min_lat, max_lon, max_lat = bbox
    raw_out.parent.mkdir(parents=True, exist_ok=True)
    try:
        copernicusmarine.subset(
            dataset_id=dataset_id,
            variables=["uo", "vo"],
            minimum_longitude=min_lon, maximum_longitude=max_lon,
            minimum_latitude=min_lat, maximum_latitude=max_lat,
            minimum_depth=0.0, maximum_depth=1.0,
            start_datetime=t0.strftime("%Y-%m-%dT%H:%M:%S"),
            end_datetime=t1.strftime("%Y-%m-%dT%H:%M:%S"),
            output_directory=str(raw_out.parent),
            output_filename=raw_out.name,
            username=username,
            password=password,
            force_download=True,
        )
    except Exception as exc:
        raise ForcingError(f"CMEMS currents download failed: {exc}") from exc
    if not raw_out.is_file():
        raise ForcingError("CMEMS subset reported success but wrote no file")


def normalize_currents(raw_path: Path, out_path: Path) -> None:
    with xr.open_dataset(raw_path) as raw:
        ds = _rename_standard(raw)
        for var in ("uo", "vo"):
            if var not in ds.data_vars:
                raise ForcingError(f"currents file has no '{var}'; found {list(ds.data_vars)}")
        out = xr.Dataset({"uo": ds["uo"], "vo": ds["vo"]})
        out = out.transpose("time", "latitude", "longitude").sortby("time").load()
    # Currents are NaN over land by design. Coastal boxes are mostly land, so only fail when
    # the file is essentially empty (for example, a wrong bounding box or dataset).
    nan_frac = float(out["uo"].isnull().mean())
    if nan_frac > 0.95:
        raise ForcingError(f"currents are {nan_frac:.0%} NaN over the box; the download is empty")
    log.info("currents: %d times, %.1f%% of cells are land/no-data (NaN), sea cells have data",
             out.sizes["time"], 100 * nan_frac)
    _write(out, out_path)


# --------------------------------------------------------------------------- wind
def fetch_wind(raw_out: Path, bbox, t0: datetime, t1: datetime, cds_url: str, cds_key: str) -> None:
    try:
        import cdsapi
    except ImportError as exc:
        raise ForcingError("cdsapi is not installed") from exc
    min_lon, min_lat, max_lon, max_lat = bbox
    days = pd.date_range(pd.Timestamp(t0.date()), pd.Timestamp(t1.date()), freq="D")
    request = {
        "product_type": ["reanalysis"],
        "variable": ["10m_u_component_of_wind", "10m_v_component_of_wind"],
        "year": sorted({f"{d.year:04d}" for d in days}),
        "month": sorted({f"{d.month:02d}" for d in days}),
        "day": sorted({f"{d.day:02d}" for d in days}),
        "time": [f"{h:02d}:00" for h in range(24)],
        "area": [max_lat, min_lon, min_lat, max_lon],
        "data_format": "netcdf",
        "download_format": "unarchived",
    }
    raw_out.parent.mkdir(parents=True, exist_ok=True)
    try:
        client = cdsapi.Client(url=cds_url, key=cds_key)
        client.retrieve("reanalysis-era5-single-levels", request, str(raw_out))
    except Exception as exc:
        raise ForcingError(f"ERA5 download failed: {exc}") from exc
    if zipfile.is_zipfile(raw_out):          # CDS sometimes wraps the NetCDF in a zip
        with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(raw_out) as z:
            members = [m for m in z.namelist() if m.endswith(".nc")]
            if not members:
                raise ForcingError("ERA5 zip contained no NetCDF file")
            z.extract(members[0], tmp)
            raw_out.unlink()
            Path(tmp, members[0]).replace(raw_out)


def normalize_wind(raw_path: Path, out_path: Path, t0: datetime, t1: datetime) -> None:
    with xr.open_dataset(raw_path) as raw:
        ds = _rename_standard(raw)
        for var in ("u10", "v10"):
            if var not in ds.data_vars:
                raise ForcingError(f"wind file has no '{var}'; found {list(ds.data_vars)}")
        out = xr.Dataset({"u10": ds["u10"], "v10": ds["v10"]})
        out = out.transpose("time", "latitude", "longitude").sortby("time")
        out = out.sel(time=slice(pd.Timestamp(t0), pd.Timestamp(t1))).load()
    if out.sizes["time"] == 0:
        raise ForcingError("ERA5 returned no hours inside the requested window")
    _write(out, out_path)



# --------------------------------------------------------------------------- Open-Meteo wind
OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
GRID_STEP_DEG = 0.25          # same resolution as ERA5 single levels
OM_BATCH = 100                # locations per request (Open-Meteo accepts comma-separated lists)


def wind_components(speed_ms, direction_from_deg):
    """Meteorological convention: direction is where the wind comes FROM.
    u points east, v points north, both in m/s."""
    spd = np.asarray(speed_ms, dtype="float64")
    rad = np.radians(np.asarray(direction_from_deg, dtype="float64"))
    return -spd * np.sin(rad), -spd * np.cos(rad)


def parse_openmeteo_response(data, n_points: int, n_times_expected: int | None = None):
    """Return (times, speed[T, P], direction[T, P]) from an Open-Meteo multi-location response."""
    if isinstance(data, dict):
        data = [data]
    if len(data) != n_points:
        raise ForcingError(f"Open-Meteo returned {len(data)} locations for {n_points} requested")
    times = data[0].get("hourly", {}).get("time", [])
    if not times or (n_times_expected is not None and len(times) != n_times_expected):
        raise ForcingError("Open-Meteo returned an unexpected number of hours")
    speed = np.full((len(times), n_points), np.nan)
    direction = np.full((len(times), n_points), np.nan)
    for j, loc in enumerate(data):
        h = loc.get("hourly", {})
        if h.get("time") != times:
            raise ForcingError("Open-Meteo locations have different time axes")
        sp = [np.nan if v is None else v for v in h.get("wind_speed_10m", [])]
        dr = [np.nan if v is None else v for v in h.get("wind_direction_10m", [])]
        speed[:, j] = sp
        direction[:, j] = dr
    return times, speed, direction


def fetch_wind_openmeteo(raw_out: Path, bbox, t0: datetime, t1: datetime) -> None:
    """Hourly 10 m wind from the Open-Meteo ERA5 archive, on a 0.25 deg grid, stored as u10/v10."""
    import requests
    min_lon, min_lat, max_lon, max_lat = bbox
    lons = np.arange(math.floor(min_lon / GRID_STEP_DEG) * GRID_STEP_DEG,
                     math.ceil(max_lon / GRID_STEP_DEG) * GRID_STEP_DEG + 1e-9, GRID_STEP_DEG)
    lats = np.arange(math.floor(min_lat / GRID_STEP_DEG) * GRID_STEP_DEG,
                     math.ceil(max_lat / GRID_STEP_DEG) * GRID_STEP_DEG + 1e-9, GRID_STEP_DEG)
    points = [(float(la), float(lo)) for la in lats for lo in lons]
    session = requests.Session()
    session.headers["User-Agent"] = "oil-slick-pipeline/1.0 (stage6 wind)"

    all_times = None
    speed_parts, dir_parts = [], []
    for i in range(0, len(points), OM_BATCH):
        batch = points[i:i + OM_BATCH]
        params = {
            "latitude": ",".join(f"{la:.4f}" for la, _ in batch),
            "longitude": ",".join(f"{lo:.4f}" for _, lo in batch),
            "hourly": "wind_speed_10m,wind_direction_10m",
            "wind_speed_unit": "ms",
            "timezone": "UTC",
            "start_date": t0.date().isoformat(),
            "end_date": t1.date().isoformat(),
        }
        data = _openmeteo_get(session, params)
        times, speed, direction = parse_openmeteo_response(data, len(batch))
        if all_times is None:
            all_times = times
        elif times != all_times:
            raise ForcingError("Open-Meteo batches returned different hours")
        speed_parts.append(speed)
        dir_parts.append(direction)
        log.info("Open-Meteo wind: %d / %d grid points", min(i + OM_BATCH, len(points)), len(points))

    speed = np.concatenate(speed_parts, axis=1)
    direction = np.concatenate(dir_parts, axis=1)
    u, v = wind_components(speed, direction)
    shape = (len(all_times), len(lats), len(lons))
    times_np = pd.to_datetime(all_times).values.astype("datetime64[ns]")
    ds = xr.Dataset(
        {"u10": (("time", "latitude", "longitude"), u.reshape(shape)),
         "v10": (("time", "latitude", "longitude"), v.reshape(shape))},
        coords={"time": times_np, "latitude": lats, "longitude": lons},
    )
    raw_out.parent.mkdir(parents=True, exist_ok=True)
    ds.to_netcdf(raw_out)
    log.info("wrote Open-Meteo wind grid %dx%d, %d hours -> %s", len(lats), len(lons), len(all_times), raw_out.name)


def _openmeteo_get(session, params: dict):
    import requests
    for attempt in range(1, 7):
        try:
            r = session.get(OPEN_METEO_ARCHIVE, params=params, timeout=90)
        except requests.RequestException as exc:
            wait = min(2 ** attempt, 60)
            log.warning("Open-Meteo network error (%s); retry in %ds", exc, wait)
            time.sleep(wait)
            continue
        if r.status_code == 200:
            time.sleep(0.5)               # stay polite with the free service
            return r.json()
        if r.status_code == 429 or r.status_code >= 500:
            wait = 20 * attempt
            log.warning("Open-Meteo HTTP %d; retry %d/6 in %ds", r.status_code, attempt, wait)
            time.sleep(wait)
            continue
        raise ForcingError(f"Open-Meteo HTTP {r.status_code}: {r.text[:200]}")
    raise ForcingError("Open-Meteo: retries exhausted")

# --------------------------------------------------------------------------- cache + entry point
def ensure_forcing(bbox, t_acq: datetime, runtime_hours: int, cache_dir: Path, cmems_dataset: str,
                   cmems_user: str, cmems_pass: str, cds_url: str, cds_key: str,
                   wind_source: str = "openmeteo") -> Tuple[Path, Path]:
    """Return (currents.nc, wind.nc) for the scene, downloading only what is missing."""
    t0, t1 = forcing_window(t_acq, runtime_hours)
    box = "_".join(f"{v:.2f}" for v in bbox)
    tag = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{t_acq:%Y%m%dT%H%M%S}_{box}")
    d = Path(cache_dir) / tag
    cur, wind = d / "currents.nc", d / "wind.nc"

    if not cur.is_file():
        raw = d / "currents_raw.nc"
        log.info("fetching CMEMS currents %s for %s..%s", cmems_dataset, t0, t1)
        fetch_currents(raw, bbox, t0, t1, cmems_dataset, cmems_user, cmems_pass)
        normalize_currents(raw, cur)
        raw.unlink(missing_ok=True)
    if not wind.is_file():
        raw = d / "wind_raw.nc"
        if wind_source == "openmeteo":
            log.info("fetching ERA5 wind via Open-Meteo for %s..%s", t0, t1)
            fetch_wind_openmeteo(raw, bbox, t0, t1)
        elif wind_source == "cds":
            log.info("fetching ERA5 wind via CDS for %s..%s", t0, t1)
            fetch_wind(raw, bbox, t0, t1, cds_url, cds_key)
        else:
            raise ForcingError(f"WIND_SOURCE must be 'openmeteo' or 'cds', got {wind_source!r}")
        normalize_wind(raw, wind, t0, t1)
        raw.unlink(missing_ok=True)

    check_coverage(cur, t_acq - timedelta(hours=runtime_hours), t_acq, "currents")
    check_coverage(wind, t_acq - timedelta(hours=runtime_hours), t_acq, "wind")
    return cur, wind
