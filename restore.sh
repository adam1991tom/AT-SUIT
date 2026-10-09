#!/usr/bin/env bash
# Restore a backup made by backup.sh. Stops the server while it restores.
#   ./restore.sh backups/atsuit-YYYYmmdd-HHMMSS.tgz   asks first
#   ./restore.sh --yes FILE [--no-start]              no question (update.sh uses this)
set -euo pipefail
cd "$(dirname "$0")"
YES=0 START=1 FILE=""
for a in "$@"; do
  case "$a" in
    --yes) YES=1 ;;
    --no-start) START=0 ;;
    *) FILE="$a" ;;
  esac
done
[ -n "$FILE" ] || { echo "Usage: ./restore.sh backups/atsuit-YYYYmmdd-HHMMSS.tgz"; exit 1; }
[ -f "$FILE" ] || { echo "$FILE doesn't exist."; exit 1; }
VOL=$(docker volume ls -q | grep -E '(^|_)atsuit-data$' | head -1)
[ -n "$VOL" ] || { echo "No atsuit-data volume found. Run ./install.sh first."; exit 1; }
if [ "$YES" != 1 ]; then
  read -r -p "This replaces all current AT-SUIT data with $FILE. Type RESTORE to continue: " ok
  [ "$ok" = "RESTORE" ] || exit 1
fi
docker compose stop atsuit
DIR="$(cd "$(dirname "$FILE")" && pwd)"
docker run --rm -v "$VOL":/data -v "$DIR":/backup alpine \
  sh -c "find /data -mindepth 1 -maxdepth 1 ! -name models -exec rm -rf {} + && tar xzf /backup/$(basename "$FILE") -C /data && chown -R \$(stat -c %u:%g /data) /data"
[ "$START" = 1 ] && docker compose start atsuit
echo "Restored $FILE."
