#!/usr/bin/env bash
# Update AT-SUIT safely. Data is kept.
#   1. back up the data and remember the version that is running now;
#   2. get the new version (git pull or the newest release, then build; or pull the published image);
#   3. start it and wait until it answers healthy on the new version;
#   4. if it doesn't, put the backup and the old version back, so the venue
#      is never left without a working server.
#   ./update.sh            update
#   ./update.sh --check    only say which version is running
set -euo pipefail
# Run from a copy: the update can replace this file while it runs.
if [ -z "${ATSUIT_UPDATE_COPY:-}" ]; then
  cd "$(dirname "$0")"
  T="$(mktemp)"; cp "$(basename "$0")" "$T"
  ATSUIT_UPDATE_COPY=1 exec bash "$T" "$@"
fi
rm -f "$0"
[ -f .env ] && { set -a; . ./.env; set +a; }
PORT="${ATSUIT_PORT:-8080}"
health() { curl -fsS "http://127.0.0.1:${PORT}/api/health" 2>/dev/null | sed -n 's/.*"version": *"\([^"]*\)".*/\1/p'; }
OLD_VERSION="$(health || true)"
if [ "${1:-}" = "--check" ]; then echo "Running: ${OLD_VERSION:-not answering}"; exit 0; fi

PROFILE=()
docker ps --format '{{.Names}}' | grep -qx atsuit-tls && PROFILE=(--profile tls)

# 1. Backup, and keep the running image under atsuit:rollback.
./backup.sh
BACKUP="$(ls -t backups/atsuit-*.tgz | head -1)"
OLD_IMAGE="$(docker inspect -f '{{.Image}}' atsuit 2>/dev/null || true)"
[ -n "$OLD_IMAGE" ] && docker image tag "$OLD_IMAGE" atsuit:rollback

# 2. The new version.
if [ -d .git ]; then
  git pull --ff-only
elif [ -f .atsuit-repo ]; then
  # Installed with get.sh: fetch the newest release (or ATSUIT_REF) the same way.
  REPO="$(cat .atsuit-repo)" REF="${ATSUIT_REF:-}"
  [ -n "$REF" ] || REF="$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest" 2>/dev/null | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -1)"
  TMP="$(mktemp -d)"
  curl -fsSL "https://codeload.github.com/${REPO}/tar.gz/${REF:-main}" | tar xz -C "$TMP" --strip-components 1
  cp -a "$TMP"/. ./ && rm -rf "$TMP"
fi
if grep -q '^ATSUIT_IMAGE=ghcr.io/' .env 2>/dev/null; then
  docker compose "${PROFILE[@]}" pull
  docker compose "${PROFILE[@]}" up -d --no-build
else
  docker compose "${PROFILE[@]}" build --build-arg VERSION="$(cat VERSION 2>/dev/null || echo dev)"
  docker compose "${PROFILE[@]}" up -d
fi

# 3. Wait for it to answer healthy five times in a row (database upgrades run
#    on start, so give it a couple of minutes; a crash loop never gets five).
echo -n "Waiting for the new version"
NEW_VERSION="" OK=0
for _ in $(seq 1 90); do
  v="$(health || true)"
  if [ -n "$v" ]; then OK=$((OK + 1)); else OK=0; fi
  if [ "$OK" -ge 5 ]; then NEW_VERSION="$v"; echo; break; fi
  echo -n "."; sleep 2
done

if [ -n "$NEW_VERSION" ]; then
  echo "Updated: ${OLD_VERSION:-unknown} → ${NEW_VERSION}. The backup from before the update is ${BACKUP}."
  exit 0
fi

# 4. Roll back.
echo
echo "The new version didn't come up healthy. Putting ${OLD_VERSION:-the previous version} and the data back…"
docker compose logs --tail 40 atsuit || true
if [ -z "$OLD_IMAGE" ]; then echo "There was no previous version running to go back to. Your data is safe in ${BACKUP}."; exit 1; fi
./restore.sh --yes "$BACKUP" --no-start
ATSUIT_IMAGE=atsuit:rollback docker compose "${PROFILE[@]}" up -d --no-build --force-recreate atsuit
for _ in $(seq 1 60); do [ -n "$(health || true)" ] && break; sleep 2; done
echo "Rolled back: running $(health || echo 'the previous version') with the data from before the update."
echo "Nothing was lost. Send the log above to support, then run ./update.sh again once it's fixed."
exit 1
