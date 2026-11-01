# Stages 6-9 integration: what changed

Stages 1-4 and db_ingest.py are unchanged.

## Changed existing files (small edits)
- stage-5/estimate_age.py: adds `spill_id` to each feature (`varunetra_<scene>_<slick>`) before
  db_ingest reads the file. db_ingest already uses this value when present.
- pipeline.py: one new step "Backtrack and vessel ranking (stages 6-9)" after database ingestion.
  It runs with STAGE_6_9_PYTHON (the Parcels 3 environment) when set.
- .env.example, .gitignore, README.md: new keys, ignore rules, and a stages 6-9 section.

## New files
- stage-6/fetch_forcing.py  CMEMS currents + ERA5 wind, normalised, cached. Fails loudly, no dummy data.
- stage-7/backtrack.py      Parcels 3 backtracking. Replaces the broken kernels in the earlier module.
- stage-8/ais_source.py     AIS from Neon ais_positions or a MarineCadastre CSV, with cleaning.
- stage-9/rank_vessels.py   your ranking code. Only the per-vessel loop changed: the age is chosen by
                            nearest approach, not by peak density, and an age range is reported.
- stage-6-9/config.py       settings from .env
- stage-6-9/run_stages.py   orchestrator for the selected slicks
- stage-6-9/db_results.py   writes spills, snapshots, spill_ais, vessel_rankings
- steps6-9-requirements.txt Parcels 3 environment

## Superseded (safe to delete after you confirm)
- varunetra_steps5_9.py and steps5-9-requirements.txt (old Parcels 2 approach)

## Known fixes over the earlier backtrack module
- wind and diffusion kernels assigned undefined names (particle_dlon); now particle.lon/lat +=
- land kernel read a field that was never loaded, and a bare except hid the error; removed
- diffusion used an unseeded RNG; now seeded per run
- forcing time coverage is checked before the run, not extrapolated silently
- CMEMS dataset is set in .env (the old default was daily, not hourly)
- ERA5 data was zipped on some CDS responses; handled

## Not yet verified
- Parcels 3 kernel and recovery API, copernicusmarine.subset arguments, cdsapi request keys
- Neon writes (SQL placeholder counts checked, no database run)
- Runtime per slick

## Two-part split (current)
- Part 1: `pipeline.py` runs stages 1-4, then `store_slicks.py` writes slicks to `spills`
  with age and origin empty and origin_source NULL (pending). Stages 5-9 are no longer in pipeline.py.
- Part 2: `part2_run.py` takes pending slicks from Neon, runs stage 5 (writes age_hours), then
  stages 6-9 per scene (`stage-6-9/run_stages.py`). Slicks not ranked in a run are marked
  `origin_source = 'not_selected'` so they are not picked again.
- `db_ingest.py` is no longer called by the pipeline; `store_slicks.py` replaces it.
