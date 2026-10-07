#!/usr/bin/env bash
# Install or reinstall AT-SUIT with Docker. Safe to run again; data is kept.
#   ./install.sh                 port 8080
#   ./install.sh --port 8180     another port (side-by-side with old apps)
#   ./install.sh --tls           also serve https (needed for browser microphones)
#   ./install.sh --no-asr        smaller image without captions
#   ./install.sh --pull [TAG]    use the published image from GHCR instead of building (default tag: latest)
set -euo pipefail
cd "$(dirname "$0")"

PORT="" TLS=0 ASR="" HOST="" PULL=""
while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --tls) TLS=1; shift ;;
    --tls-host) HOST="$2"; shift 2 ;;
    --no-asr) ASR=0; shift ;;
    --pull) PULL="${2:-latest}"; if [ $# -ge 2 ] && [ "${2#--}" = "$2" ]; then shift 2; else PULL=latest; shift; fi ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

command -v docker >/dev/null || { echo "Docker isn't installed. Install it first: https://docs.docker.com/engine/install/"; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "Docker Compose v2 is needed (docker compose)."; exit 1; }

[ -f .env ] || cp .env.example .env
setenv() { if grep -q "^$1=" .env; then sed -i "s|^$1=.*|$1=$2|" .env; else echo "$1=$2" >> .env; fi; }
[ -n "$PORT" ] && setenv ATSUIT_PORT "$PORT"
[ -n "$ASR" ] && { setenv ATSUIT_WITH_ASR 0; setenv ATSUIT_ASR 0; }
[ -n "$PULL" ] && setenv ATSUIT_IMAGE "ghcr.io/adam1991tom/at-suit:${PULL}"
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
[ -n "$HOST" ] || HOST="$IP"
[ "$TLS" = 1 ] && setenv ATSUIT_TLS_HOST "${HOST:-localhost}"
set -a; . ./.env; set +a

PORT_IN_USE=$(ss -ltnH "sport = :${ATSUIT_PORT}" 2>/dev/null | head -1 || true)
if [ -n "$PORT_IN_USE" ] && ! docker ps --format '{{.Names}}' | grep -qx atsuit; then
  echo "Port ${ATSUIT_PORT} is already in use. Pick another with --port."; exit 1
fi

PROFILE=()
[ "$TLS" = 1 ] && PROFILE=(--profile tls)
if [ -n "$PULL" ]; then
  echo "Pulling ${ATSUIT_IMAGE}…"
  docker compose "${PROFILE[@]}" pull
  docker compose "${PROFILE[@]}" up -d --no-build
else
  echo "Building AT-SUIT (this takes a few minutes the first time)…"
  docker compose "${PROFILE[@]}" build --build-arg VERSION="$(cat VERSION 2>/dev/null || echo dev)"
  docker compose "${PROFILE[@]}" up -d
fi

echo -n "Waiting for the server"
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${ATSUIT_PORT}/api/health" >/dev/null 2>&1; then echo; break; fi
  echo -n "."; sleep 2
done
echo
echo "AT-SUIT is running."
echo "  Open http://${IP:-SERVER-IP}:${ATSUIT_PORT}/ to finish setup in the browser."
[ "$TLS" = 1 ] && echo "  https: https://${HOST:-SERVER-IP}:${ATSUIT_TLS_PORT}/ (install the root certificate on laptops; see docs/INSTALL.md)"
echo "  Tech laptops: open /node on the same address."
