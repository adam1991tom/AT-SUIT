#!/usr/bin/env bash
# One-step install of AT-SUIT on a fresh Linux server (Ubuntu, Debian and similar):
#   curl -fsSL https://raw.githubusercontent.com/adam1991tom/at-suit/HEAD/get.sh | sudo bash
#   curl -fsSL .../get.sh | sudo bash -s -- --port 8180 --tls      (install.sh options pass through)
# It installs Docker if it's missing, puts AT-SUIT in /opt/at-suit (or $ATSUIT_DIR)
# and runs install.sh. Run it again any time: it updates the files and keeps the data.
#   ATSUIT_REF=v1.0.0   install that release instead of the latest
set -euo pipefail
REPO="${ATSUIT_REPO:-adam1991tom/at-suit}"
DIR="${ATSUIT_DIR:-/opt/at-suit}"
REF="${ATSUIT_REF:-}"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
[ "$(id -u)" = 0 ] || { echo "Run it with sudo: curl -fsSL …/get.sh | sudo bash"; exit 1; }
command -v curl >/dev/null || { echo "curl is needed: apt-get install -y curl"; exit 1; }

if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  say "Installing Docker…"
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker 2>/dev/null || true
fi

if [ -z "$REF" ]; then
  # The newest release; the default branch (HEAD) if there isn't one yet.
  REF="$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest" 2>/dev/null | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -1)"
  REF="${REF:-HEAD}"
fi

say "Getting AT-SUIT ${REF}…"
mkdir -p "$DIR"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
curl -fsSL "https://codeload.github.com/${REPO}/tar.gz/${REF}" | tar xz -C "$TMP" --strip-components 1
# New files over the old ones; .env, backups and anything else of the venue's stay.
cp -a "$TMP"/. "$DIR"/
cd "$DIR"
echo "$REPO" > .atsuit-repo

say "Starting AT-SUIT…"
./install.sh "$@"
echo "Installed in ${DIR}. To update later: sudo ${DIR}/update.sh (it backs up first and rolls back if anything goes wrong)."
