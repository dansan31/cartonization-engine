#!/usr/bin/env bash
# Run the engine from the laptop against the live database, through cloud-sql-proxy.
# Pass --dry-run to calculate without writing anything.
set -euo pipefail

SOCKET_DIR=/tmp/csql
INSTANCE=armbrust-3pl:us-central1:shipstation-db

if [[ ! -S "$SOCKET_DIR/$INSTANCE" ]]; then
  echo "cloud-sql-proxy is not running. Start it with:"
  echo "  mkdir -p $SOCKET_DIR && /tmp/cloud-sql-proxy --unix-socket $SOCKET_DIR $INSTANCE &"
  exit 1
fi

export DB_SOCKET="$SOCKET_DIR/$INSTANCE"
export DB_USER=shipstation_user
export DB_NAME=inventory
export DB_PASS="$(gcloud secrets versions access latest --secret=SHIPSTATION_DB_PASSWORD --project=armbrust-3pl)"
[[ "${1:-}" == "--dry-run" ]] && export DRY_RUN=1

python3 "$(dirname "$0")/main.py"
