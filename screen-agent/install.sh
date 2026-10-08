#!/usr/bin/env bash
# AT-SUIT screen installer for a Linux laptop or all-in-one.
#
# Run it once, as the user who is logged in to the desktop (not root):
#
#   curl -fsSL http://SERVER:8180/screen-agent/install.sh | bash -s -- \
#        --server http://SERVER:8180 [--name HD-STAGE-1] [--allow-power] [--code ENROLMENT-CODE]
#
# It installs Chromium, xrandr and Python, downloads the screen agent and
# starts it every time the desktop logs in. Without --code the screen shows a
# six-digit pairing code: a tech types it into "Add a screen" on their laptop
# and picks what it shows. With --code (Admin -> Node setup) it is added
# straight away instead.
# --allow-power lets the dashboard reboot or shut this screen down (it adds a
# sudoers rule for exactly those two commands).
set -euo pipefail

SERVER="" CODE="" NAME="" ALLOW_POWER=0
while [ $# -gt 0 ]; do
  case "$1" in
    --server) SERVER="${2%/}"; shift 2 ;;
    --code) CODE="$2"; shift 2 ;;
    --name) NAME="$2"; shift 2 ;;
    --allow-power) ALLOW_POWER=1; shift ;;
    -h|--help) sed -n '2,16p' "$0" 2>/dev/null || true; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
[ -n "$SERVER" ] || { echo "Usage: install.sh --server http://SERVER:8180 [--name NAME] [--allow-power] [--code ENROLMENT-CODE]" >&2; exit 2; }
[ "$(id -u)" -ne 0 ] || { echo "Run this as the desktop user, not root (it uses sudo where it needs to)." >&2; exit 2; }

say() { printf '\n==> %s\n' "$*"; }

say "Installing Chromium, xrandr and Python"
if command -v apt-get >/dev/null; then
  sudo apt-get update -q
  sudo apt-get install -y -q python3 x11-xserver-utils curl
  if ! command -v chromium >/dev/null && ! command -v chromium-browser >/dev/null && ! command -v google-chrome >/dev/null; then
    sudo apt-get install -y -q chromium || sudo apt-get install -y -q chromium-browser
  fi
else
  echo "No apt-get here: install python3, xrandr and chromium yourself, then run this again." >&2
  command -v python3 >/dev/null && command -v xrandr >/dev/null || exit 1
fi

DIR="$HOME/.local/share/atsuit-screen"
AGENT="$DIR/atsuit_screen.py"
say "Downloading the screen agent from $SERVER"
mkdir -p "$DIR"
curl -fsSL "$SERVER/screen-agent/atsuit_screen.py" -o "$AGENT.new"
python3 -m py_compile "$AGENT.new"
mv "$AGENT.new" "$AGENT"
chmod 755 "$AGENT"

if [ -n "$CODE" ]; then
  say "Adding this screen to AT-SUIT"
  ENROL=(python3 "$AGENT" --server "$SERVER" --code "$CODE" --re-enrol --save-only)
  [ -n "$NAME" ] && ENROL+=(--name "$NAME")
  "${ENROL[@]}"
else
  say "This screen will show a pairing code when the agent starts"
  python3 "$AGENT" --server "$SERVER" --save-only
fi

FLAGS=""
if [ "$ALLOW_POWER" = 1 ]; then
  say "Letting the dashboard reboot and shut down this screen"
  SYSTEMCTL="$(command -v systemctl)"
  RULE="/etc/sudoers.d/atsuit-screen"
  TMP="$(mktemp)"
  printf '%s ALL=(root) NOPASSWD: %s reboot, %s poweroff\n' "$USER" "$SYSTEMCTL" "$SYSTEMCTL" > "$TMP"
  sudo visudo -cf "$TMP" >/dev/null
  sudo install -m 0440 -o root -g root "$TMP" "$RULE"
  rm -f "$TMP"
  FLAGS=" --allow-power"
fi

NAMEFLAG=""
[ -n "$NAME" ] && NAMEFLAG=" --name $(printf '%q' "$NAME")"
say "Starting the agent when the desktop logs in"
mkdir -p "$HOME/.config/autostart"
cat > "$HOME/.config/autostart/atsuit-screen.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=AT-SUIT screen
Comment=Shows this screen's AT-SUIT timer or view full screen
Exec=sh -c 'exec python3 "$AGENT"$FLAGS$NAMEFLAG >> "$DIR/agent.log" 2>&1'
X-GNOME-Autostart-enabled=true
NoDisplay=true
EOF

if [ "${XDG_SESSION_TYPE:-}" = "wayland" ]; then
  cat <<'EOF'

NOTE: this desktop is running Wayland. The HDMI rule needs an X11 session:
  * Ubuntu/GDM: set WaylandEnable=false in /etc/gdm3/custom.conf and reboot.
  * Or pick "Ubuntu on Xorg" on the login screen.
EOF
fi

cat <<EOF

Done. Unless you used --code, the screen shows a pairing code: type it into
"Add a screen" on a tech laptop (next to the timer) and pick what it shows.
Turn on automatic login for $USER so the screen comes back by itself after a power cut.
The agent starts at the next login; to start it now:  python3 "$AGENT"$FLAGS$NAMEFLAG &
Log: $DIR/agent.log
EOF
