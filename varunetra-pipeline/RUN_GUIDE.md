# VARUNETRA: run guide (two parts, Windows PowerShell)

The pipeline is split into two independent parts.

- **Part 1** (`pipeline.py`): downloads Sentinel-1 scenes, detects and filters oil slicks (stages 1-4), and stores them in Neon table `spills`. It needs only the main environment (.venv).
- **Part 2** (`part2_run.py`): reads the pending slicks from Neon, estimates age (stage 5), then backtracks, loads AIS, and ranks vessels (stages 6-9). It needs .venv for stage 5 and .venv-69 for stages 6-9.

Part 2 can run any time after part 1 finishes, on the same or another day.

## 1. Before you start

1. Python 3.12 (or the version you already use). Check with `py -0`.
2. Python 3.10, for part 2's Parcels environment.
3. ESA SNAP installed, with its `bin` folder on PATH. Test: `gpt -h`
4. The trained model at `..\model\best_model.pth`.
5. Accounts: Copernicus Data Space (CDSE), Copernicus Marine (CMEMS), Climate Data Store (CDS, with a token), and your Neon database URL.

## 2. Keys go in the `.env` file

```powershell
Copy-Item .env.example .env
notepad .env
```

Fill in (no spaces around `=`):
- `DATABASE_URL` (Neon, used by both parts)
- `COPERNICUS_USERNAME`, `COPERNICUS_PASSWORD` (part 1, downloads)
- `CMEMS_USERNAME`, `CMEMS_PASSWORD`, `CMEMS_CURRENTS_DATASET_ID` (part 2, hourly currents dataset)
- `CDSAPI_KEY` (part 2, wind). Leave `CDSAPI_URL` as it is.
- `AIS_SOURCE` (`neon` or `csv`) and `AIS_CSV_PATH` if you use `csv`
- `STAGE_6_9_PYTHON=.venv-69\Scripts\python.exe` (part 2)

Never share `.env`.

## 3. Create the two environments (once)

```powershell
# main environment: part 1 and stage 5
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
deactivate

# Parcels environment: stages 6-9 (part 2)
py -3.10 -m venv .venv-69
.\.venv-69\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r steps6-9-requirements.txt
deactivate
```

If PowerShell blocks activation, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

## 4. Pre-flight check

```powershell
.\.venv\Scripts\Activate.ps1
python check_setup.py
python check_setup.py --write-test
```

The second command tests database writes and rolls the test back. Fix every `[FAIL]` line first.

## 5. Part 1: download, detect, store (run first)

Test with one target first (about 30-60 minutes):

```powershell
Get-Content targets.csv | Select-Object -First 1 | Set-Content targets_test.csv
Get-Content targets.csv | Select-Object -Skip 1 -First 1 | Add-Content targets_test.csv
python pipeline.py --targets-csv targets_test.csv 2>&1 | Tee-Object -FilePath part1_test.log
```

Full run (start it at night):

```powershell
python pipeline.py --targets-csv targets.csv 2>&1 | Tee-Object -FilePath part1_full.log
```

Part 1 is finished when the log says `PART 1 FINISHED`. To store the stage-4 output again without downloading, run `python store_slicks.py` (safe to repeat; existing rows are skipped). `python store_slicks.py --dry-run` only shows what it would store.

## 6. Check that part 1 saved to the database

Run these in the Neon SQL editor:

```sql
-- new slicks, waiting for part 2 (origin_source is empty)
SELECT spill_id, image_time_utc, area_km2, detection_confidence, age_hours, origin_source
FROM spills WHERE spill_id LIKE 'varunetra_%' ORDER BY created_utc DESC LIMIT 20;

-- how many are pending
SELECT COUNT(*) AS pending FROM spills
WHERE origin_source IS NULL AND spill_id LIKE 'varunetra_%';
```

Expect one row per slick (71 for your test scene), `origin_source` empty and `age_hours` empty.

## 7. Part 2: age, backtrack, rank (run after part 1)

```powershell
.\.venv\Scripts\Activate.ps1
python part2_run.py 2>&1 | Tee-Object -FilePath part2.log
```

To process only one scene: `python part2_run.py --scene-time 2026-09-18T01:02:38+00:00`

Part 2 is finished when the log says `part 2 finished for all scenes`. A failed scene is logged and the next scene still runs; the exit code is 1 if any scene failed.

## 8. Check the results (morning)

```powershell
Select-String -Path part2.log -Pattern "failed|ERROR|finished"
dir stage-6-9\out
```

Neon SQL editor:

```sql
-- processed slicks: age and origin
SELECT spill_id, age_hours, age_sigma_hours, origin_lat, origin_lon, origin_source
FROM spills WHERE spill_id LIKE 'varunetra_%' AND origin_source IS NOT NULL
ORDER BY created_utc DESC LIMIT 20;

-- ranked vessels
SELECT spill_id, status, rank, mmsi, name, score, confidence, hours_before_image
FROM vessel_rankings ORDER BY created_utc DESC, rank LIMIT 30;

-- AIS snapshot used by each run
SELECT snapshot_id, spill_id, source, rows_found, vessels_found, warnings
FROM snapshots ORDER BY snapshot_id DESC LIMIT 10;
```

Each processed slick has `origin_source` set to `backtrack_top_candidate` or `backtrack_prior_estimate`. Slicks that were not in the top-N for their scene have `origin_source = 'not_selected'`.

## 9. Overnight tips

- Keep the laptop plugged in. In Settings > System > Power, set "sleep when plugged in" to Never.
- Log everything with `Tee-Object`, as shown above.

## 10. Limits

- AIS coverage decides whether a vessel can be found. Live AIS in Neon only started recently. MarineCadastre covers only US waters.
- The full two-part pipeline has not yet run end to end. Expect at least one fix. Send me the first error with the lines around it.
- Results are relative rankings, not proof of who caused a spill.

## 11. Troubleshooting

| Symptom | Fix |
|---|---|
| `DATABASE_URL is not set` | Fill `.env`. The file must be named `.env`, not `.env.txt`. |
| `gpt` not found | Add the SNAP `bin` folder to PATH, then open a new window. |
| `cannot connect` | Check the URL and that the Neon project is not paused. |
| `no pending slicks` in part 2 | Part 1 has not stored anything yet, or all slicks were already processed. |
| `stage 5 failed` | Check internet access; stage 5 downloads wind data. |
| `forcing unavailable` | Check the CMEMS and CDS keys, and that the dataset is hourly and covers the date. |
| `no AIS vessels in the window` | Expected when there is no AIS for the area and date. |
