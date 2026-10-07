#!/usr/bin/env bash
# Restore a backup made by backup.sh. Stops the server while it restores.
set -euo pipefail
cd "$(dirname "$0")"
FILE="${1:?Usage: ./restore.sh backups/atsuit-YYYYmmdd-HHMMSS.tgz}"
VOL=$(docker volume ls -q | grep -E '(^|_)atsuit-data$' | head -1)
[ -n "$VOL" ] || { echo "No atsuit-data volume found. Run ./install.sh first."; exit 1; }
read -r -p "This replaces all current AT-SUIT data with $FILE. Type RESTORE to continue: " ok
[ "$ok" = "RESTORE" ] || exit 1
docker compose stop atsuit
docker run --rm -v "$VOL":/data -v "$PWD/$(dirname "$FILE")":/backup alpine \
  sh -c "find /data -mindepth 1 -maxdepth 1 ! -name models -exec rm -rf {} + && tar xzf /backup/$(basename "$FILE") -C /data && chown -R \$(stat -c %u:%g /data) /data"
docker compose start atsuit
echo "Restored."
