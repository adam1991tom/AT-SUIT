"""Remote screens: the /screen page and the Linux screen agent."""
import importlib.util
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
AGENT = REPO / "screen-agent" / "atsuit_screen.py"


def load_agent():
    spec = importlib.util.spec_from_file_location("atsuit_screen", AGENT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


LAPTOP_ONLY = """Screen 0: minimum 320 x 200, current 1920 x 1080, maximum 16384 x 16384
eDP-1 connected primary 1920x1080+0+0 (normal left inverted right x axis y axis) 344mm x 194mm
   1920x1080     60.02*+
HDMI-1 disconnected (normal left inverted right x axis y axis)
DP-1 disconnected (normal left inverted right x axis y axis)
"""
HDMI_PLUGGED = """Screen 0: minimum 320 x 200, current 3840 x 1080, maximum 16384 x 16384
eDP-1 connected primary 1920x1080+0+0 (normal left inverted right x axis y axis) 344mm x 194mm
   1920x1080     60.02*+
HDMI-1 connected 1920x1080+1920+0 (normal left inverted right x axis y axis) 520mm x 290mm
   1920x1080     60.00*+
"""
HDMI_ONLY = """Screen 0: minimum 320 x 200, current 1920 x 1080, maximum 16384 x 16384
eDP-1 connected (normal left inverted right x axis y axis) 344mm x 194mm
   1920x1080     60.02 +
HDMI-1 connected primary 1920x1080+0+0 (normal left inverted right x axis y axis) 520mm x 290mm
   1920x1080     60.00*+
"""
HDMI_UNPLUGGED = """Screen 0: minimum 320 x 200, current 1920 x 1080, maximum 16384 x 16384
eDP-1 connected (normal left inverted right x axis y axis) 344mm x 194mm
   1920x1080     60.02 +
HDMI-1 disconnected 1920x1080+0+0 (normal left inverted right x axis y axis) 0mm x 0mm
"""


def test_hdmi_rule():
    a = load_agent()
    # Laptop on its own: nothing to change.
    outs = a.parse_xrandr(LAPTOP_ONLY)
    assert [o["name"] for o in outs] == ["eDP-1", "HDMI-1", "DP-1"]
    assert a.wanted_outputs(outs) == ["eDP-1"] and a.xrandr_args(outs, ["eDP-1"]) is None
    # HDMI plugged in (desktop extended it): picture only on HDMI.
    outs = a.parse_xrandr(HDMI_PLUGGED)
    assert a.wanted_outputs(outs) == ["HDMI-1"]
    args = a.xrandr_args(outs, ["HDMI-1"])
    assert args[:7] == ["xrandr", "--output", "eDP-1", "--off", "--output", "HDMI-1", "--auto"]
    assert "--primary" in args
    # Already HDMI only: leave it.
    outs = a.parse_xrandr(HDMI_ONLY)
    assert a.xrandr_args(outs, a.wanted_outputs(outs)) is None
    # HDMI pulled out: the built-in screen comes back.
    outs = a.parse_xrandr(HDMI_UNPLUGGED)
    assert a.wanted_outputs(outs) == ["eDP-1"]
    args = a.xrandr_args(outs, ["eDP-1"])
    assert ["--output", "eDP-1", "--auto"] == args[1:4] and ["--output", "HDMI-1", "--off"] == args[-3:]
    # An all-in-one with no recognised names: keep whatever is connected.
    outs = a.parse_xrandr("Screen 0:\nXWAYLAND0 connected 1920x1080+0+0 (normal)\n")
    assert a.wanted_outputs(outs) == ["XWAYLAND0"] and a.xrandr_args(outs, ["XWAYLAND0"]) is None
    assert a.newer("0.10.0", "0.9.9") and not a.newer("0.2.0", "0.2.0")


def _kiosk(admin, name="scr1"):
    code = admin.get("/api/fleet/enrolment").json()[0]["enrol_code"]
    n = admin.post("/api/nodes/enrol", json={"code": code, "name": name, "kind": "kiosk"}).json()
    return n, {"Authorization": f"Node {n['token']}"}


def test_screen_pick_and_route(admin):
    n, hdr = _kiosk(admin)
    rooms = admin.get("/api/bootstrap").json()["rooms"]
    me = admin.get("/api/nodes/me", headers=hdr).json()
    assert me["node"]["screen_view"] == "" and {"hcc", "captions"} <= {v["id"] for v in me["views"]}
    # Picked on the screen itself.
    assert admin.put("/api/nodes/me/screen", headers=hdr, json={"room_id": rooms[1]["id"], "view": "backstage"}).status_code == 200
    beat = admin.post("/api/nodes/heartbeat", headers=hdr, json={"info": {"page": "screen-web-0.2.0"}, "current_url": "http://x/timer/2"}).json()
    assert beat["room_id"] == rooms[1]["id"] and beat["screen_view"] == "backstage"
    assert "screen_agent" in beat and beat["screen_agent"]["version"]
    # Routed from the dashboard.
    assert admin.put(f"/api/fleet/nodes/{n['node_id']}/screen", json={"room_id": rooms[0]["id"], "view": "url:https://example.com/x"}).status_code == 200
    beat = admin.post("/api/nodes/heartbeat", headers=hdr, json={}).json()
    assert beat["screen_view"] == "url:https://example.com/x" and beat["room_id"] == rooms[0]["id"]
    for bad in ("javascript:alert(1)", "url:file:///etc/passwd", "view:nope", "<b>"):
        assert admin.put(f"/api/fleet/nodes/{n['node_id']}/screen", json={"room_id": rooms[0]["id"], "view": bad}).status_code == 400
    # The agent and the page report separately without wiping each other.
    admin.post("/api/nodes/heartbeat", headers=hdr, json={"version": "screen-agent 0.2.0", "mac": "aa:bb",
                                                          "info": {"agent": "0.2.0", "showing_on": ["HDMI-1"]}})
    admin.post("/api/nodes/heartbeat", headers=hdr, json={"current_url": "http://x/y", "info": {"page": "screen-web-0.2.0"}})
    node = next(x for x in admin.get("/api/fleet/nodes").json() if x["id"] == n["node_id"])
    assert node["version"] == "screen-agent 0.2.0" and node["mac"] == "aa:bb" and node["current_url"] == "http://x/y"
    assert node["info"]["showing_on"] == ["HDMI-1"] and node["info"]["page"] == "screen-web-0.2.0"
    admin.post("/api/nodes/heartbeat", headers=hdr, json={"info": {}})
    node = next(x for x in admin.get("/api/fleet/nodes").json() if x["id"] == n["node_id"])
    assert node["current_url"] == "http://x/y"


def test_screen_commands_split_by_kind(admin):
    n, hdr = _kiosk(admin)
    for kind in ("identify", "restart_browser", "reload"):
        assert admin.post(f"/api/fleet/nodes/{n['node_id']}/command", json={"kind": kind}).status_code == 200
    page = admin.get("/api/nodes/commands?kinds=set_url,reload,identify,message", headers=hdr).json()
    agent = admin.get("/api/nodes/commands?kinds=reboot,shutdown,update,restart_browser", headers=hdr).json()
    assert sorted(c["kind"] for c in page) == ["identify", "reload"]
    assert [c["kind"] for c in agent] == ["restart_browser"]


def test_screen_agent_downloads(admin, client):
    _, hdr = _kiosk(admin)
    assert client.get("/screen-agent/install.sh").text.startswith("#!/usr/bin/env bash")
    body = client.get("/screen-agent/atsuit_screen.py").content
    assert body == AGENT.read_bytes()
    assert client.get("/screen-agent/../atsuit/config.py").status_code == 404
    assert client.get("/api/nodes/screen-agent/file").status_code in (401, 403)
    assert client.get("/api/nodes/screen-agent/file", headers=hdr).content == body
    assert admin.get("/api/fleet/screen-agent").json()["version"] == load_agent().VERSION


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.skipif(sys.platform == "win32", reason="Linux agent")
def test_screen_agent_end_to_end(tmp_path):
    """The real agent against a real server, with xrandr and Chromium faked:
    it enrols, moves the picture to HDMI, opens /screen and reports in."""
    port, data, home, bin_ = _free_port(), tmp_path / "data", tmp_path / "home", tmp_path / "bin"
    bin_.mkdir()
    log = tmp_path / "calls.log"
    state = tmp_path / "hdmi-only"
    (bin_ / "xrandr").write_text(f"""#!/bin/sh
echo "xrandr $*" >> {log}
if [ "$1" = "--query" ]; then
  if [ -f {state} ]; then cat {tmp_path / 'only.txt'}; else cat {tmp_path / 'plugged.txt'}; fi
else touch {state}; fi
""")
    (bin_ / "chromium").write_text(f'#!/bin/sh\necho "chromium $*" >> {log}\nsleep 30\n')
    (bin_ / "xset").write_text("#!/bin/sh\n")
    for f in bin_.iterdir():
        f.chmod(0o755)
    (tmp_path / "plugged.txt").write_text(HDMI_PLUGGED)
    (tmp_path / "only.txt").write_text(HDMI_ONLY)
    env = {**os.environ, "ATSUIT_DATA": str(data), "ATSUIT_ASR": "0", "ATSUIT_PORT": str(port)}
    srv = subprocess.Popen([sys.executable, "tests/licensed_server.py", "--port", str(port)],
                           cwd=REPO / "server", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(url + "/api/health", timeout=1)
                break
            except OSError:
                time.sleep(0.1)

        def call(method, path, body=None, headers=None):
            req = urllib.request.Request(url + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                         headers={"Content-Type": "application/json", **(headers or {})})
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.loads(r.read() or b"null")

        call("POST", "/api/setup", {"organisation": "T", "site_name": "V", "admin_username": "admin",
                                    "admin_password": "correct-horse", "rooms": ["CC"],
                                    "licence_key": (data / "test-licence.txt").read_text()})
        login = urllib.request.Request(url + "/api/auth/login", method="POST", headers={"Content-Type": "application/json"},
                                       data=json.dumps({"username": "admin", "password": "correct-horse"}).encode())
        with urllib.request.urlopen(login) as r:
            cookie = r.headers["Set-Cookie"].split(";")[0]
        code = call("GET", "/api/fleet/enrolment", headers={"Cookie": cookie})[0]["enrol_code"]

        agent_env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "ATSUIT_SCREEN_HOME": str(home)}
        p = subprocess.Popen([sys.executable, str(AGENT), "--server", url, "--code", code, "--name", "aio-1",
                              "--poll", "0.2", "--interval", "0.5", "--no-self-update"],
                             env=agent_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            node = None
            for _ in range(60):
                nodes = call("GET", "/api/fleet/nodes", headers={"Cookie": cookie})
                node = nodes[0] if nodes else None
                if node and node["info"].get("showing_on") == ["HDMI-1"] and "chromium" in log.read_text():
                    break
                time.sleep(0.2)
            assert node and node["name"] == "AIO-1"
            assert node["kind"] == "kiosk" and node["version"] == f"screen-agent {load_agent().VERSION}"
            assert node["info"]["showing_on"] == ["HDMI-1"], node["info"]
            calls = log.read_text()
            assert "--output eDP-1 --off --output HDMI-1 --auto --primary --pos 0x0" in calls
            assert "--kiosk" in calls and f"{url}/screen#token=" in calls
            assert json.loads((home / "config.json").read_text())["token"]
            # The dashboard can restart the browser.
            call("POST", f"/api/fleet/nodes/{node['id']}/command", {"kind": "restart_browser"}, headers={"Cookie": cookie})
            for _ in range(50):
                if log.read_text().count("chromium ") >= 2:
                    break
                time.sleep(0.2)
            assert log.read_text().count("chromium ") >= 2
        finally:
            p.terminate()
            p.wait(15)
    finally:
        srv.terminate()
        srv.wait(15)


def test_screen_test_patterns(admin, client):
    # The browser screen test is a public page (like the stage timers) and a screen can be put on any pattern.
    page = client.get("/screentest")
    assert page.status_code == 200 and "ledmap" in page.text
    plain = {v["id"] for v in admin.get("/api/timers-views").json()}
    withtests = {v["id"] for v in admin.get("/api/timers-views?screens=1").json()}
    assert not any(i.startswith("screentest:") for i in plain)          # the Views page stays about timers
    assert {"screentest:colorbars", "screentest:ledmap", "screentest:checker"} <= withtests
    code = admin.get("/api/fleet/enrolment").json()[0]["enrol_code"]
    n = admin.post("/api/nodes/enrol", json={"code": code, "name": "tv", "kind": "kiosk"}).json()
    put = lambda v: admin.put(f"/api/fleet/nodes/{n['node_id']}/screen", json={"view": v})
    assert put("screentest:ledmap").status_code == 200
    assert put("screentest:nope").status_code == 400
    # every pattern the page draws is one the server accepts, and the other way round
    import re
    from atsuit.modules.timers import SCREENTEST
    drawn = set(re.findall(r"^    (\w+): \{ name:", page.text, re.M)) | {"white", "red", "green", "blue", "gray"}
    assert drawn == set(SCREENTEST)


def test_companion_guide_is_served(client):
    page = client.get("/guide/companion")
    assert page.status_code == 200
    text = client.get("/api/guide/companion")
    assert text.status_code == 200 and "API" in text.text


def test_companion_module_calls_real_routes():
    """Every request the Companion module can build must match a route on the server."""
    import json, re, shutil, subprocess
    from pathlib import Path
    from atsuit.main import app
    node = shutil.which("node")
    lib = Path(__file__).resolve().parents[2] / "companion-module" / "lib.js"
    if not node or not lib.is_file():
        import pytest
        pytest.skip("node or the module isn't here")
    cases = [["preset", {"minutes": 5}], ["add", {"minutes": 1}], ["control", {"cmd": "go"}], ["blink", {"mode": "on"}],
             ["clock", {"mode": "toggle"}], ["blackout", {"mode": "off"}], ["message", {"text": "x"}], ["message_hide", {}],
             ["overlay", {"node": 7, "on": "on"}], ["caption_test", {"text": "x"}], ["caption_clear", {}],
             ["quick_message", {"text": "x"}], ["secondary_text", {"text": "x"}], ["secondary_timer", {"minutes": 5}],
             ["secondary_control", {"cmd": "hide"}], ["secondary_add", {"minutes": 1}]]
    js = f"const {{request}}=require({json.dumps(str(lib))});console.log(JSON.stringify({json.dumps(cases)}.map(([a,o])=>[a,request(a,o,3)])))"
    out = json.loads(subprocess.check_output([node, "-e", js]))
    routes = [(r.path, m) for r in app.routes if hasattr(r, "methods") for m in r.methods]
    for action, (path, _body, *meth) in out:
        method = meth[0] if meth else "POST"
        assert any(m == method and re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", p), path) for p, m in routes), (action, path)
