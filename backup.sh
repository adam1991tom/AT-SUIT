#!/usr/bin/env bash
# Save the whole data volume (database, uploads, key, models) to ./backups.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p backups
STAMP=$(date +%Y%m%d-%H%M%S)
VOL=$(docker volume ls -q | grep -E '(^|_)atsuit-data$' | head -1)
[ -n "$VOL" ] || { echo "No atsuit-data volume found."; exit 1; }
docker run --rm -v "$VOL":/data:ro -v "$PWD/backups":/backup alpine \
  tar czf "/backup/atsuit-$STAMP.tgz" --exclude=./models -C /data .
echo "Saved backups/atsuit-$STAMP.tgz (speech models are left out; they download again)."
