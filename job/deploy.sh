#!/usr/bin/env bash
# Deploy the cartonization engine as a Cloud Run job.
# Re-run to ship a new version of main.py.
set -euo pipefail

PROJECT=armbrust-3pl
REGION=us-central1
JOB=cartonization-engine
INSTANCE=armbrust-3pl:us-central1:shipstation-db

gcloud run jobs deploy "$JOB" \
  --source=. \
  --project="$PROJECT" \
  --region="$REGION" \
  --set-cloudsql-instances="$INSTANCE" \
  --set-env-vars="DB_SOCKET=/cloudsql/$INSTANCE,DB_USER=shipstation_user,DB_NAME=inventory,DRY_RUN=0,PUSH_TO_SHIPSTATION=1,PUSH_LIMIT=10,PUSH_PAUSE_SECONDS=2.0" \
  --set-secrets="DB_PASS=SHIPSTATION_DB_PASSWORD:latest,SS_API_KEY=SHIPSTATION_API_KEY:latest,SS_API_SECRET=SHIPSTATION_API_SECRET:latest" \
  --max-retries=1 \
  --task-timeout=10m
