#!/usr/bin/env bash
# Update AT-SUIT safely. Data is kept.
#   1. back up the data and remember the version that is running now;
#   2. get the new version (git pull or the newest release, then build; or pull the published image);
#   3. start it and wait until it answers healthy on the new version;
#   4. if it doesn't, put the backup and the old version back, so the venue
#      is never left without a working server.
#   ./update.sh            update
#   ./update.sh --check    only say which version is running
#   ./update.sh --auto     what the atsuit-update timer runs every few minutes: ask the server
#                          (Settings → Updates) and install the newest GitHub release only when
#                          it says so: updates on, a new version out, and no show on.
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
# Tell the server how an update went (Settings → Updates shows it).
report() { docker exec atsuit python -m atsuit.updates report "$@" >/dev/null 2>&1 || true; }

AUTO=0 WANT="" TOKEN=""
if [ "${1:-}" = "--auto" ]; then
  AUTO=1
  docker ps --format '{{.Names}}' | grep -qx atsuit || exit 0  # not running: nothing to ask
  PLAN="$(docker exec atsuit python -m atsuit.updates plan 2>/dev/null)" || exit 0
  plan() { printf '%s\n' "$PLAN" | sed -n "s/^$1=//p" | head -1; }
  if [ "$(plan ACTION)" != install ]; then echo "No update: $(plan REASON)"; exit 0; fi
  WANT="$(plan VERSION)" TOKEN="$(plan TOKEN)"
  ATSUIT_REF="$(plan TAG)"; export ATSUIT_REF
  if [ -f .atsuit-repo ] && [ -n "$(plan REPO)" ]; then plan REPO > .atsuit-repo; fi
  echo "$(date '+%F %T') Installing AT-SUIT ${WANT} (running ${OLD_VERSION:-unknown})"
  trap 'report failed "$WANT" "update.sh stopped at line $LINENO before the new version started"' ERR
fi

PROFILE=()
docker ps --format '{{.Names}}' | grep -qx atsuit-tls && PROFILE=(--profile tls)

# 1. Backup, and keep the running image under atsuit:rollback.
./backup.sh
BACKUP="$(ls -t backups/atsuit-*.tgz | head -1)"
OLD_IMAGE="$(docker inspect -f '{{.Image}}' atsuit 2>/dev/null || true)"
[ -n "$OLD_IMAGE" ] && docker image tag "$OLD_IMAGE" atsuit:rollback

# 2. The new version. A GitHub token (Settings → Updates) lets it read a private repository.
AUTH=() GITAUTH=()
if [ -n "$TOKEN" ]; then
  AUTH=(-H "Authorization: Bearer ${TOKEN}")
  GITAUTH=(-c "http.https://github.com/.extraheader=AUTHORIZATION: basic $(printf 'x-access-token:%s' "$TOKEN" | base64 | tr -d '\n')")
fi
IMAGE_INSTALL=0
if grep -q '^ATSUIT_IMAGE=ghcr.io/' .env 2>/dev/null; then IMAGE_INSTALL=1; fi
if [ -d .git ]; then
  if [ "$AUTO" = 1 ]; then
    git "${GITAUTH[@]}" fetch -q origin "refs/tags/${ATSUIT_REF}:refs/tags/${ATSUIT_REF}"
    git merge -q --ff-only "$ATSUIT_REF"
  else
    git pull --ff-only
  fi
elif [ -f .atsuit-repo ]; then
  # Installed with get.sh: fetch the newest release (or ATSUIT_REF) the same way.
  REPO="$(cat .atsuit-repo)" REF="${ATSUIT_REF:-}"
  [ -n "$REF" ] || REF="$(curl -fsSL "${AUTH[@]}" "https://api.github.com/repos/${REPO}/releases/latest" 2>/dev/null | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -1)"
  TMP="$(mktemp -d)"
  if [ -n "$TOKEN" ]; then
    curl -fsSL "${AUTH[@]}" "https://api.github.com/repos/${REPO}/tarball/${REF:-main}" | tar xz -C "$TMP" --strip-components 1
  else
    curl -fsSL "https://codeload.github.com/${REPO}/tar.gz/${REF:-main}" | tar xz -C "$TMP" --strip-components 1
  fi
  cp -a "$TMP"/. ./ && rm -rf "$TMP"
elif [ "$AUTO" = 1 ] && [ "$IMAGE_INSTALL" = 0 ]; then
  echo "This copy of AT-SUIT can't fetch new versions by itself (it isn't a git checkout, from get.sh or the published image)."
  trap - ERR; report failed "$WANT" "this install can't fetch new versions by itself: reinstall with get.sh"; exit 1
fi
if [ "$IMAGE_INSTALL" = 1 ]; then
  if [ "$AUTO" = 1 ]; then
    # The published image of exactly this release.
    sed -i "s|^ATSUIT_IMAGE=ghcr.io/\([^:]*\):.*|ATSUIT_IMAGE=ghcr.io/\1:${WANT}|" .env
    set -a; . ./.env; set +a
    if [ -n "$TOKEN" ]; then printf '%s' "$TOKEN" | docker login ghcr.io -u x-access-token --password-stdin >/dev/null 2>&1 || true; fi
  fi
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

trap - ERR
if [ -n "$NEW_VERSION" ]; then
  echo "Updated: ${OLD_VERSION:-unknown} → ${NEW_VERSION}. The backup from before the update is ${BACKUP}."
  report ok "${NEW_VERSION}" "from ${OLD_VERSION:-unknown}"
  # Installs from before 1.0.2 get the automatic updater on their first update.
  if [ "$(id -u)" = 0 ] && [ -f install.sh ] && [ ! -f /etc/systemd/system/atsuit-update.timer ]; then bash install.sh --updater || true; fi
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
report failed "${WANT:-${NEW_VERSION:-new}}" "didn't come up healthy; rolled back to ${OLD_VERSION:-the previous version}"
echo "Nothing was lost. Send the log above to support, then run ./update.sh again once it's fixed."
exit 1
