#!/usr/bin/env bash
# One-off rollout for the push-queue fix (2026-09-16).
# Order matters: the 'skipped' status must exist before the new code writes it.
set -euo pipefail
cd "$(dirname "$0")"

SOCK="/tmp/csql/armbrust-3pl:us-central1:shipstation-db"
export MYSQL_PWD="$(gcloud secrets versions access latest --secret=SHIPSTATION_DB_PASSWORD --project=armbrust-3pl)"

echo "== 1. migration: add 'skipped', clear the stuck rows"
mysql --socket="$SOCK" -u shipstation_user inventory \
  --init-command="SET SESSION lock_wait_timeout=10" \
  < ../sql/migrations/2026-09-16_cart_order_box_skipped.sql
mysql --socket="$SOCK" -u shipstation_user inventory -t -e "
  SELECT b.status, o.order_status, COUNT(*) n
  FROM cart_order_box b JOIN shipstation.shipstation_orders o USING(order_id)
  GROUP BY 1,2 ORDER BY 1,2;"

echo "== 2. pause the schedule so an old image can't run mid-deploy"
gcloud scheduler jobs pause cartonization-engine-job --location=us-central1

echo "== 3. deploy"
./deploy.sh

echo "== 4. resume the schedule"
gcloud scheduler jobs resume cartonization-engine-job --location=us-central1

echo "== done. Next run is at the next :x3/:x8 minute."
