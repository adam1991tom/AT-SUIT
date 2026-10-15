"""No password, token, key or code ever travels in a web address: not from a
form, not as a way to sign in, not for a WebSocket, and never into a log."""
import logging
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "server" / "atsuit" / "static"


def _laptop(admin, name="atlap1", kind="tech"):
    code = admin.get("/api/fleet/enrolment").json()[0]["enrol_code"]
    tok = admin.post("/api/nodes/enrol", json={"code": code, "name": name, "kind": kind}).json()["token"]
    return tok, {"Authorization": f"Node {tok}"}


# ------------------------------------------------------------- the pages --
def test_every_form_posts_and_scripts_never_let_the_browser_send_one():
    """A form with no method that the browser sends by itself is a GET: what was
    typed, password included, lands in the address bar, history and logs."""
    pages = list(STATIC.glob("*.html")) + [ROOT / "node-app" / "src" / "setup.html"]
    forms = 0
    for page in pages:
        for tag in re.findall(r"<form\b[^>]*>", page.read_text("utf-8")):
            forms += 1
            assert re.search(r'\bmethod="post"', tag), f"{page.name}: {tag}"
    assert forms >= 7
    # And the shared scripts stop any form from being sent by the browser at all.
    for js in ("common.js", "theme.js"):
        src = (STATIC / js).read_text("utf-8")
        assert 'addEventListener("submit", (e) => e.preventDefault(), true)' in src, js
        assert "history.replaceState" in src and "password" in src, js


def test_no_page_puts_a_secret_in_an_address():
    bad = re.compile(r"[?&](node_token|token|password|secret|key|code|ticket)=\$\{", re.I)
    files = [*STATIC.glob("*.html"), *STATIC.glob("*.js"), *(ROOT / "node-app" / "src").glob("*.js"),
             *(ROOT / "node-app" / "src").glob("*.html")]
    for f in files:
        if f.name == "desk.js" and f.parent.name == "ui":
            continue
        text = f.read_text("utf-8")
        assert not bad.search(text), f"{f.name}: {bad.search(text).group(0)}"
        assert "node_token:" not in text and "node_token=" not in text, f.name
    for agent in ("node-agent/atsuit_node.py", "screen-agent/atsuit_screen.py", "room-sync/atsuit_room_sync.py"):
        text = (ROOT / agent).read_text("utf-8")
        assert "node_token=" not in text and "/sync/{urllib" not in text, agent


def test_a_page_opened_with_a_password_in_its_address_is_sent_on_without_it(admin):
    anon = TestClient(admin.app, follow_redirects=False)
    r = anon.get("/node?username=amy&password=hunter22&mode=main")
    assert r.status_code == 303 and r.headers["location"] == "/node?mode=main"
    assert "hunter22" not in r.headers["location"] and r.headers["cache-control"] == "no-store"
    assert anon.get("/?password=x").headers["location"] == "/"
    assert anon.get("/screen?token=abc&paired=1").headers["location"] == "/screen?paired=1"
    # Nothing else is touched.
    assert anon.get("/timer/1?view=stage").status_code == 200


def test_the_api_refuses_secrets_in_the_address(admin, client):
    tok, _ = _laptop(admin)
    session = admin.cookies.get("atsuit_session")
    anon = TestClient(admin.app)
    for path in ("/api/auth/login?username=admin&password=correct-horse", f"/api/bootstrap?node_token={tok}",
                 f"/api/bootstrap?token={session}", "/api/admin/licence?key=abc"):
        if "login" in path:
            r = anon.post(path, json={"username": "admin", "password": "correct-horse"})
        else:
            r = anon.get(path)
        assert r.status_code == 400 and "never in the address" in r.json()["detail"], path


def test_signing_in_by_address_no_longer_works(admin):
    """Even past the address check, the server never reads who you are from it."""
    from starlette.datastructures import QueryParams

    from atsuit import security

    tok, _ = _laptop(admin)
    assert security.resolve({}, {}) is None
    assert "query" not in security.resolve.__code__.co_varnames
    assert QueryParams(f"node_token={tok}")  # (what the old code read)


# ------------------------------------------------------------- websockets --
def test_websockets_take_a_one_time_ticket_not_a_token(admin):
    tok, h = _laptop(admin)
    rid = admin.get("/api/bootstrap").json()["rooms"][0]["id"]
    admin.put(f"/api/captions/{rid}/settings", json={"enabled": True})
    anon = TestClient(admin.app)
    # The old way, a token in the address, gets nothing.
    with pytest.raises(WebSocketDisconnect) as e:
        with anon.websocket_connect(f"/ws/audio/{rid}?node_token={tok}") as ws:
            ws.receive_json()
    assert e.value.code == 4403
    # A header works (the node agent)...
    with anon.websocket_connect(f"/ws/audio/{rid}?mode=local", headers=h) as ws:
        assert ws.receive_json()["type"] == "ready"
    # ...and so does a ticket (the browser), once.
    assert anon.post("/api/auth/ws-ticket").status_code == 401
    ticket = anon.post("/api/auth/ws-ticket", headers=h).json()["ticket"]
    with anon.websocket_connect(f"/ws/audio/{rid}?mode=local&ticket={ticket}") as ws:
        assert ws.receive_json()["type"] == "ready"
    with pytest.raises(WebSocketDisconnect):
        with anon.websocket_connect(f"/ws/audio/{rid}?mode=local&ticket={ticket}") as ws:
            ws.receive_json()
    # The general socket signs in with one too.
    ticket = anon.post("/api/auth/ws-ticket", headers=h).json()["ticket"]
    with anon.websocket_connect(f"/ws?topics=fleet&ticket={ticket}") as ws:
        assert ws.receive_json()["signed_in"] is True


def test_a_ticket_runs_out_and_dies_with_its_laptop(admin, monkeypatch):
    from atsuit import security

    tok, h = _laptop(admin)
    t1 = admin.post("/api/auth/ws-ticket", headers=h).json()["ticket"]
    t2 = admin.post("/api/auth/ws-ticket", headers=h).json()["ticket"]
    real = time.monotonic
    monkeypatch.setattr(security.time, "monotonic", lambda: real() + security.TICKET_SECONDS + 1)
    assert security.redeem_ticket(t1) is None
    monkeypatch.setattr(security.time, "monotonic", real)
    nid = next(n for n in admin.get("/api/fleet/nodes").json() if n["name"] == "ATLAP1")["id"]
    assert admin.delete(f"/api/fleet/nodes/{nid}").status_code == 200
    assert security.redeem_ticket(t2) is None
    assert security.redeem_ticket("") is None and security.redeem_ticket("made-up") is None


# ------------------------------------------------------------------- logs --
def test_logs_never_show_a_secret():
    from atsuit import security

    assert security.redact_url("/ws/audio/3?node_token=abc&mode=local") == "/ws/audio/3?node_token=***&mode=local"
    assert security.redact_url("/node?username=amy&password=hunter22") == "/node?username=***&password=***"
    assert security.redact_url("/api/screens/pair/status?secret=s3cret") == "/api/screens/pair/status?secret=***"
    assert security.redact_url("/present/abcdefghijklmnopqrstuvwxyz") == "/present/***"
    assert security.redact_url("/api/present/abcdefghijklmnopqrstuvwxyz/upload") == "/api/present/***/upload"
    assert security.redact_url("/api/presenter/sync/AB12-CD34/files/3") == "/api/presenter/sync/***/files/3"
    assert security.redact_url("/timer/1?view=stage") == "/timer/1?view=stage"
    security.redact_logs()
    for name, args in (("uvicorn.access", ("10.0.0.5:5000", "GET", "/node?password=hunter22", "1.1", 303)),
                       ("uvicorn.error", ("10.0.0.5:5000", "/ws/audio/1?ticket=t1ck3t"))):
        log = logging.getLogger(name)
        record = log.makeRecord(name, logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d' if len(args) == 5 else '%s - "WebSocket %s"',
                                args, None)
        assert all(f.filter(record) for f in log.filters)
        text = record.getMessage()
        assert "hunter22" not in text and "t1ck3t" not in text and "***" in text, text


def test_a_laptop_reporting_its_address_keeps_secrets_out_of_the_console(admin):
    _, h = _laptop(admin)
    admin.post("/api/nodes/heartbeat", headers=h, json={"current_url": "http://srv/node?username=a&password=b&x=1#token=zzz"})
    n = next(n for n in admin.get("/api/fleet/nodes").json() if n["name"] == "ATLAP1")
    assert n["current_url"] == "http://srv/node?x=1"
    admin.post("/api/nodes/heartbeat", headers=h, json={"current_url": "http://srv/node#/timer"})
    n = next(n for n in admin.get("/api/fleet/nodes").json() if n["name"] == "ATLAP1")
    assert n["current_url"] == "http://srv/node#/timer"


# ---------------------------------------------------------- agents, sync --
def test_room_sync_code_goes_in_a_header(admin):
    rid = admin.get("/api/bootstrap").json()["rooms"][0]["id"]
    code = admin.post(f"/api/presenter/rooms/{rid}/sync-code").json()["sync_code"]
    anon = TestClient(admin.app)
    assert anon.get("/api/presenter/sync").status_code == 401
    assert anon.get("/api/presenter/sync", headers={"X-Sync-Code": "WRONG-CODE"}).status_code == 404
    m = anon.get("/api/presenter/sync", headers={"X-Sync-Code": code.lower()}).json()
    assert m["room_id"] == rid
    assert anon.get("/api/presenter/sync-node/files/1", headers={"X-Sync-Code": code}).status_code == 404  # not a file for this room


def test_screen_agent_asks_for_its_token_without_putting_the_secret_in_the_address():
    src = (ROOT / "screen-agent" / "atsuit_screen.py").read_text("utf-8")
    assert 'http(self.server, "POST", "/api/screens/pair/status", {"secret": self.secret})' in src


# ------------------------------------------------- the rest of the area --
def test_security_headers_and_no_caching_of_sign_in(admin):
    r = admin.get("/api/auth/me")
    assert r.headers["cache-control"] == "no-store" and r.headers["referrer-policy"] == "same-origin"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert admin.get("/present/abcdefghijklmnopqrstuvwxyz").headers["referrer-policy"] == "no-referrer"
    assert "no-store" not in admin.get("/api/bootstrap").headers.get("cache-control", "")


def test_cookies_are_secure_over_https(admin):
    https = TestClient(admin.app, base_url="https://testserver")
    r = https.post("/api/auth/login", json={"username": "admin", "password": "correct-horse"})
    assert r.status_code == 200 and "secure" in r.headers["set-cookie"].lower()
    plain = TestClient(admin.app)
    r = plain.post("/api/auth/login", json={"username": "admin", "password": "correct-horse"})
    assert "secure" not in r.headers["set-cookie"].lower()


def test_a_new_password_signs_out_everywhere_else(admin):
    admin.post("/api/admin/accounts", json={"username": "amy", "password": "amy-password-1", "display_name": "Amy", "role": "tech"})
    a, b = TestClient(admin.app), TestClient(admin.app)
    for c in (a, b):
        assert c.post("/api/auth/login", json={"username": "amy", "password": "amy-password-1"}).status_code == 200
    assert a.post("/api/auth/password", json={"current": "amy-password-1", "new": "amy-password-2"}).status_code == 200
    assert a.get("/api/auth/me").status_code == 200
    assert b.get("/api/auth/me").status_code == 401


def test_sign_in_fields_have_limits(admin):
    assert admin.post("/api/auth/login", json={"username": "admin", "password": "x" * 10_000}).status_code == 422
    assert admin.post("/api/auth/login", json={"username": "a" * 1000, "password": "x"}).status_code == 422
