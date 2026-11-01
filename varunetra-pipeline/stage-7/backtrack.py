#!/usr/bin/env python3
"""
Stage 7 - backward Lagrangian tracking of an oil slick (Parcels v3).

Particles are seeded uniformly inside the slick at the acquisition time t_acq and
advected backwards with RK4 current advection, a windage drift (a fraction of the
10 m wind), and a random-walk diffusion term.

Output convention (consumed by stage-9 rank_vessels.py):
    lon, lat : float64 [N_particles, T]  NaN = particle lost (left the forcing domain
                                         or removed by Parcels)
    times    : datetime64[s] [T]         index 0 = t_acq, index k = t_acq - k hours

Forcing inputs must already be normalised by stage 6:
    currents: variables uo, vo      dims (time, latitude, longitude)
    wind:     variables u10, v10    dims (time, latitude, longitude)

Research prototype. Results are relative likelihoods, not proof of origin.
"""
from __future__ import annotations

import argparse
import inspect
import json
import logging
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Tuple, Union

import netCDF4 as nc
import numpy as np
import shapely
import shapely.geometry as sg
import xarray as xr
from parcels import AdvectionRK4, Field, FieldSet, ParticleSet, ScipyParticle, StatusCode
from shapely.geometry.base import BaseGeometry

log = logging.getLogger("stage7.backtrack")

M_PER_DEG = 111_000.0
OUTPUT_INTERVAL_H = 1          # output grid is hourly; to_hourly relies on this
COORD_LON, COORD_LAT, COORD_TIME = "longitude", "latitude", "time"


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------
def parse_utc_naive(value: str) -> datetime:
    """ISO 8601 -> naive UTC datetime. Naive input is assumed to be UTC."""
    dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def load_slick_geometry(polygon: Union[str, BaseGeometry]) -> BaseGeometry:
    """Accept a shapely geometry or a path to a GeoJSON Feature / FeatureCollection / Geometry."""
    if isinstance(polygon, BaseGeometry):
        geom = polygon
    else:
        data = json.loads(Path(polygon).read_text(encoding="utf-8"))
        kind = data.get("type")
        if kind == "FeatureCollection":
            geom = shapely.union_all([sg.shape(f["geometry"]) for f in data["features"] if f.get("geometry")])
        elif kind == "Feature":
            geom = sg.shape(data["geometry"])
        else:
            geom = sg.shape(data)
    geom = shapely.make_valid(geom)
    if geom.geom_type == "GeometryCollection":
        polys = [g for g in geom.geoms if g.geom_type in ("Polygon", "MultiPolygon")]
        geom = shapely.union_all(polys) if polys else geom
    if geom.is_empty or geom.geom_type not in ("Polygon", "MultiPolygon"):
        raise ValueError(f"Slick geometry is not a usable polygon: {geom.geom_type}")
    return geom


def seed_in_geometry(geom: BaseGeometry, n_particles: int, random_seed=None) -> Tuple[np.ndarray, np.ndarray]:
    """Rejection-sample n points uniformly inside the geometry (holes respected)."""
    rng = np.random.default_rng(random_seed)
    minx, miny, maxx, maxy = geom.bounds
    lons, lats, got = [], [], 0
    for _ in range(200):
        m = max(4 * (n_particles - got), 1000)
        x = rng.uniform(minx, maxx, m)
        y = rng.uniform(miny, maxy, m)
        keep = shapely.contains_xy(geom, x, y)
        lons.append(x[keep])
        lats.append(y[keep])
        got += int(keep.sum())
        if got >= n_particles:
            break
    if got < n_particles:
        raise ValueError("Could not seed enough particles inside the slick (polygon too thin or invalid).")
    return np.concatenate(lons)[:n_particles], np.concatenate(lats)[:n_particles]


# ---------------------------------------------------------------------------
# Parcels kernels (Parcels v3: state changes go through particle.<var> += ...)
# ---------------------------------------------------------------------------
def windage_kernel(particle, fieldset, time):
    """Wind drift = windage_fraction * wind velocity. Sign is correct for negative dt."""
    du = fieldset.u_wind[time, particle.depth, particle.lat, particle.lon]
    dv = fieldset.v_wind[time, particle.depth, particle.lat, particle.lon]
    coslat = math.cos(math.radians(particle.lat))
    particle.lon += fieldset.windage_fraction * du * particle.dt / (111000.0 * coslat)
    particle.lat += fieldset.windage_fraction * dv * particle.dt / 111000.0


def diffusion_kernel(particle, fieldset, time):
    """Isotropic random walk with variance 2*Kh*|dt| per step."""
    sigma_m = math.sqrt(2.0 * fieldset.Kh * abs(particle.dt))
    coslat = math.cos(math.radians(particle.lat))
    particle.lon += random.gauss(0.0, 1.0) * sigma_m / (111000.0 * coslat)
    particle.lat += random.gauss(0.0, 1.0) * sigma_m / 111000.0


def edge_guard_kernel(particle, fieldset, time):
    """Remove particles that get close to the edge of the current data. This keeps them away
    from positions where Parcels cannot interpolate, so the run does not stop with an error."""
    if (particle.lon < fieldset.lon_lo or particle.lon > fieldset.lon_hi
            or particle.lat < fieldset.lat_lo or particle.lat > fieldset.lat_hi):
        particle.state = StatusCode.Delete


def delete_lost_particle(particle, fieldset, time):
    """Recovery kernel: remove particles that leave the forcing domain."""
    particle.state = StatusCode.Delete


# ---------------------------------------------------------------------------
# Forcing checks and simulation
# ---------------------------------------------------------------------------
def assert_time_coverage(path: str, t_start: datetime, t_end: datetime, label: str) -> None:
    with xr.open_dataset(path) as ds:
        t = ds[COORD_TIME].values.astype("datetime64[ns]")
    lo, hi = t.min(), t.max()
    if lo > np.datetime64(t_start, "ns") or hi < np.datetime64(t_end, "ns"):
        raise ValueError(f"{label} covers {lo}..{hi} but the backtrack needs {t_start}..{t_end}")


def to_hourly(ds: xr.Dataset, t_acq: datetime, runtime_hours: int):
    """Map Parcels output (any obs times) onto the fixed hourly grid, index 0 = t_acq."""
    traj = "trajectory" if "trajectory" in ds.dims else "traj"
    n_steps = int(runtime_hours) + 1
    obs = [d for d in ds["lon"].dims if d != traj][0]
    lon = ds["lon"].transpose(traj, obs).values.astype(np.float64)
    lat = ds["lat"].transpose(traj, obs).values.astype(np.float64)
    times = ds["time"].transpose(traj, obs).values.astype("datetime64[ns]")
    order = np.argsort(ds[traj].values)
    lon, lat, times = lon[order], lat[order], times[order]

    age_h = (np.datetime64(t_acq, "ns") - times) / np.timedelta64(1, "h")
    k = np.rint(age_h)
    valid = (np.isfinite(lon) & np.isfinite(lat) & np.isfinite(age_h)
             & (np.abs(age_h - k) < 0.5) & (k >= 0) & (k < n_steps))
    lon_out = np.full((lon.shape[0], n_steps), np.nan)
    lat_out = np.full((lon.shape[0], n_steps), np.nan)
    ii, jj = np.nonzero(valid)
    kk = k[ii, jj].astype(int)
    lon_out[ii, kk] = lon[ii, jj]
    lat_out[ii, kk] = lat[ii, jj]
    times_out = np.datetime64(t_acq, "s") - np.arange(n_steps) * np.timedelta64(1, "h")
    return lon_out, lat_out, times_out.astype("datetime64[s]")


class BacktrackSimulation:
    def __init__(self, currents_path: str, wind_path: str, t_acq: datetime, runtime_hours: int = 72,
                 windage: float = 0.03, kh: float = 10.0, dt_minutes: float = 10.0, random_seed=None):
        if t_acq.tzinfo is not None:
            raise ValueError("t_acq must be naive UTC (use parse_utc_naive)")
        if int(runtime_hours) != runtime_hours or runtime_hours <= 0:
            raise ValueError("runtime_hours must be a positive whole number of hours")
        if dt_minutes <= 0 or windage < 0 or kh < 0:
            raise ValueError("dt_minutes must be positive; windage and kh must be non-negative")
        self.currents_path = str(currents_path)
        self.wind_path = str(wind_path)
        self.t_acq = t_acq
        self.runtime_hours = int(runtime_hours)
        self.windage = float(windage)
        self.kh = float(kh)
        self.dt_minutes = float(dt_minutes)
        self.random_seed = random_seed

    def _build_fieldset(self) -> FieldSet:
        dims = {"lon": COORD_LON, "lat": COORD_LAT, "time": COORD_TIME}
        fs = FieldSet.from_netcdf(
            filenames={"U": self.currents_path, "V": self.currents_path},
            variables={"U": "uo", "V": "vo"},
            dimensions=dims,
            allow_time_extrapolation=False,
        )
        fs.add_field(Field.from_netcdf(self.wind_path, variable=("u_wind", "u10"),
                                       dimensions=dims, allow_time_extrapolation=False))
        fs.add_field(Field.from_netcdf(self.wind_path, variable=("v_wind", "v10"),
                                       dimensions=dims, allow_time_extrapolation=False))
        fs.add_constant("windage_fraction", self.windage)
        fs.add_constant("Kh", self.kh)
        # edge guard: stay 0.25 deg inside the current data domain
        with xr.open_dataset(self.currents_path) as ds:
            lon = ds[COORD_LON].values
            lat = ds[COORD_LAT].values
        fs.add_constant("lon_lo", float(lon.min()) + 0.25)
        fs.add_constant("lon_hi", float(lon.max()) - 0.25)
        fs.add_constant("lat_lo", float(lat.min()) + 0.25)
        fs.add_constant("lat_hi", float(lat.max()) - 0.25)
        return fs

    def run(self, lons: np.ndarray, lats: np.ndarray):
        t_start = self.t_acq - timedelta(hours=self.runtime_hours)
        assert_time_coverage(self.currents_path, t_start, self.t_acq, "currents")
        assert_time_coverage(self.wind_path, t_start, self.t_acq, "wind")

        fieldset = self._build_fieldset()
        pset = ParticleSet(fieldset=fieldset, pclass=ScipyParticle, lon=lons, lat=lats, time=self.t_acq)
        kernels = (pset.Kernel(AdvectionRK4) + pset.Kernel(windage_kernel)
                   + pset.Kernel(diffusion_kernel) + pset.Kernel(edge_guard_kernel))
        if self.random_seed is not None:
            random.seed(self.random_seed)      # diffusion kernel draws from Python's random module
            np.random.seed(self.random_seed)

        log.info("Backtracking %d particles from %s for %d h (dt=%g min)",
                 len(lons), self.t_acq.isoformat(), self.runtime_hours, self.dt_minutes)
        with TemporaryDirectory(prefix="stage7-") as tmp:
            zarr_path = Path(tmp) / "particles.zarr"
            out = pset.ParticleFile(name=str(zarr_path), outputdt=timedelta(hours=OUTPUT_INTERVAL_H))
            exec_args = dict(
                pyfunc=kernels,
                runtime=timedelta(hours=self.runtime_hours),
                dt=-timedelta(minutes=self.dt_minutes),
                output_file=out,
            )
            # 'recovery' exists only in some Parcels 3.x releases; the edge guard covers the rest
            if "recovery" in inspect.signature(pset.execute).parameters:
                exec_args["recovery"] = {StatusCode.ErrorOutOfBounds: delete_lost_particle}
            else:
                log.info("this Parcels version has no 'recovery' argument; using the edge guard only")
            pset.execute(**exec_args)
            with xr.open_zarr(zarr_path) as ds:
                return to_hourly(ds, self.t_acq, self.runtime_hours)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def run_backtrack(polygon, t_acq, currents_path: str, wind_path: str, output_path=None,
                  runtime_hours: int = 72, n_particles: int = 2000, windage: float = 0.03,
                  kh: float = 10.0, dt_minutes: float = 10.0, seed=None) -> dict:
    """Seed inside the slick, backtrack, optionally write NetCDF. Returns lon/lat/times arrays."""
    if isinstance(t_acq, str):
        t_acq = parse_utc_naive(t_acq)
    geom = load_slick_geometry(polygon)
    lons, lats = seed_in_geometry(geom, n_particles, seed)
    sim = BacktrackSimulation(currents_path, wind_path, t_acq, runtime_hours, windage, kh, dt_minutes, seed)
    lon, lat, times = sim.run(lons, lats)
    if output_path is not None:
        write_tracks_netcdf(output_path, lon, lat, times)
    return {"lon": lon, "lat": lat, "times": times}


def write_tracks_netcdf(path, lon: np.ndarray, lat: np.ndarray, times: np.ndarray) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    xr.Dataset(
        {"lon": (("particle", "time"), lon), "lat": (("particle", "time"), lat)},
        coords={"time": ("time", times.astype("datetime64[s]"))},
    ).to_netcdf(path)


def load_tracks(path: str) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Read a backtrack NetCDF written by write_tracks_netcdf (or an older compatible file)."""
    with nc.Dataset(path, "r") as ds:
        lon = np.ma.filled(ds["lon"][:], np.nan).astype(np.float64)
        lat = np.ma.filled(ds["lat"][:], np.nan).astype(np.float64)
        if "time_iso" in ds.variables:
            times = np.array([np.datetime64(t, "s") for t in ds["time_iso"][:]], dtype="datetime64[s]")
        else:
            var = ds["time"]
            objs = nc.num2date(var[:], units=var.units, calendar=getattr(var, "calendar", "standard"))
            times = np.array([np.datetime64(o.isoformat(), "s") for o in objs], dtype="datetime64[s]")
    return lon, lat, times


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Stage 7: backtrack an oil slick with Parcels v3")
    ap.add_argument("--polygon", required=True, help="GeoJSON Feature/FeatureCollection/Geometry")
    ap.add_argument("--time", required=True, help="acquisition time, ISO 8601 (UTC)")
    ap.add_argument("--currents", required=True, help="normalised currents NetCDF (uo, vo)")
    ap.add_argument("--wind", required=True, help="normalised wind NetCDF (u10, v10)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--hours", type=int, default=72)
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--windage", type=float, default=0.03)
    ap.add_argument("--kh", type=float, default=10.0)
    ap.add_argument("--dt-minutes", type=float, default=10.0)
    ap.add_argument("--seed", type=int, default=None)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    res = run_backtrack(a.polygon, a.time, a.currents, a.wind, a.out, a.hours, a.n,
                        a.windage, a.kh, a.dt_minutes, a.seed)
    alive = int(np.isfinite(res["lon"][:, -1]).sum())
    print(f"wrote {a.out}: {res['lon'].shape[0]} particles x {res['lon'].shape[1]} hours; "
          f"{alive} alive at the last hour")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())