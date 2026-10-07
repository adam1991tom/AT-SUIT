#!/usr/bin/env bash
# Back up, pull the latest code, rebuild and restart. Data is kept.
set -euo pipefail
cd "$(dirname "$0")"
./backup.sh
if [ -d .git ]; then git pull --ff-only; fi
PROFILE=()
docker ps --format '{{.Names}}' | grep -qx atsuit-tls && PROFILE=(--profile tls)
docker compose "${PROFILE[@]}" build --build-arg VERSION="$(cat VERSION 2>/dev/null || echo dev)"
docker compose "${PROFILE[@]}" up -d
echo "Updated. A backup was saved in ./backups first."
