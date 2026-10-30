#!/bin/sh
# Container start: wait for Postgres, seed the factors and the verified peer figures, serve.
#
# Every loader applies every migration and upserts from committed data (literals, or
# data/peer_disclosures_verified.json), so it needs no workbook and changes nothing on the
# second start. That is what lets `docker compose up` be the whole setup on a fresh clone,
# every time. The peer load is `make seed-peer` without uv.
set -eu

python - <<'PY'
import sys, time
import psycopg
from carbon_r.config import get_settings

url = get_settings().database_url
for attempt in range(30):
    try:
        psycopg.connect(url, connect_timeout=3).close()
        break
    except psycopg.OperationalError as exc:
        print(f"waiting for the database ({attempt + 1}/30): {exc}".splitlines()[0], flush=True)
        time.sleep(2)
else:
    sys.exit("database never became reachable")
PY

python scripts/seed_factors.py
python scripts/load_desnz_factors.py
python scripts/peer_disclosures_file.py load

exec uvicorn carbon_r.main:app --host 0.0.0.0 --port 8000
