#!/usr/bin/env python3
"""AT-SUIT screen agent for Linux laptops and all-in-ones.

Turns the machine into a display-only screen: it opens AT-SUIT's /screen page
full screen in Chromium and keeps it there. The page shows one room's timer,
captions, a custom view or a web page, picked on the screen itself or routed
from the dashboard (Timers -> Screens).

  * HDMI rule: when an external display (HDMI, DisplayPort, DVI, VGA) is
    plugged in, the picture goes only to that display and the built-in panel
    is switched off. Unplug it and the built-in panel comes back.
  * Reports its name, IP, MAC, displays and agent version to the dashboard.
  * Restarts the browser if it crashes; reboot/shutdown/update from the
    dashboard (power needs --allow-power and a sudoers rule, see install.sh).
  * Updates itself from the AT-SUIT server.

Python 3 standard library only. Needs an X11 desktop session, xrandr and
Chromium (install.sh sets all of this up).

    python3 atsuit_screen.py --server http://10.100.70.101:8180 --code ABCD-1234-EF56
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

VERSION = "0.2.0"
HOME = Path(os.environ.get("ATSUIT_SCREEN_HOME", Path.home() / ".config" / "atsuit-screen"))
CONFIG = HOME / "config.json"
INTERNAL = re.compile(r"^(eDP|LVDS|DSI)", re.I)
EXTERNAL = re.compile(r"^(HDMI|DP|DisplayPort|DVI|VGA)", re.I)
PAGE_KINDS = "set_url,reload,identify,message"  # the /screen page handles these
AGENT_KINDS = "reboot,shutdown,update,restart_browser"


# ------------------------------------------------------------------ state --
def load_config() -> dict:
    try:
        return json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        return {}


def save_config(conf: dict) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(conf, indent=2))
    os.chmod(tmp, 0o600)
    os.replace(tmp, CONFIG)


def http(server: str, method: str, path: str, body=None, token: str | None = None, timeout: float = 10):
    req = urllib.request.Request(server.rstrip("/") + path, method=method)
    if token:
        req.add_header("Authorization", f"Node {token}")
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data, timeout=timeout) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        try:
            detail = json.loads(detail).get("detail", detail)
        except ValueError:
            pass
        raise RuntimeError(f"{e.code}: {detail}") from None


def local_ip(server: str) -> str:
    host = server.split("//", 1)[-1].split("/", 1)[0].split(":")[0]
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((host, 9))
            return s.getsockname()[0]
    except OSError:
        return ""


def mac() -> str:
    n = uuid.getnode()
    return ":".join(f"{(n >> s) & 0xFF:02x}" for s in range(40, -1, -8))


# --------------------------------------------------------------- displays --
def parse_xrandr(text: str) -> list[dict]:
    """Outputs from `xrandr --query`: name, connected, active (has a mode set)
    and geometry."""
    outs = []
    for line in text.splitlines():
        m = re.match(r"^(\S+) (connected|disconnected)(?: primary)?(?: (\d+)x(\d+)\+(\d+)\+(\d+))?", line)
        if not m:
            continue
        name, state, w, h, x, y = m.groups()
        outs.append({"name": name, "connected": state == "connected", "active": w is not None,
                     "geometry": [int(w), int(h), int(x), int(y)] if w else None,
                     "internal": bool(INTERNAL.match(name)), "external": bool(EXTERNAL.match(name))})
    return outs


def wanted_outputs(outs: list[dict]) -> list[str]:
    """The rule: an external display that's plugged in gets the picture on its
    own; otherwise the built-in panel does. If neither kind is recognised,
    leave the connected outputs as they are."""
    external = [o["name"] for o in outs if o["connected"] and o["external"]]
    if external:
        return external[:1]
    internal = [o["name"] for o in outs if o["connected"] and o["internal"]]
    if internal:
        return internal[:1]
    return [o["name"] for o in outs if o["connected"]][:1]


def xrandr_args(outs: list[dict], want: list[str]) -> list[str] | None:
    """The xrandr call that makes only `want` active, or None if it already is."""
    active = sorted(o["name"] for o in outs if o["active"])
    if active == sorted(want) or not want:
        return None
    args = ["xrandr"]
    for o in outs:
        if o["name"] in want:
            args += ["--output", o["name"], "--auto", "--primary", "--pos", "0x0"]
        elif o["active"] or not o["connected"]:
            args += ["--output", o["name"], "--off"]
    return args


def read_displays() -> list[dict]:
    try:
        out = subprocess.run(["xrandr", "--query"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    return parse_xrandr(out)


def arrange_displays() -> tuple[list[dict], bool]:
    """Apply the HDMI rule. Returns the outputs and whether anything changed."""
    outs = read_displays()
    args = xrandr_args(outs, wanted_outputs(outs))
    if not args:
        return outs, False
    print("displays:", " ".join(args[1:]), flush=True)
    subprocess.run(args, timeout=20)
    time.sleep(1.5)  # let the desktop settle before the browser is placed
    return read_displays(), True


# ---------------------------------------------------------------- browser --
def find_browser(preferred: str | None) -> str | None:
    for b in ([preferred] if preferred else []) + ["chromium", "chromium-browser", "google-chrome", "google-chrome-stable"]:
        if b and shutil.which(b):
            return shutil.which(b)
    return None


def browser_args(browser: str, url: str, geometry) -> list[str]:
    x, y = (geometry[2], geometry[3]) if geometry else (0, 0)
    return [browser, "--kiosk", "--noerrdialogs", "--disable-infobars", "--no-first-run",
            "--disable-session-crashed-bubble", "--disable-features=Translate,MediaRouter",
            "--check-for-update-interval=31536000", "--autoplay-policy=no-user-gesture-required",
            "--password-store=basic", f"--user-data-dir={HOME / 'browser'}",
            f"--window-position={x},{y}", url]


class Browser:
    def __init__(self, path: str):
        self.path = path
        self.proc: subprocess.Popen | None = None

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, url: str, geometry) -> None:
        self.stop()
        print("browser:", url.split("#")[0], flush=True)
        self.proc = subprocess.Popen(browser_args(self.path, url, geometry), stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, start_new_session=True)

    def stop(self) -> None:
        if self.running():
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
                self.proc.wait(10)
            except (OSError, subprocess.TimeoutExpired):
                self.proc.kill()
        self.proc = None


# --------------------------------------------------------------- commands --
def power(action: str) -> None:
    subprocess.Popen(["sudo", "-n", "systemctl", "reboot" if action == "reboot" else "poweroff"])


def self_update(server: str, token: str, release: dict) -> None:
    """Pull a newer agent from the server, check it, swap it in and restart."""
    req = urllib.request.Request(server.rstrip("/") + "/api/nodes/screen-agent/file")
    req.add_header("Authorization", f"Node {token}")
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
    if hashlib.sha256(data).hexdigest() != release.get("sha256"):
        raise RuntimeError("downloaded agent failed its checksum")
    me = Path(__file__).resolve()
    tmp = me.with_suffix(".new")
    tmp.write_bytes(data)
    compile(data, str(tmp), "exec")
    os.replace(tmp, me)
    print(f"updated agent {VERSION} -> {release['version']}, restarting", flush=True)
    os.execv(sys.executable, [sys.executable, str(me), *sys.argv[1:]])


def newer(a: str, b: str) -> bool:
    def parts(v):
        return [int(x) if x.isdigit() else 0 for x in v.split(".")]
    return parts(a) > parts(b)


# ------------------------------------------------------------------- main --
def enrol(args, conf: dict) -> dict:
    server = args.server.rstrip("/")
    name = args.name or socket.gethostname()
    r = http(server, "POST", "/api/nodes/enrol", {"code": args.code.strip().upper(), "name": name, "kind": "kiosk"})
    conf.update(server=server, token=r["token"], name=r["name"])
    save_config(conf)
    print(f"enrolled as {r['name']}", flush=True)
    return conf


def keep_awake() -> None:
    for cmd in (["xset", "s", "off"], ["xset", "-dpms"], ["xset", "s", "noblank"]):
        try:
            subprocess.run(cmd, timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired):
            pass


def run(args) -> None:
    conf = load_config()
    if args.server and (not conf.get("token") or conf.get("server") != args.server.rstrip("/") or args.re_enrol):
        if not args.code:
            sys.exit("This screen isn't enrolled yet: pass --code with the enrolment code from Admin -> Node setup")
        conf = enrol(args, conf)
    if not conf.get("token"):
        sys.exit("Pass --server and --code the first time")
    server, token = conf["server"], conf["token"]
    browser_path = find_browser(args.browser)
    if not browser_path and not args.no_browser:
        sys.exit("Chromium isn't installed (sudo apt install chromium, or chromium-browser)")
    browser = Browser(browser_path) if browser_path else None
    url = f"{server}/screen#token={token}"
    keep_awake()
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    last_beat = 0.0
    outs: list[dict] = []
    while not stopping:
        outs, changed = arrange_displays() if not args.no_displays else (read_displays(), False)
        geometry = next((o["geometry"] for o in outs if o["active"]), None)
        if browser and (changed or not browser.running()):
            browser.start(url, geometry)
        if time.time() - last_beat >= args.interval:
            last_beat = time.time()
            try:
                r = http(server, "POST", "/api/nodes/heartbeat", token=token, body={
                    "ip": local_ip(server), "mac": mac(), "version": f"screen-agent {VERSION}",
                    "info": {"hostname": socket.gethostname(), "agent": VERSION,
                             "displays": [{"name": o["name"], "connected": o["connected"], "active": o["active"]} for o in outs if o["connected"]],
                             "showing_on": [o["name"] for o in outs if o["active"]], "browser": bool(browser and browser.running())}})
                release = r.get("screen_agent") or {}
                update_due = bool(args.self_update and release.get("version") and newer(release["version"], VERSION))
                for cmd in http(server, "GET", f"/api/nodes/commands?kinds={AGENT_KINDS}", token=token) or []:
                    ok, detail = True, ""
                    if cmd["kind"] in ("reboot", "shutdown"):
                        if args.allow_power:
                            power(cmd["kind"])
                        else:
                            ok, detail = False, "Power commands are off (install with --allow-power)"
                    elif cmd["kind"] == "restart_browser" and browser:
                        browser.start(url, geometry)
                    elif cmd["kind"] == "update":
                        detail = f"Updating to {release['version']}" if update_due else f"Already up to date ({VERSION})"
                    http(server, "POST", f"/api/nodes/commands/{cmd['id']}/ack", {"ok": ok, "detail": detail}, token=token)
                if update_due:  # a newer agent on the server: swap it in now
                    if browser:
                        browser.stop()
                    self_update(server, token, release)
            except RuntimeError as e:
                if str(e).startswith("401"):
                    sys.exit("The server no longer knows this screen. Enrol it again with --code.")
                print("server:", e, flush=True)
            except OSError as e:
                print("server unreachable:", e, flush=True)
        if args.once:
            break
        time.sleep(args.poll)
    if browser and not args.once:
        browser.stop()


def main() -> None:
    ap = argparse.ArgumentParser(description="AT-SUIT screen agent (Linux)")
    ap.add_argument("--server", help="AT-SUIT address, e.g. http://10.100.70.101:8180 (only needed the first time)")
    ap.add_argument("--code", help="enrolment code (first time only)")
    ap.add_argument("--name", help="screen name (default: the computer's hostname)")
    ap.add_argument("--re-enrol", action="store_true")
    ap.add_argument("--browser", help="browser command (default: chromium)")
    ap.add_argument("--allow-power", action="store_true", help="let the dashboard reboot or shut down this screen")
    ap.add_argument("--no-self-update", dest="self_update", action="store_false")
    ap.add_argument("--no-displays", action="store_true", help="don't change display outputs")
    ap.add_argument("--no-browser", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--once", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--poll", type=float, default=3.0, help=argparse.SUPPRESS)
    ap.add_argument("--interval", type=float, default=15.0, help=argparse.SUPPRESS)
    ap.add_argument("--version", action="version", version=VERSION)
    run(ap.parse_args())


if __name__ == "__main__":
    main()
