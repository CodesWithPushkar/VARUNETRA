#!/usr/bin/env python3
"""
Check public/data/ocean-currents/currents.json against the contract used by the wind route.
Prints PASS or FAIL and exits with code 1 on any failure. Reads only; writes nothing.

    python validate_currents.py
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
from global_land_mask import globe

REPO_ROOT = Path(__file__).resolve().parent   # folder that contains this script
PATH = REPO_ROOT / "public" / "data" / "ocean-currents" / "currents.json"
MAX_SPEED = 60.0


def fail(msg: str) -> int:
    print(f"FAIL: {msg}")
    return 1


def main() -> int:
    if not PATH.is_file():
        return fail(f"{PATH} not found; run export_currents.py first")
    data = json.loads(PATH.read_text(encoding="utf-8"))

    for key in ("meta", "frames"):
        if key not in data:
            return fail(f"missing top-level key '{key}'")
    meta = data["meta"]
    for key in ("units", "convention", "data_time", "status", "grid_step_deg"):
        if key not in meta:
            return fail(f"meta is missing '{key}'")
    frames = data["frames"]
    if not frames:
        return fail("no frames")

    times = [f["time"] for f in frames]
    if times != sorted(times):
        return fail("frame times are not sorted")

    for idx, f in enumerate(frames):
        for key in ("time", "ni", "nj", "la1", "lo1", "dx", "dy", "u", "v"):
            if key not in f:
                return fail(f"frame {idx} is missing '{key}'")
        ni, nj = f["ni"], f["nj"]
        if len(f["u"]) != ni * nj or len(f["v"]) != ni * nj:
            return fail(f"frame {idx}: u/v length {len(f['u'])}/{len(f['v'])} != ni*nj {ni * nj}")
        if f["dx"] <= 0 or f["dy"] >= 0:
            return fail(f"frame {idx}: expected dx > 0 and dy < 0")

        u = np.array([np.nan if x is None else x for x in f["u"]], dtype=float).reshape(nj, ni)
        v = np.array([np.nan if x is None else x for x in f["v"]], dtype=float).reshape(nj, ni)
        finite = np.isfinite(u) & np.isfinite(v)
        if np.any(np.hypot(u[finite], v[finite]) > MAX_SPEED):
            return fail(f"frame {idx}: speed above {MAX_SPEED} m/s")

        lat = f["la1"] + np.arange(nj)[:, None] * f["dy"] + np.zeros((1, ni))
        lon = f["lo1"] + np.arange(ni)[None, :] * f["dx"] + np.zeros((nj, 1))
        on_land = globe.is_land(lat, lon)
        if np.any(finite & on_land):
            n = int(np.sum(finite & on_land))
            return fail(f"frame {idx}: {n} value(s) lie on land")

    print(f"PASS: {PATH.name} | {len(frames)} frames | status={meta['status']} | "
          f"data_time={meta['data_time']} | no values on land")
    return 0


if __name__ == "__main__":
    sys.exit(main())
