"""Settings for stages 6-9, read from the project .env. No secret is hard-coded."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from dotenv import load_dotenv

PIPELINE_DIR = Path(__file__).resolve().parent.parent


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not set in .env (see .env.example)")
    return value


def _parse_bbox(raw: str) -> Optional[Tuple[float, float, float, float]]:
    raw = raw.strip()
    if not raw:
        return None
    parts = [float(x) for x in raw.split(",")]
    if len(parts) != 4:
        raise ValueError("SLICK_BBOX must be min_lon,min_lat,max_lon,max_lat")
    min_lon, min_lat, max_lon, max_lat = parts
    if min_lon >= max_lon or min_lat >= max_lat:
        raise ValueError("SLICK_BBOX min values must be below max values")
    return (min_lon, min_lat, max_lon, max_lat)


@dataclass(frozen=True)
class Settings:
    database_url: str
    cmems_dataset_id: str
    cmems_username: str
    cmems_password: str
    wind_source: str
    cds_url: str
    cds_key: str
    ais_source: str
    ais_csv_path: str
    ais_dir: str
    slick_bbox: Optional[Tuple[float, float, float, float]]
    max_slicks: int
    runtime_hours: int
    n_particles: int
    windage: float
    kh: float
    dt_minutes: float
    seed: int
    pad_deg: float
    cache_dir: Path
    output_dir: Path


def load_settings(env_file: Path = PIPELINE_DIR / ".env") -> Settings:
    load_dotenv(env_file)
    ais_source = os.getenv("AIS_SOURCE", "neon").strip().lower()
    ais_csv = os.getenv("AIS_CSV_PATH", "").strip()
    ais_dir = os.getenv("AIS_CSV_DIR", "").strip()
    if ais_source not in ("neon", "csv", "dma_folder"):
        raise RuntimeError("AIS_SOURCE must be 'dma_folder', 'csv' or 'neon'")
    if ais_source == "csv" and not ais_csv:
        raise RuntimeError("AIS_SOURCE=csv needs AIS_CSV_PATH")
    if ais_source == "dma_folder" and not ais_dir:
        raise RuntimeError("AIS_SOURCE=dma_folder needs AIS_CSV_DIR (the folder with aisdk-YYYY-MM-DD.csv)")
    runtime = int(os.getenv("BACKTRACK_RUNTIME_HOURS", "72"))
    if not 1 <= runtime <= 72:
        raise RuntimeError("BACKTRACK_RUNTIME_HOURS must be between 1 and 72")
    return Settings(
        database_url=_required("DATABASE_URL"),
        cmems_dataset_id=_required("CMEMS_CURRENTS_DATASET_ID"),
        cmems_username=_required("CMEMS_USERNAME"),
        cmems_password=_required("CMEMS_PASSWORD"),
        wind_source=os.getenv("WIND_SOURCE", "openmeteo").strip().lower(),
        cds_url=os.getenv("CDSAPI_URL", "").strip(),
        cds_key=os.getenv("CDSAPI_KEY", "").strip(),
        ais_source=ais_source,
        ais_csv_path=ais_csv,
        ais_dir=ais_dir,
        slick_bbox=_parse_bbox(os.getenv("SLICK_BBOX", "")),
        max_slicks=int(os.getenv("MAX_SLICKS_PER_SCENE", "5")),
        runtime_hours=runtime,
        n_particles=int(os.getenv("BACKTRACK_N_PARTICLES", "1000")),
        windage=float(os.getenv("BACKTRACK_WINDAGE", "0.03")),
        kh=float(os.getenv("BACKTRACK_KH", "10")),
        dt_minutes=float(os.getenv("BACKTRACK_DT_MINUTES", "10")),
        seed=int(os.getenv("BACKTRACK_RANDOM_SEED", "42")),
        pad_deg=float(os.getenv("FORCING_PAD_DEG", "3.0")),
        cache_dir=PIPELINE_DIR / os.getenv("FORCING_CACHE_DIR", "stage-6/cache"),
        output_dir=PIPELINE_DIR / os.getenv("STAGE_6_9_OUTPUT_DIR", "stage-6-9/out"),
    )
