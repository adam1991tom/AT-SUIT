"""Pairing codes: a new screen shows a code, a tech types it and picks the layout."""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

import pytest

from test_screens import AGENT, HDMI_ONLY, _free_port, _kiosk, load_agent


def _rooms(admin):
    return {r["name"]: r["id"] for r in admin.get("/api/bootstrap").json()["rooms"]}


def _tech(admin, client, name="ben"):
    admin.post("/api/admin/accounts", json={"username": name, "password": "ben-password-1", "display_name": name.title(), "role": "tech"})
    from fastapi.testclient import TestClient
    t = TestClient(client.app)
    assert t.post("/api/auth/login", json={"username": name, "password": "ben-password-1"}).status_code == 200
    return t


def test_screen_pairs_with_a_code(admin, client):
    rooms = _rooms(admin)
    tech = _tech(admin, client)
    anon = client.__class__(client.app)
    r = anon.post("/api/screens/pair/request", json={"name": "hd-stage-1", "info": {"screen": "1920x1080"}}).json()
    assert re.fullmatch(r"[1-9]\d{5}", r["code"]) and r["display"] == f"{r['code'][:3]} {r['code'][3:]}"
    st = anon.get(f"/api/screens/pair/status?secret={r['secret']}").json()
    assert st == {"state": "waiting", "code": r["code"], "display": r["display"], "expires_in": st["expires_in"]}
    assert anon.get("/api/screens/pair/status?secret=nope").status_code == 404
    # Only techs can pair; nobody can pair a code that isn't showing.
    assert anon.post("/api/screens/pair", json={"code": r["code"], "room_id": rooms["HD"], "view": "stage"}).status_code == 401
    assert tech.post("/api/screens/pair", json={"code": "999999", "room_id": rooms["HD"], "view": "stage"}).status_code == 404
    assert tech.post("/api/screens/pair", json={"code": r["code"], "room_id": rooms["HD"], "view": "<b>"}).status_code == 400
    # A bad layout doesn't burn the code.
    done = tech.post("/api/screens/pair", json={"code": r["display"], "room_id": rooms["HD"], "view": "captions:bar"})
    assert done.status_code == 200, done.text
    out = done.json()
    assert out["name"] == "HD-STAGE-1" and out["room_id"] == rooms["HD"] and out["view"] == "captions:bar"
    # The code is used up; the screen collects its token with its secret.
    assert tech.post("/api/screens/pair", json={"code": r["code"], "room_id": rooms["HD"], "view": "stage"}).status_code == 404
    st = anon.get(f"/api/screens/pair/status?secret={r['secret']}").json()
    assert st["state"] == "paired" and st["token"].startswith("atn_") and st["view"] == "captions:bar"
    hdr = {"Authorization": f"Node {st['token']}"}
    me = anon.get("/api/nodes/me", headers=hdr).json()
    assert me["node"]["kind"] == "kiosk" and me["node"]["room_id"] == rooms["HD"] and me["node"]["screen_view"] == "captions:bar"
    assert {"captions", "captions:overlay", "captions:bar"} <= {v["id"] for v in me["views"]}
    beat = anon.post("/api/nodes/heartbeat", headers=hdr, json={}).json()
    assert beat["room_id"] == rooms["HD"] and beat["screen_view"] == "captions:bar"
    # A kiosk can't pair other screens (its token is a node, not a tech).
    r2 = anon.post("/api/screens/pair/request", json={}).json()
    assert anon.post("/api/screens/pair", headers=hdr, json={"code": r2["code"], "room_id": rooms["HD"], "view": "stage"}).status_code == 403
    # Without a name it's SCREEN-<code>; the tech can name it.
    out2 = tech.post("/api/screens/pair", json={"code": r2["code"], "room_id": rooms["CC"], "view": "url:https://example.com/a"}).json()
    assert out2["name"] == f"SCREEN-{r2['code']}"
    r3 = anon.post("/api/screens/pair/request", json={}).json()
    assert tech.post("/api/screens/pair", json={"code": r3["code"], "room_id": rooms["CC"], "view": "screentest:ledmap", "name": "cc left"}).json()["name"] == "CC-LEFT"


def test_pairing_again_replaces_the_token_and_keeps_tech_names(admin, client):
    rooms = _rooms(admin)
    n, old_hdr = _kiosk(admin, "hd-stage-1")
    r = client.post("/api/screens/pair/request", json={"name": "hd-stage-1"}).json()
    out = admin.post("/api/screens/pair", json={"code": r["code"], "room_id": rooms["HD"], "view": "stage"}).json()
    assert out["node_id"] == n["node_id"]  # the same screen, re-imaged
    assert client.get("/api/nodes/me", headers=old_hdr).status_code == 401
    # A screen can't take over a tech laptop's name.
    code = admin.get("/api/fleet/enrolment").json()[0]["enrol_code"]
    admin.post("/api/nodes/enrol", json={"code": code, "name": "laptop-1", "kind": "tech"})
    r = client.post("/api/screens/pair/request", json={"name": "laptop-1"}).json()
    assert admin.post("/api/screens/pair", json={"code": r["code"], "room_id": rooms["HD"], "view": "stage"}).json()["name"] == "LAPTOP-1-2"


def test_pairing_codes_expire_and_are_capped(admin, client, monkeypatch):
    from atsuit.modules import pairing
    p = pairing.new_pending("1.2.3.4", now=1000.0)
    assert pairing.lookup_code(p.code, now=1000.0 + pairing.CODE_TTL - 1) is p
    assert pairing.lookup_code(p.code, now=1000.0 + pairing.CODE_TTL + 1) is None
    assert pairing.lookup_secret(p.secret, now=1000.0 + pairing.CODE_TTL + 1) is None
    pairing.reset()
    codes = [pairing.new_pending("5.6.7.8", now=2000.0 + i) for i in range(pairing.MAX_PER_IP + 3)]
    live = [c for c in codes if pairing.lookup_secret(c.secret, now=2001.0)]
    assert len(live) == pairing.MAX_PER_IP and codes[-1] in live and codes[0] not in live
    assert len({c.code for c in codes}) == len(codes)
    assert pairing.normalise_code(" 482-913 ") == "482913"


def test_layouts_for_the_picker(admin):
    g = admin.get("/api/screens/layouts").json()["groups"]
    names = [x["name"] for x in g]
    assert names == ["Timer", "Captions", "Test patterns"]
    ids = {v["id"] for x in g for v in x["views"]}
    assert {"hcc", "captions", "captions:overlay", "captions:bar", "screentest:ledmap"} <= ids


def test_tech_laptop_can_pair(admin, client):
    rooms = _rooms(admin)
    code = admin.get("/api/fleet/enrolment").json()[0]["enrol_code"]
    t = client.post("/api/nodes/enrol", json={"code": code, "name": "tech-1", "kind": "tech"}).json()
    hdr = {"Authorization": f"Node {t['token']}"}
    r = client.post("/api/screens/pair/request", json={}).json()
    assert client.post("/api/screens/pair", headers=hdr, json={"code": r["code"], "room_id": rooms["RH"], "view": "captions"}).status_code == 200


@pytest.mark.skipif(sys.platform == "win32", reason="Linux agent")
def test_screen_agent_pairs_without_an_enrolment_code(tmp_path):
    """The real agent with no --code shows a pairing code; once a tech types
    it in, the agent keeps the token and shows the picked layout."""
    port, data, home, bin_ = _free_port(), tmp_path / "data", tmp_path / "home", tmp_path / "bin"
    bin_.mkdir()
    log = tmp_path / "calls.log"
    (bin_ / "xrandr").write_text(f"#!/bin/sh\nif [ \"$1\" = \"--query\" ]; then cat {tmp_path / 'only.txt'}; fi\n")
    (bin_ / "chromium").write_text(f'#!/bin/sh\necho "chromium $*" >> {log}\nsleep 30\n')
    (bin_ / "xset").write_text("#!/bin/sh\n")
    for f in bin_.iterdir():
        f.chmod(0o755)
    (tmp_path / "only.txt").write_text(HDMI_ONLY)
    env = {**os.environ, "ATSUIT_DATA": str(data), "ATSUIT_ASR": "0"}
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "atsuit.main:app", "--port", str(port)],
                           cwd=AGENT.parents[1] / "server", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
                                    "admin_password": "correct-horse", "rooms": ["CC"]})
        login = urllib.request.Request(url + "/api/auth/login", method="POST", headers={"Content-Type": "application/json"},
                                       data=json.dumps({"username": "admin", "password": "correct-horse"}).encode())
        with urllib.request.urlopen(login) as r:
            cookie = r.headers["Set-Cookie"].split(";")[0]
        rid = call("GET", "/api/bootstrap", headers={"Cookie": cookie})["rooms"][0]["id"]
        agent_env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "ATSUIT_SCREEN_HOME": str(home)}
        p = subprocess.Popen([sys.executable, str(AGENT), "--server", url, "--name", "aio-2",
                              "--poll", "0.2", "--interval", "0.5", "--no-self-update"],
                             env=agent_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            secret = None
            for _ in range(60):
                m = re.search(r"/screen#pair=(\S+)", log.read_text() if log.exists() else "")
                if m:
                    secret = m.group(1)
                    break
                time.sleep(0.2)
            assert secret, "the agent should open the pairing page"
            code = call("GET", f"/api/screens/pair/status?secret={secret}")["code"]
            out = call("POST", "/api/screens/pair", {"code": code, "room_id": rid, "view": "captions:overlay"}, headers={"Cookie": cookie})
            assert out["name"] == "AIO-2"
            node = None
            for _ in range(60):
                nodes = call("GET", "/api/fleet/nodes", headers={"Cookie": cookie})
                node = nodes[0] if nodes else None
                if node and node["version"].startswith("screen-agent") and "#token=" in log.read_text():
                    break
                time.sleep(0.2)
            assert node and node["screen_view"] == "captions:overlay" and node["room_id"] == rid
            assert node["version"] == f"screen-agent {load_agent().VERSION}"
            assert f"{url}/screen#token=" in log.read_text()
            assert json.loads((home / "config.json").read_text())["token"].startswith("atn_")
            # Removed from the dashboard: the agent goes back to showing a code.
            call("DELETE", f"/api/fleet/nodes/{node['id']}", headers={"Cookie": cookie})
            for _ in range(60):
                if log.read_text().count("#pair=") >= 2:
                    break
                time.sleep(0.2)
            assert log.read_text().count("#pair=") >= 2
            assert "token" not in json.loads((home / "config.json").read_text())
        finally:
            p.terminate()
            p.wait(15)
    finally:
        srv.terminate()
        srv.wait(15)
