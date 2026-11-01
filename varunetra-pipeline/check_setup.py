#!/usr/bin/env python3
"""
Pre-flight check for VARUNETRA. Run it before the long pipeline run.

    python check_setup.py                 # local checks + database read check
    python check_setup.py --write-test    # also proves writes work, then rolls back (changes nothing)

It never prints secret values. Only the names of .env keys are shown.
"""
from __future__ import annotations

import argparse
import importlib
import os
import shutil
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
OK, FAIL, WARN = "[ OK ]", "[FAIL]", "[WARN]"
failures = 0


def report(status: str, msg: str) -> None:
    global failures
    if status == FAIL:
        failures += 1
    print(f"{status} {msg}")


def load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        report(FAIL, "python-dotenv missing. Activate the venv and run: python -m pip install -r requirements.txt")
        return
    env = PROJECT / ".env"
    if not env.is_file():
        report(FAIL, f"no .env file at {env}. Copy .env.example to .env and fill it in.")
        return
    load_dotenv(env)
    report(OK, ".env found")


def check_env(keys: list[str], optional: list[str]) -> None:
    for k in keys:
        report(OK if os.getenv(k, "").strip() else FAIL, f"{k} is {'set' if os.getenv(k, '').strip() else 'MISSING'}")
    for k in optional:
        report(OK if os.getenv(k, "").strip() else WARN, f"{k} is {'set' if os.getenv(k, '').strip() else 'empty (optional)'}")


def check_packages(modules: list[str], label: str) -> None:
    missing = []
    for m in modules:
        try:
            importlib.import_module(m)
        except Exception:
            missing.append(m)
    report(FAIL if missing else OK, f"{label}: " + (f"missing {', '.join(missing)}" if missing else "all importable"))


def check_files() -> None:
    model = (PROJECT / ".." / "model" / "best_model.pth").resolve()
    report(OK if model.is_file() else FAIL, f"trained model at {model}")
    targets = PROJECT / "targets.csv"
    report(OK if targets.is_file() else FAIL, "targets.csv present")
    gpt = shutil.which("gpt")
    report(OK if gpt else FAIL, f"SNAP 'gpt' on PATH: {gpt or 'not found (install SNAP and add its bin folder to PATH)'}")
    venv_ok = sys.prefix != sys.base_prefix
    report(OK if venv_ok else WARN, f"running inside a virtual environment: {sys.prefix}")


def check_database(write_test: bool) -> None:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        report(FAIL, "DATABASE_URL missing; database checks skipped")
        return
    try:
        import psycopg2
    except ImportError:
        report(FAIL, "psycopg2 missing; database checks skipped")
        return
    try:
        conn = psycopg2.connect(url, connect_timeout=10)
    except Exception as exc:
        report(FAIL, f"cannot connect to Neon: {str(exc).splitlines()[0]}")
        return
    report(OK, "connected to Neon")
    try:
        with conn.cursor() as cur:
            for table in ["spills", "reported_culprits", "ais_positions", "vessels",
                          "snapshots", "spill_ais", "vessel_rankings"]:
                cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
                if cur.fetchone()[0] is None:
                    report(WARN, f"table {table} does not exist yet (run create_schema.py; needed for part 2 only)")
                else:
                    cur.execute(f"SELECT COUNT(*) FROM {table}")
                    report(OK, f"table {table}: {cur.fetchone()[0]} rows readable")
            cur.execute("SELECT to_regclass('public.ais_positions') IS NOT NULL")
            if cur.fetchone()[0]:
                cur.execute("SELECT MAX(ts), COUNT(DISTINCT mmsi) FROM ais_positions")
                last_ts, n_ships = cur.fetchone()
                report(WARN if not n_ships else OK, f"ais_positions: {n_ships} distinct ships, latest ts={last_ts}")
        conn.rollback()
        if write_test:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO spills (spill_id, polygon_geojson, area_km2, age_hours, kind,
                        image_time_utc, origin_lat, origin_lon, origin_radius_km,
                        window_start_utc, window_end_utc, created_utc)
                    VALUES ('preflight_write_test', '{}', 0, 0, 'past', '2000-01-01T00:00:00Z',
                        0, 0, 0, '2000-01-01T00:00:00Z', '2000-01-01T00:00:00Z', '2000-01-01T00:00:00Z')
                """)
                cur.execute("SELECT COUNT(*) FROM spills WHERE spill_id = 'preflight_write_test'")
                seen = cur.fetchone()[0]
            conn.rollback()     # nothing is kept
            report(OK if seen == 1 else FAIL, "write test: insert works and was rolled back (no data changed)")
    except Exception as exc:
        conn.rollback()
        report(FAIL, f"database check error: {exc}")
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-test", action="store_true", help="test INSERT permission, then roll back")
    a = ap.parse_args()

    print("== 1. Environment file ==")
    load_env()
    check_env(["DATABASE_URL", "COPERNICUS_USERNAME", "COPERNICUS_PASSWORD"], ["SNAP_MEMORY", "SNAP_CACHE_SIZE"])
    print("\n== 2. Python packages (steps 1-5) ==")
    check_packages(["psycopg2", "dotenv", "geopandas", "numpy", "pandas", "requests", "pyproj",
                    "shapely", "rasterio", "scipy", "global_land_mask", "matplotlib", "torch"], "main venv")
    print("\n== 3. Files and tools ==")
    check_files()
    print("\n== 4. Stages 6-9 (Parcels) environment ==")
    stage_py = os.getenv("STAGE_6_9_PYTHON", "").strip()
    if not stage_py:
        report(WARN, "STAGE_6_9_PYTHON not set; stages 6-9 will use the main python and fail")
    else:
        report(OK if Path(stage_py).is_file() else FAIL, f"STAGE_6_9_PYTHON exists: {stage_py}")
    print("\n== 5. Database ==")
    check_database(a.write_test)

    print(f"\nResult: {'READY' if failures == 0 else f'{failures} problem(s) to fix before the run'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
