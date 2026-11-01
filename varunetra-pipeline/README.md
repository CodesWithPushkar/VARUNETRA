# VARUNETRA automated SAR pipeline

The Python pipeline reads target areas from `targets.csv`, downloads the newest matching Sentinel-1 IW GRD scene for each row from Copernicus Data Space Ecosystem (CDSE), and runs preprocessing, slick extraction, filtering, age estimation, and database ingestion. The Python scripts connect directly to Neon through `DATABASE_URL`; they do not call or modify the Node.js backend.

## Setup

1. Install ESA SNAP and make its `gpt` executable available on `PATH`.
2. Ensure the trained model exists at `../model/best_model.pth` relative to this folder.
3. Configure `DATABASE_URL`, `COPERNICUS_USERNAME`, and `COPERNICUS_PASSWORD` in the local `.env`. Use `.env.example` as a template. Never commit `.env`.
4. Install Python dependencies:

   ```powershell
   python -m pip install -r requirements.txt
   ```

5. Edit `targets.csv` with each location's bounding box and inclusive date range (`YYYY-MM-DD`).

## Run

From this directory:

```powershell
python pipeline.py --targets-csv targets.csv
```

For each CSV row, the downloader searches CDSE and processes the newest matching scene in the requested date range. It reports the downloaded ZIP's absolute path and exact acquisition time as JSON for the orchestrator. A failed download or processing stage is logged against that location, and the next CSV row is still attempted. The command exits non-zero if any target fails.

Products and intermediate outputs are stored under `downloads/`, `out/`, `sweep/`, `stage-4/`, and `stage-5/`. The large download and generated output directories are ignored by Git.

## Standalone Steps 5-9 environment

Steps 5-9 use a separate virtual environment and dependency list so the Steps 1-4 environment remains unchanged. From this directory, in Windows PowerShell:

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r .\steps5-9-requirements.txt
```

Run the backtracking and AIS matcher directly:

```powershell
python .\varunetra_steps5_9.py `
  --spill_id "varunetra_48939049" `
  --geojson_path "C:\data\slick_polygon.geojson" `
  --forcing_nc "C:\data\currents_wind.nc" `
  --ais_data "C:\data\historical_ais.csv"
```

The GeoJSON must contain `scene_time_utc` or `scene_time`; otherwise pass `--t_obs` as an ISO 8601 timestamp with a timezone offset. The NetCDF must contain `uo`, `vo`, `u10`, and `v10` on compatible grids/times. `--ais_data` currently accepts a NOAA-style AIS CSV. Set `NEON_DATABASE_URL` in the PowerShell process before running to persist results; use `--dry-run` to skip database writes.

## Stages 6-9: backtrack and vessel ranking

Runs automatically after `db_ingest.py` (added to `pipeline.py`). It needs its own Python 3.10 to 3.13 environment because Parcels 3 does not support the main Python used for stages 1-5.

```powershell
py -3.10 -m venv .venv-69
.\.venv-69\Scripts\Activate.ps1
python -m pip install -r .\steps6-9-requirements.txt
```

Set `STAGE_6_9_PYTHON` in `.env` to `.venv-69\Scripts\python.exe`. Set the CMEMS and CDS keys listed in `.env.example`. To run only this step on an existing scene:

```powershell
python .\stage-6-9\run_stages.py --scene-time 2026-09-18T01:02:38+00:00
```

Outputs: `stage-6-9/out/<spill_id>/` (backtrack NetCDF, ranking JSON, origin heatmap), `stage-6/cache/` (forcing), and Neon tables `spills`, `snapshots`, `spill_ais`, `vessel_rankings`.

For testing without live AIS, set `AIS_SOURCE=csv` and `AIS_CSV_PATH` to a MarineCadastre file that covers the slick area.
