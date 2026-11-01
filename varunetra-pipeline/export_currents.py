#!/usr/bin/env python3
"""
Export archived ocean currents from the pipeline's cache to the frontend's JSON file.

    python export_currents.py <cache_folder>

<cache_folder> is a folder that contains currents.nc (variables uo, vo on time, latitude,
longitude). The cache is READ ONLY: nothing is written into it.

Output: public/data/ocean-currents/currents.json (inside the frontend repo), in the
WindGrid-compatible layout used by the wind route:
  - regular 0.25 deg grid, north row first, longitude west to east
  - flat row-major arrays: index = row * ni + col, row = latitude index, col = longitude index
  - dx > 0 (eastward step), dy < 0 (southward step)
  - u, v in m/s; null = land or no data
One frame every 3 hours, up to 24 frames.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import xarray as xr
from global_land_mask import globe

GRID = 0.25
FRAME_STEP_HOURS = 3
MAX_FRAMES = 24
REPO_ROOT = Path(__file__).resolve().parent   # folder that contains this script
OUT_PATH = REPO_ROOT / "public" / "data" / "ocean-currents" / "currents.json"


def build_grid(lat_min, lat_max, lon_min, lon_max):
    """Regular grid with north row first (lat descending) and west column first (lon ascending)."""
    lat0 = math.ceil(lat_max / GRID) * GRID
    lat1 = math.floor(lat_min / GRID) * GRID
    lon0 = math.ceil(lon_min / GRID) * GRID
    lon1 = math.floor(lon_max / GRID) * GRID
    lats = np.round(np.arange(lat0, lat1 - 1e-9, -GRID), 4)
    lons = np.round(np.arange(lon0, lon1 + 1e-9, GRID), 4)
    return lats, lons


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python export_currents.py <cache_folder>")
        return 2
    cache = Path(argv[1])
    src = cache / "currents.nc"
    if not src.is_file():
        print(f"FAIL: {src} not found")
        return 1

    with xr.open_dataset(src) as ds:
        if "depth" in ds.dims:
            ds = ds.isel(depth=0, drop=True)
        ds = ds[["uo", "vo"]]
        lat_min, lat_max = float(ds.latitude.min()), float(ds.latitude.max())
        lon_min, lon_max = float(ds.longitude.min()), float(ds.longitude.max())
        lats, lons = build_grid(lat_min, lat_max, lon_min, lon_max)
        ni, nj = len(lons), len(lats)

        times = ds.time.values[::FRAME_STEP_HOURS][:MAX_FRAMES]
        land2d = globe.is_land(np.repeat(lats[:, None], ni, axis=1),
                               np.repeat(lons[None, :], nj, axis=0))

        frames = []
        for t in times:
            frame = ds.sel(time=t).interp(latitude=lats, longitude=lons, method="linear")
            frame = frame.transpose("latitude", "longitude")
            u = frame["uo"].values.astype("float64")
            v = frame["vo"].values.astype("float64")
            bad = land2d | ~np.isfinite(u) | ~np.isfinite(v)
            u = np.where(bad, np.nan, np.round(u, 3))
            v = np.where(bad, np.nan, np.round(v, 3))
            frames.append({
                "time": str(np.datetime_as_string(np.datetime64(t, "s"), unit="s")) + "Z",
                "ni": ni, "nj": nj,
                "la1": float(lats[0]), "lo1": float(lons[0]),
                "dx": GRID, "dy": -GRID,
                "u": [None if math.isnan(x) else float(x) for x in u.ravel()],
                "v": [None if math.isnan(x) else float(x) for x in v.ravel()],
            })

    data_time = frames[0]["time"] if frames else None
    out = {
        "meta": {
            "source": "CMEMS archived currents (cache from the VARUNETRA pipeline)",
            "units": "m/s",
            "convention": "u = eastward velocity, v = northward velocity (direction the flow moves)",
            "data_time": data_time,
            "status": "archived",
            "grid_step_deg": GRID,
        },
        "frames": frames,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    print(f"PASS: wrote {OUT_PATH} ({len(frames)} frames, grid {nj} x {ni}, first frame {data_time})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
