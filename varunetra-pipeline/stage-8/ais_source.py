#!/usr/bin/env python3
"""
Stage 8 - AIS positions for the backtracked area and time window.

Source chosen by AIS_SOURCE in .env:
    neon : table ais_positions (live collector) joined to vessels for names.
           ts is integer Unix seconds (migration schema).
    csv  : MarineCadastre-style CSV for testing. Columns read: MMSI, BaseDateTime (UTC),
           LAT, LON, SOG, COG, VesselName, IMO, VesselType, Length.

Both sources go through the same cleaning: missing values, impossible speeds, duplicate
(mmsi, ts) and single-point glitches whose implied speed exceeds MAX_IMPLIED_KN are removed.
"""
from __future__ import annotations

import logging
import re
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import numpy as np
import pandas as pd

log = logging.getLogger("stage8.ais")

MAX_SOG_KN = 50.0          # reported speed above this is an error
MAX_IMPLIED_KN = 60.0      # speed implied between consecutive reports above this is a glitch
KM_PER_NM = 1.852
COLUMNS = ["mmsi", "ts", "lat", "lon", "sog", "cog", "name", "imo", "vessel_type", "length_m"]
NUMERIC = ["mmsi", "ts", "lat", "lon", "sog", "cog", "vessel_type", "length_m"]
CSV_MAP = {
    "MMSI": "mmsi", "BaseDateTime": "when", "LAT": "lat", "LON": "lon", "SOG": "sog",
    "COG": "cog", "VesselName": "name", "IMO": "imo", "VesselType": "vessel_type", "Length": "length_m",
}
EPOCH = pd.Timestamp("1970-01-01", tz="UTC")
DMA_FILE_RE = re.compile(r"^aisdk-(\d{4})-(\d{2})-(\d{2})\.csv$", re.IGNORECASE)
DMA_RENAME = {"Timestamp": "when", "MMSI": "mmsi", "Latitude": "lat", "Longitude": "lon",
              "SOG": "sog", "COG": "cog", "Name": "name", "IMO": "imo", "Length": "length_m",
              "Type of mobile": "mobile"}
DMA_REQUIRED = ["when", "mmsi", "lat", "lon"]
PLACEHOLDER_TEXT = {"", "unknown", "nan", "none", "null"}


def to_epoch(t: datetime) -> int:
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return int(t.timestamp())


def haversine_km(lat1, lon1, lat2, lon2) -> np.ndarray:
    lat1, lon1, lat2, lon2 = (np.radians(np.asarray(x, dtype=float)) for x in (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0088 * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=COLUMNS)


def clean(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return _empty()
    df = df.reindex(columns=COLUMNS).copy()
    for c in NUMERIC:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["mmsi", "ts", "lat", "lon"])
    df["mmsi"] = df["mmsi"].astype("int64")
    df["ts"] = df["ts"].astype("int64")
    df = df[df["sog"].isna() | df["sog"].between(0, MAX_SOG_KN)]
    df = df.sort_values(["mmsi", "ts"]).drop_duplicates(["mmsi", "ts"], keep="last")
    n0 = len(df)
    df = _drop_glitches(df)
    log.info("AIS cleaning: %d rows -> %d after glitch filter", n0, len(df))
    return df.reset_index(drop=True)


def _drop_glitches(df: pd.DataFrame) -> pd.DataFrame:
    """
    Drop report i when the jump into it is impossible but the jump over it (previous -> next)
    is plausible. A valid report that follows a glitch is kept, because its own predecessor
    is compared with the report before the glitch, not with the glitch.
    """
    g = df.groupby("mmsi", sort=False)
    prev_ts, prev_lat, prev_lon = g["ts"].shift(1), g["lat"].shift(1), g["lon"].shift(1)
    next_ts, next_lat, next_lon = g["ts"].shift(-1), g["lat"].shift(-1), g["lon"].shift(-1)

    def knots(t0, la0, lo0, t1, la1, lo1):
        dt_h = (t1 - t0).to_numpy() / 3600.0
        dist = haversine_km(la0.to_numpy(), lo0.to_numpy(), la1.to_numpy(), lo1.to_numpy())
        with np.errstate(invalid="ignore", divide="ignore"):
            return dist / dt_h / KM_PER_NM, dt_h

    kn_in, dt_in = knots(prev_ts, prev_lat, prev_lon, df["ts"], df["lat"], df["lon"])
    kn_skip, dt_skip = knots(prev_ts, prev_lat, prev_lon, next_ts, next_lat, next_lon)
    bad_in = (dt_in > 0) & (kn_in > MAX_IMPLIED_KN)
    ok_skip = (dt_skip > 0) & (kn_skip <= MAX_IMPLIED_KN)
    glitch = bad_in & ok_skip
    return df.loc[~glitch]


def load_neon(database_url: str, bbox, t0: datetime, t1: datetime) -> pd.DataFrame:
    import psycopg2
    min_lon, min_lat, max_lon, max_lat = bbox
    sql = """
        SELECT p.mmsi, p.ts, p.lat, p.lon, p.sog, p.cog,
               v.name, v.imo, v.vessel_type, v.length_m
        FROM ais_positions p
        LEFT JOIN vessels v ON v.mmsi = p.mmsi
        WHERE p.ts BETWEEN %s AND %s
          AND p.lat BETWEEN %s AND %s
          AND p.lon BETWEEN %s AND %s
    """
    with closing(psycopg2.connect(database_url)) as conn, conn.cursor() as cur:
        cur.execute(sql, (to_epoch(t0), to_epoch(t1), min_lat, max_lat, min_lon, max_lon))
        rows = cur.fetchall()
    log.info("Neon ais_positions: %d rows in window", len(rows))
    return clean(pd.DataFrame(rows, columns=COLUMNS))


def load_csv(path: str, bbox, t0: datetime, t1: datetime, chunksize: int = 500_000) -> pd.DataFrame:
    min_lon, min_lat, max_lon, max_lat = bbox
    e0, e1 = to_epoch(t0), to_epoch(t1)
    parts: List[pd.DataFrame] = []
    reader = pd.read_csv(path, usecols=lambda c: c in CSV_MAP, chunksize=chunksize, low_memory=False)
    for chunk in reader:
        chunk = chunk.rename(columns=CSV_MAP)
        when = pd.to_datetime(chunk["when"], errors="coerce", utc=True)
        chunk["ts"] = (when - EPOCH) // pd.Timedelta(seconds=1)
        chunk = chunk.dropna(subset=["ts", "lat", "lon"])
        keep = (chunk["ts"].between(e0, e1)
                & chunk["lat"].between(min_lat, max_lat)
                & chunk["lon"].between(min_lon, max_lon))
        parts.append(chunk.loc[keep].reindex(columns=COLUMNS))
    df = pd.concat(parts, ignore_index=True) if parts else _empty()
    log.info("CSV %s: %d rows in window and box", path, len(df))
    return clean(df)


def _clean_text(series: pd.Series) -> pd.Series:
    s = series.astype("string").str.strip()
    return s.mask(s.str.lower().isin(PLACEHOLDER_TEXT))


def dma_files_for_window(folder: str, t0: datetime, t1: datetime) -> List[Path]:
    """Daily files aisdk-YYYY-MM-DD.csv that overlap the window (plus one day of margin)."""
    root = Path(folder)
    if not root.is_dir():
        raise ValueError(f"AIS_CSV_DIR is not a folder: {root}")
    lo = t0.date() - timedelta(days=1)
    hi = t1.date() + timedelta(days=1)
    chosen: List[Path] = []
    for p in sorted(root.iterdir()):
        m = DMA_FILE_RE.match(p.name)
        if not m or not p.is_file():
            continue
        day = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if lo <= day <= hi:
            chosen.append(p)
    if not chosen:
        raise ValueError(f"no aisdk-YYYY-MM-DD.csv files for {t0.date()}..{t1.date()} in {root}")
    have = {DMA_FILE_RE.match(p.name).group(0) for p in chosen}
    log.info("DMA daily files used: %s", ", ".join(sorted(have)))
    return chosen


DMA_WANT = {"Timestamp", "Type of mobile", "MMSI", "Latitude", "Longitude", "SOG", "COG",
            "Name", "IMO", "Length"}
CACHE_PAD_DEG = 3.0


def _cache_key(files: List[Path], t0: datetime, t1: datetime) -> str:
    import hashlib
    h = hashlib.sha256()
    for f in files:
        st = f.stat()
        h.update(f"{f.name}|{st.st_size}|{int(st.st_mtime)}".encode())
    h.update(f"{t0:%Y%m%d%H}|{t1:%Y%m%d%H}".encode())
    return h.hexdigest()[:16]


def _extract_dma(files: List[Path], box, t0: datetime, t1: datetime, chunksize: int) -> pd.DataFrame:
    """One pass over the daily files: only needed columns, only the time window and box."""
    min_lon, min_lat, max_lon, max_lat = box
    e0, e1 = to_epoch(t0), to_epoch(t1)
    parts: List[pd.DataFrame] = []
    for path in files:
        log.info("reading %s (%.1f GB)", path.name, path.stat().st_size / 1e9)
        reader = pd.read_csv(
            path, chunksize=chunksize, low_memory=False, dtype=str, engine="c",
            on_bad_lines="warn",
            usecols=lambda c: c.strip().lstrip("#").strip() in DMA_WANT,
        )
        for chunk in reader:
            chunk.columns = [c.strip().lstrip("#").strip() for c in chunk.columns]
            missing = [c for c in ("Timestamp", "MMSI", "Latitude", "Longitude") if c not in chunk.columns]
            if missing:
                raise ValueError(f"{path.name}: needed columns not found: {missing}")
            chunk = chunk.rename(columns=DMA_RENAME)
            if "mobile" in chunk.columns:
                chunk = chunk[chunk["mobile"].fillna("").str.contains("Class", case=False)]
            when = pd.to_datetime(chunk["when"], format="%d/%m/%Y %H:%M:%S", errors="coerce", utc=True)
            chunk = chunk.assign(ts=(when - EPOCH) // pd.Timedelta(seconds=1))
            for col in ("name", "imo"):
                if col in chunk.columns:
                    chunk[col] = _clean_text(chunk[col])
            chunk = chunk.dropna(subset=["ts"])
            for c in ("lat", "lon", "sog", "cog", "mmsi", "ts"):
                chunk[c] = pd.to_numeric(chunk[c], errors="coerce")
            keep = (chunk["ts"].between(e0, e1)
                    & chunk["lat"].between(min_lat, max_lat)
                    & chunk["lon"].between(min_lon, max_lon))
            parts.append(chunk.loc[keep].reindex(columns=COLUMNS))
    return pd.concat(parts, ignore_index=True) if parts else _empty()


def load_dma_folder(folder: str, bbox, t0: datetime, t1: datetime, chunksize: int = 500_000,
                    cache_dir: str = "") -> pd.DataFrame:
    """
    Danish Maritime Authority daily AIS files (aisdk-YYYY-MM-DD.csv), read once per time window.

    The first call reads the daily files and keeps a filtered copy in cache_dir, covering the
    requested box plus CACHE_PAD_DEG. Later calls for the same window and an area inside that
    box read the small cache instead of the 3 GB files. Only Class A/B reports are kept.
    Timestamps are dd/mm/yyyy hh:mm:ss, treated as UTC.
    """
    import pickle
    min_lon, min_lat, max_lon, max_lat = bbox
    files = dma_files_for_window(folder, t0, t1)
    cache_root = Path(cache_dir) if cache_dir else Path(__file__).resolve().parent / "cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    key = _cache_key(files, t0, t1)
    cache_file = cache_root / f"dma_{key}.pkl"

    cached_box, cached_df = None, None
    if cache_file.is_file():
        with open(cache_file, "rb") as fh:
            cached_box, cached_df = pickle.load(fh)
        inside = (cached_box[0] <= min_lon and cached_box[1] <= min_lat
                  and cached_box[2] >= max_lon and cached_box[3] >= max_lat)
        if not inside:
            cached_df = None
    if cached_df is None:
        # Rebuild to cover both the old and the new area, so alternating slicks do not
        # keep re-reading the 3 GB files.
        req = (min_lon - CACHE_PAD_DEG, min_lat - CACHE_PAD_DEG, max_lon + CACHE_PAD_DEG, max_lat + CACHE_PAD_DEG)
        if cached_box is not None:
            req = (min(req[0], cached_box[0]), min(req[1], cached_box[1]),
                   max(req[2], cached_box[2]), max(req[3], cached_box[3]))
        cover = (max(req[0], -180.0), max(req[1], -90.0), min(req[2], 180.0), min(req[3], 90.0))
        cached_df = _extract_dma(files, cover, t0, t1, chunksize)
        cached_box = cover
        with open(cache_file, "wb") as fh:
            pickle.dump((cached_box, cached_df), fh)
        log.info("cached %d reports for box %s in %s", len(cached_df), cover, cache_file.name)
    else:
        log.info("using cached AIS extract %s (%d reports)", cache_file.name, len(cached_df))

    e0, e1 = to_epoch(t0), to_epoch(t1)
    sel = (cached_df["ts"].between(e0, e1)
           & cached_df["lat"].between(min_lat, max_lat)
           & cached_df["lon"].between(min_lon, max_lon))
    log.info("DMA folder: %d Class A/B reports in window and box", int(sel.sum()))
    return clean(cached_df.loc[sel].reset_index(drop=True))


def load_ais(source: str, bbox, t0: datetime, t1: datetime, *, database_url: str = "",
             csv_path: str = "", folder: str = "", cache_dir: str = "") -> pd.DataFrame:
    if source == "neon":
        return load_neon(database_url, bbox, t0, t1)
    if source == "csv":
        return load_csv(csv_path, bbox, t0, t1)
    if source == "dma_folder":
        return load_dma_folder(folder, bbox, t0, t1, cache_dir=cache_dir)
    raise ValueError(f"unknown AIS source {source!r}; use 'neon' or 'csv'")


def to_vessels(df: pd.DataFrame) -> List[Dict]:
    """Shape AIS rows as the vessel dicts stage-9 rank_vessels expects."""
    vessels: List[Dict] = []
    if df.empty:
        return vessels
    for mmsi, g in df.groupby("mmsi", sort=False):
        g = g.sort_values("ts")
        names = [n for n in g["name"].dropna().astype(str).str.strip() if n]
        pts = [
            {"time": datetime.fromtimestamp(int(t), tz=timezone.utc).replace(tzinfo=None).isoformat(),
             "lon": float(lo), "lat": float(la)}
            for t, lo, la in zip(g["ts"], g["lon"], g["lat"])
        ]
        vessels.append({"id": str(int(mmsi)), "name": names[-1] if names else None, "points": pts})
    return vessels
