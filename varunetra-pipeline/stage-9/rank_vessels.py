"""
rank_vessels.py - Stage 9: rank vessels by how well each one explains the drifted oil.

Score for vessel v (age a = hours before the satellite picture, window [a_min, a_max]):

  S_v = 1/K * sum over output ages a_k in the window of
        (1/N) * sum over particles i of  Gauss2D( x_v(t_acq - a_k) ; X_i(a_k), sigma^2 )

  sigma^2(a, v) = sigma0^2 + (sigma_u * a)^2 + sigma_v^2

  P(v)    = S_v   / (1/A + sum_u S_u)
  P(none) = (1/A) / (1/A + sum_u S_u)        A = search area in km^2, equal priors for all vessels

  X_i(a)   backtracked particle positions (stage 7 output file backtrack_base.nc)
  x_v(t)   vessel position from AIS, linearly interpolated between pings
  sigma0   slick / grid resolution (km)           sigma_u  current + wind error growth (km per hour)
  sigma_v  vessel position error inside AIS gaps = speed * time to the nearest ping (0 at a ping)

This is our own formulation. It is NOT published or validated. sigma0 and sigma_u are placeholders:
estimate sigma_u from your stage 7 variant runs with estimate_sigma_u(). The output is a RELATIVE
ranking, not a calibrated probability of guilt.

Input file for the command line (vessels.json):
  {"vessels": [{"id": "MMSI or name", "name": "optional",
                "points": [{"time": "2026-01-10T01:00:00", "lon": 56.1, "lat": 25.2}, ...]}]}
Times are UTC.  Run:
  python rank_vessels.py --tracks out/backtrack_base.nc --vessels vessels.json --time 2026-01-10T06:00:00 \
      --age-min 20 --age-max 28 [--area-km2 5000] [--out ranking.json]
Exit codes: 0 ok, 2 bad input, 3 failed.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

log = logging.getLogger("rank_vessels")

KM_PER_DEG = 111.195          # 6371 km * pi / 180
SIGMA0_KM = 1.0               # placeholder
SIGMA_U_KM_H = 0.36           # placeholder: 0.1 m/s
V_FLOOR_KMH = 2.0             # minimum speed assumed when judging how far a vessel could move in a gap
MAX_EXTRAP_H = 6.0            # no evidence from a vessel more than this far outside its AIS record


# ---------- helpers ----------
def parse_time(s) -> datetime:
    txt = str(s).strip().replace("Z", "+00:00")
    t = datetime.fromisoformat(txt)
    return t.astimezone(timezone.utc).replace(tzinfo=None) if t.tzinfo else t


def estimate_sigma_u(centroid_shift_km: float, age_h: float) -> float:
    """sigma_u (km/h) from how far the origin centroid moved between stage 7 variants at a given age."""
    if age_h <= 0:
        raise ValueError("age_h must be positive")
    return float(centroid_shift_km) / float(age_h)


def _km_offsets(lon, lat, lon0: float, lat0: float):
    dlon = (np.asarray(lon) - lon0 + 180.0) % 360.0 - 180.0
    return dlon * KM_PER_DEG * math.cos(math.radians(lat0)), (np.asarray(lat) - lat0) * KM_PER_DEG


def _dist_km(lon1, lat1, lon2, lat2) -> float:
    dx, dy = _km_offsets(lon2, lat2, lon1, lat1)
    return float(np.hypot(dx, dy))


# ---------- vessel track ----------
def prepare_vessel(v: dict, t_acq: datetime) -> Optional[dict]:
    """Sort and clean AIS pings; times become hours relative to t_acq (negative = before)."""
    pts = v.get("points") or []
    rows = {}
    for p in pts:
        try:
            t = (np.datetime64(parse_time(p["time"]), "s") - np.datetime64(t_acq, "s")) / np.timedelta64(1, "h")
            lon, lat = float(p["lon"]), float(p["lat"])
        except (KeyError, ValueError, TypeError):
            continue
        if math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90:
            rows[float(t)] = (lon, lat)
    if not rows:
        return None
    th = np.array(sorted(rows))
    lon = np.array([rows[t][0] for t in th])
    lat = np.array([rows[t][1] for t in th])
    speeds = []
    for i in range(len(th) - 1):
        dt = th[i + 1] - th[i]
        if dt > 0:
            speeds.append(_dist_km(lon[i], lat[i], lon[i + 1], lat[i + 1]) / dt)
    v_med = max(float(np.median(speeds)) if speeds else 0.0, V_FLOOR_KMH)
    return {"id": str(v.get("id")), "name": v.get("name"), "th": th, "lon": lon, "lat": lat, "v_med": v_med}


def vessel_state(vp: dict, q_h: float, max_extrap_h: float = MAX_EXTRAP_H):
    """
    Position and position error at time q_h (hours relative to t_acq).
    Returns (lon, lat, sigma_v_km, in_gap) or None if there is no usable evidence at that time.
    """
    th = vp["th"]
    if q_h < th[0] or q_h > th[-1]:
        dt = (th[0] - q_h) if q_h < th[0] else (q_h - th[-1])
        if dt > max_extrap_h:
            return None
        i = 0 if q_h < th[0] else -1
        return float(vp["lon"][i]), float(vp["lat"][i]), vp["v_med"] * dt, True
    j = int(np.searchsorted(th, q_h))
    if j < len(th) and th[j] == q_h:
        return float(vp["lon"][j]), float(vp["lat"][j]), 0.0, False
    i0, i1 = j - 1, j
    frac = (q_h - th[i0]) / (th[i1] - th[i0])
    lon = vp["lon"][i0] + frac * ((vp["lon"][i1] - vp["lon"][i0] + 180.0) % 360.0 - 180.0)
    lat = vp["lat"][i0] + frac * (vp["lat"][i1] - vp["lat"][i0])
    seg_v = max(_dist_km(vp["lon"][i0], vp["lat"][i0], vp["lon"][i1], vp["lat"][i1]) / (th[i1] - th[i0]),
                V_FLOOR_KMH)
    sigma_v = seg_v * min(q_h - th[i0], th[i1] - q_h)
    gap = bool((th[i1] - th[i0]) > 1.0)    # more than an hour between pings counts as a gap
    return float(lon), float(lat), float(sigma_v), gap


# ---------- scoring ----------
def default_area_km2(lon, lat, pad_km: float = 20.0) -> float:
    ok = np.isfinite(lon) & np.isfinite(lat)
    if not ok.any():
        raise ValueError("no finite particle positions")
    w = float(_dist_km(lon[ok].min(), np.mean(lat[ok]), lon[ok].max(), np.mean(lat[ok])))
    h = (float(lat[ok].max()) - float(lat[ok].min())) * KM_PER_DEG
    return (w + 2 * pad_km) * (h + 2 * pad_km)


def rank_vessels(lon, lat, times, t_acq: datetime, age_min_h: float, age_max_h: float, vessels: Sequence[dict],
                 area_km2: Optional[float] = None, sigma0_km: float = SIGMA0_KM,
                 sigma_u_km_h: float = SIGMA_U_KM_H, max_extrap_h: float = MAX_EXTRAP_H) -> dict:
    """lon, lat: particle tracks [N, T]; times: datetime64[s] [T]. Returns the ranking dict."""
    if not (0 <= age_min_h <= age_max_h):
        raise ValueError("need 0 <= age_min <= age_max")
    if sigma0_km <= 0 or sigma_u_km_h < 0:
        raise ValueError("sigma0 must be > 0 and sigma_u >= 0")
    lon, lat = np.asarray(lon, dtype=float), np.asarray(lat, dtype=float)
    n_total = lon.shape[0]
    ages = (np.datetime64(t_acq, "s") - times) / np.timedelta64(1, "h")
    idx = np.where((ages >= age_min_h - 1e-6) & (ages <= age_max_h + 1e-6))[0]
    if len(idx) == 0:
        raise ValueError("no output time steps fall inside the age window")
    pool_x, pool_y = lon[:, idx], lat[:, idx]
    if not np.isfinite(pool_x).any():
        raise ValueError("no finite particle positions inside the age window")
    area = float(area_km2) if area_km2 else default_area_km2(pool_x, pool_y)
    if area <= 0:
        raise ValueError("area_km2 must be positive")

    prepared = [p for p in (prepare_vessel(v, t_acq) for v in vessels) if p is not None]
    results = []
    for vp in prepared:
        dens_k, used = [], 0
        profile = []               # (age_h, density) for every output step in the window
        closest = None             # (distance_km, age_h, sigma_km) at the nearest approach
        gap = False
        for k in idx:
            a = float(ages[k])
            st = vessel_state(vp, -a, max_extrap_h)
            if st is None:
                dens_k.append(0.0)
                profile.append((a, 0.0))
                continue
            vlon, vlat, sig_v, in_gap = st
            sigma2 = sigma0_km ** 2 + (sigma_u_km_h * a) ** 2 + sig_v ** 2
            ok = np.isfinite(lon[:, k]) & np.isfinite(lat[:, k])
            if not ok.any():
                dens_k.append(0.0)
                profile.append((a, 0.0))
                continue
            dx, dy = _km_offsets(lon[ok, k], lat[ok, k], vlon, vlat)
            d2 = dx * dx + dy * dy
            dens = float(np.exp(-d2 / (2 * sigma2)).sum() / (2 * math.pi * sigma2) / n_total)
            dens_k.append(dens)
            profile.append((a, dens))
            used += 1
            gap = gap or in_gap
            dmin = float(np.sqrt(d2.min()))
            # Age is chosen by nearest approach, not by peak density. Peak density is biased
            # toward young ages because the Gaussian normalisation 1/sigma^2 grows as sigma shrinks.
            if closest is None or dmin < closest[0] - 1e-9:
                closest = (dmin, a, math.sqrt(sigma2))
        score = float(np.sum(dens_k) / len(idx))
        reasons = []
        age_range = None
        if used == 0:
            reasons.append("no AIS evidence inside the age window")
        else:
            d_b, a_b, s_b = closest
            reasons.append(f"closest match to the drifted oil {d_b:.1f} km away at age {a_b:.0f} h "
                           f"(position uncertainty {s_b:.1f} km)")
            if gap:
                reasons.append("AIS gap inside the window widens the uncertainty")
            peak = max(d for _, d in profile)
            if peak > 0:
                strong = [a for a, d in profile if d >= 0.5 * peak]
                age_range = [min(strong), max(strong)]
        results.append({
            "id": vp["id"], "name": vp["name"], "score": score, "ages_used": used,
            "best_age_h": closest[1] if closest else None,
            "closest_km": closest[0] if closest else None,
            "age_range_h": age_range,
            "age_profile": [{"age_h": a, "density": d} for a, d in profile],
            "reasons": reasons,
        })

    inv_a = 1.0 / area
    denom = inv_a + sum(r["score"] for r in results)
    for r in results:
        r["P"] = r["score"] / denom
    results.sort(key=lambda r: r["P"], reverse=True)
    for rank, r in enumerate(results, 1):
        r["rank"] = rank
    return {"ranking": results, "P_none": inv_a / denom, "area_km2": area,
            "age_window_h": [age_min_h, age_max_h], "n_particles": int(n_total),
            "n_vessels_scored": len(results),
            "n_vessels_dropped": len(vessels) - len(prepared),
            "params": {"sigma0_km": sigma0_km, "sigma_u_km_h": sigma_u_km_h, "max_extrap_h": max_extrap_h}}


def rank_from_file(tracks_nc: str, vessels: Sequence[dict], t_acq: datetime, age_min_h: float, age_max_h: float,
                   **kw) -> dict:
    import backtrack                                  # stage 7 module; reads OpenDrift's output file
    lon, lat, times = backtrack.load_tracks(tracks_nc)
    return rank_vessels(lon, lat, times, t_acq, age_min_h, age_max_h, vessels, **kw)


# ---------- command line ----------
def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Stage 9: rank vessels against the backtracked oil")
    ap.add_argument("--tracks", required=True, help="stage 7 backtrack_*.nc")
    ap.add_argument("--vessels", required=True, help="vessels.json (see file header)")
    ap.add_argument("--time", required=True, help="picture time, UTC")
    ap.add_argument("--age-min", type=float, required=True)
    ap.add_argument("--age-max", type=float, required=True)
    ap.add_argument("--area-km2", type=float, default=None)
    ap.add_argument("--sigma0-km", type=float, default=SIGMA0_KM)
    ap.add_argument("--sigma-u-km-h", type=float, default=SIGMA_U_KM_H)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        with open(a.vessels, encoding="utf-8") as f:
            vessels = json.load(f)["vessels"]
        res = rank_from_file(a.tracks, vessels, parse_time(a.time), a.age_min, a.age_max, area_km2=a.area_km2,
                             sigma0_km=a.sigma0_km, sigma_u_km_h=a.sigma_u_km_h)
    except (ValueError, KeyError, OSError) as exc:
        print("bad input:", exc, file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print("failed:", repr(exc), file=sys.stderr)
        return 3
    print(f"{'rank':<5}{'vessel':<24}{'P':>8}{'closest km':>12}{'best age h':>12}")
    for r in res["ranking"]:
        label = (r["name"] or r["id"])[:22]
        ck = "-" if r["closest_km"] is None else f"{r['closest_km']:.1f}"
        ba = "-" if r["best_age_h"] is None else f"{r['best_age_h']:.0f}"
        print(f"{r['rank']:<5}{label:<24}{r['P']:>8.3f}{ck:>12}{ba:>12}")
    print(f"P(culprit not in the data) = {res['P_none']:.3f}")
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=2)
        print("wrote", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
