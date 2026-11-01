Replace these files in your project (same folder layout):
  stage-6\fetch_forcing.py
  stage-6-9\config.py
  stage-6-9\run_stages.py
  steps6-9-requirements.txt   (adds 'requests')

Then add to .env:
  WIND_SOURCE=openmeteo

Install the one new package in the Parcels environment:
  .\.venv-69\Scripts\python.exe -m pip install requests
