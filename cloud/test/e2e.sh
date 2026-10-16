#!/usr/bin/env bash
# End-to-end test of the cloud kit on this machine's Docker: two venues behind the front door,
# kept apart, set up only from the setup link, paused and resumed, backed up and restored,
# updated (and rolled back when the new version isn't healthy), and removed.
#
#   cloud/test/e2e.sh IMAGE:TAG        e.g. atsuit:ci (a locally built AT-SUITE image)
#
# Uses a throwaway ATCLOUD_ROOT and https on port 8443 with a stand-in certificate.
set -euo pipefail
IMG="${1:?usage: e2e.sh IMAGE:TAG}"
REPO_IMG="${IMG%:*}" TAG="${IMG##*:}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
export ATCLOUD_ROOT="${ATCLOUD_ROOT:-$(mktemp -d)/atcloud}"
AT="$HERE/atcloud"
D=atsuit.test PORT=8443
pass=0
ok() { pass=$((pass + 1)); echo "  ok - $*"; }
die() { echo "FAIL: $*" >&2; exit 1; }
is() { [ "$1" = "$2" ] || die "$3 (got '$1', wanted '$2')"; ok "$3"; }
has() { grep -q -e "$2" <<<"$1" || die "$3 (no '$2' in: ${1:0:300})"; ok "$3"; }
get() { curl -sk --noproxy '*' --resolve "$1.$D:$PORT:127.0.0.1" "${@:3}" "https://$1.$D:$PORT$2"; }
code() { curl -sk --noproxy '*' -o /dev/null -w '%{http_code}' --resolve "$1.$D:$PORT:127.0.0.1" "${@:3}" "https://$1.$D:$PORT$2"; }
rls() { docker run --rm -e RESTIC_PASSWORD_FILE=/p -v "$ATCLOUD_ROOT/restic-password:/p:ro" -v "$ATCLOUD_ROOT/restic-repo:/r" \
  -e RESTIC_REPOSITORY=/r restic/restic:0.17.3 ls latest --tag "venue:$1"; }
cleanup() {
  for v in alpha beta; do docker rm -f "atsuit-$v" >/dev/null 2>&1 || true; docker network rm "atsuit-$v-net" >/dev/null 2>&1 || true
    docker volume rm "atsuit-$v-data" >/dev/null 2>&1 || true; done
  docker rm -f atcloud-edge >/dev/null 2>&1 || true
  docker volume rm atcloud-edge-data atcloud-restic-cache >/dev/null 2>&1 || true
  docker image rm "$REPO_IMG:e2e-next" "$REPO_IMG:e2e-broken" >/dev/null 2>&1 || true
  [ -n "${KEEP:-}" ] || rm -rf "$ATCLOUD_ROOT"
}
trap cleanup EXIT
cleanup
mkdir -p "$ATCLOUD_ROOT"

echo "1. Set up the cloud server"
"$AT" init --domain "$D" --image "$REPO_IMG" --version "$TAG" --https-port "$PORT" >/dev/null
echo "RESTIC_REPOSITORY=$ATCLOUD_ROOT/restic-repo" >> "$ATCLOUD_ROOT/atcloud.env"
is "$(docker inspect -f '{{.State.Status}}' atcloud-edge)" running "front door running"
is "$(code nobody /)" 404 "an unknown venue address is a 404"
sed -i 's/^ATCLOUD_TRUSTED_PROXIES=.*/ATCLOUD_TRUSTED_PROXIES=173.245.48.0\/20/' "$ATCLOUD_ROOT/atcloud.env"
"$AT" edge >/dev/null; sleep 1
is "$(code nobody / || true)" 000 "only the trusted proxy (Cloudflare) may connect"
sed -i 's/^ATCLOUD_TRUSTED_PROXIES=.*/ATCLOUD_TRUSTED_PROXIES=private_ranges/' "$ATCLOUD_ROOT/atcloud.env"
"$AT" edge >/dev/null; sleep 1

echo "2. Two venues"
M="$ATCLOUD_ROOT/models/sherpa-onnx-streaming-zipformer-en-2023-06-21"
mkdir -p "$M" && echo "<blk> 0" > "$M/tokens.txt"  # a stand-in for the shared speech model
"$AT" new alpha --name "Alpha Arena" --no-pull > "$ATCLOUD_ROOT/alpha.txt"
"$AT" new beta --name "Beta Hall" --no-pull > "$ATCLOUD_ROOT/beta.txt"
LINK_A=$(grep -o 'https://[^ ]*setup?code=[A-Za-z0-9_-]*' "$ATCLOUD_ROOT/alpha.txt")
CODE_A=${LINK_A##*code=}
CODE_B=$(grep -o 'setup?code=[A-Za-z0-9_-]*' "$ATCLOUD_ROOT/beta.txt"); CODE_B=${CODE_B##*code=}
is "$LINK_A" "https://alpha.$D:$PORT/setup?code=$CODE_A" "setup link printed"
has "$(get alpha /api/health)" '"setup_complete":false' "alpha answers at its address"
has "$(get beta /api/health)" '"ok":true' "beta answers at its address"
is "$("$AT" link alpha)" "$LINK_A" "atcloud link prints the same link"
has "$("$AT" list)" '^alpha  *Alpha Arena  *running' "atcloud list shows alpha running"
docker exec atsuit-alpha sh -c 'test -f /data/models/sherpa-onnx-streaming-zipformer-en-2023-06-21/tokens.txt && mkdir -p /data/models/laptop' \
  || die "the shared speech model isn't there, or the venue can't write next to it"
ok "venues share one read-only speech model and can still write their own laptop files"

echo "3. Setting up needs the venue's own setup link"
SETUP='{"organisation":"Alpha","site_name":"Main","admin_username":"admin","admin_password":"correct-horse","rooms":["CC","HD"]}'
J=(-H 'Content-Type: application/json')
is "$(code alpha /api/setup "${J[@]}" -d "$SETUP")" 403 "no code: refused"
is "$(code alpha /api/setup "${J[@]}" -H "X-Setup-Code: $CODE_B" -d "$SETUP")" 403 "beta's code doesn't set up alpha"
is "$(code alpha /api/setup "${J[@]}" -H "X-Setup-Code: $CODE_A" -d "$SETUP")" 200 "alpha's code sets up alpha"
is "$(code alpha /api/setup "${J[@]}" -H "X-Setup-Code: $CODE_A" -d "$SETUP")" 409 "and works only once"
has "$(get beta /api/health)" '"setup_complete":false' "beta is still not set up"
has "$("$AT" link alpha)" 'set up already' "atcloud link says alpha is set up"

echo "4. Venues are kept apart"
TOKEN=$(get alpha /api/auth/login "${J[@]}" -d '{"username":"admin","password":"correct-horse"}' | python3 -c 'import json,sys;print(json.load(sys.stdin)["token"])')
is "$(code alpha /api/bootstrap -H "Authorization: Bearer $TOKEN")" 200 "alpha's admin signs in to alpha"
is "$(code beta /api/bootstrap -H "Authorization: Bearer $TOKEN")" 401 "alpha's sign-in means nothing to beta"
[ "$(code beta /api/auth/login "${J[@]}" -d '{"username":"admin","password":"correct-horse"}')" != 200 ] || die "alpha's password opened beta"
ok "alpha's password doesn't open beta"
if docker exec atsuit-alpha python -c "import urllib.request;urllib.request.urlopen('http://atsuit-beta:8080/api/health',timeout=3)" 2>/dev/null; then
  die "alpha's container can reach beta's"; fi
ok "alpha's container can't reach beta's"
is "$(docker inspect -f '{{.HostConfig.ReadonlyRootfs}} {{.Config.User}}' atsuit-alpha)" "true atsuit" "read-only, not root"
has "$(get alpha /api/guide/help)" '"slug":"at-suit"' "the tech guide is there"
has "$(docker inspect -f '{{json .Config.Env}}' atsuit-alpha)" '"ATSUIT_ASR=0".*"ATSUIT_LAPTOP_MODELS=1"' "no captions on the server; laptops get the speech model"
is "$(docker exec atsuit-alpha python -c "import sqlite3;print(sqlite3.connect('/data/atsuit.db').execute(\"SELECT value FROM settings WHERE key='captions_private'\").fetchone()[0])")" true "captions stay on the laptops"

echo "5. Pause and resume"
"$AT" suspend beta >/dev/null
is "$(code beta /)" 503 "a paused venue says so"
is "$(code alpha /api/health)" 200 "alpha unaffected"
"$AT" resume beta >/dev/null
is "$(code beta /api/health)" 200 "beta is back"

echo "6. Backups"
"$AT" backup >/dev/null
has "$("$AT" snapshots alpha)" 'venue:alpha' "alpha backed up"
"$AT" suspend beta >/dev/null; "$AT" backup beta >/dev/null; "$AT" resume beta >/dev/null
has "$(rls beta)" '/.snapshot/atsuit.db' "a paused venue is backed up too"
docker exec atsuit-alpha sh -c 'echo secret > /data/transcripts/t.txt'
"$AT" backup alpha >/dev/null
L=$(rls alpha)
if grep -q -e '/transcripts' -e '/alpha/atsuit.db' <<<"$L"; then die "the backup has transcripts or the live database in it: $L"; fi
ok "transcripts and the live database files are not in backups"
has "$L" '/.snapshot/atsuit.db' "the database snapshot is"
# change alpha after the backup: a new admin account
get alpha /api/admin/accounts -H "Authorization: Bearer $TOKEN" "${J[@]}" -d '{"username":"late","password":"late-password-1","display_name":"Late","role":"admin"}' >/dev/null || true
docker exec atsuit-alpha python -c "import sqlite3;c=sqlite3.connect('/data/atsuit.db');c.execute(\"INSERT INTO settings(key,value) VALUES('e2e_after_backup','1')\");c.commit()"
"$AT" restore alpha >/dev/null 2>&1 && die "restore went ahead without --yes"
"$AT" restore alpha --yes >/dev/null
is "$(docker exec atsuit-alpha python -c "import sqlite3;print(sqlite3.connect('/data/atsuit.db').execute(\"SELECT COUNT(*) FROM settings WHERE key='e2e_after_backup'\").fetchone()[0])")" 0 "restore puts the data back as it was"
is "$(code alpha /api/bootstrap -H "Authorization: Bearer $TOKEN")" 200 "alpha's admin still signs in after the restore"

echo "7. Updates"
docker tag "$IMG" "$REPO_IMG:e2e-next"
"$AT" update --all --version e2e-next --no-pull >/dev/null
is "$(docker inspect -f '{{.Config.Image}}' atsuit-alpha)" "$REPO_IMG:e2e-next" "alpha updated"
is "$(code alpha /api/bootstrap -H "Authorization: Bearer $TOKEN")" 200 "and keeps its data"
docker build -q -t "$REPO_IMG:e2e-broken" - >/dev/null <<EOF
FROM $IMG
CMD ["sh", "-c", "exit 3"]
EOF
"$AT" update alpha --version e2e-broken --no-pull >/dev/null 2>&1 && die "a broken version was kept"
is "$(docker inspect -f '{{.Config.Image}}' atsuit-alpha)" "$REPO_IMG:e2e-next" "a broken version is rolled back"
is "$(code alpha /api/bootstrap -H "Authorization: Bearer $TOKEN")" 200 "with alpha's data"
"$AT" check >/dev/null || die "atcloud check"
ok "atcloud check: all healthy"

echo "8. The Windows tech app"
mkdir -p "$ATCLOUD_ROOT/rel"
echo "MZ" > "$ATCLOUD_ROOT/rel/AT-SUIT-Node-Setup-9.9.9.exe"; echo "map" > "$ATCLOUD_ROOT/rel/AT-SUIT-Node-Setup-9.9.9.exe.blockmap"
printf 'version: 9.9.9\npath: AT-SUIT-Node-Setup-9.9.9.exe\n' > "$ATCLOUD_ROOT/rel/latest.yml"
has "$("$AT" app --dir "$ATCLOUD_ROOT/rel")" 'alpha: published' "atcloud app publishes it to every venue"
has "$(docker exec atsuit-alpha ls /data/app)" 'AT-SUIT-Node-Setup-9.9.9.exe' "alpha's laptops can update from it"

echo "9. Remove"
"$AT" remove beta >/dev/null 2>&1 && die "remove went ahead without --yes"
"$AT" remove beta --yes >/dev/null
is "$(code beta /)" 404 "beta's address is gone"
docker volume inspect atsuit-beta-data >/dev/null 2>&1 && die "beta's data is still there"
ok "beta's data is deleted"
has "$("$AT" snapshots beta)" 'venue:beta' "its last backup is kept"
is "$(code alpha /api/health)" 200 "alpha unaffected"

echo "All $pass checks passed."
