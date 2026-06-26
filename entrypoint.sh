#!/bin/sh
# Container start: create tables + seed the base data (idempotent), then run the app.
# SEED_DEMO defaults to 0 here so a real deploy never plants fake cache rows.
set -e

export SEED_DEMO="${SEED_DEMO:-0}"

echo "Seeding (SEED_DEMO=$SEED_DEMO) ..."
python -m app.seed

echo "Starting uvicorn on 0.0.0.0:8000 ..."
exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
