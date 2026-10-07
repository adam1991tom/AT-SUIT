import time


def rooms(c):
    return {r["name"]: r["id"] for r in c.get("/api/bootstrap").json()["rooms"]}


def test_chat_flow(admin):
    chans = admin.get("/api/comms/channels").json()
    room_ch = next(c for c in chans if c["kind"] == "room")
    with admin.websocket_connect(f"/ws?topics=room:{room_ch['room_id']}") as ws:
        assert ws.receive_json()["type"] == "hello"
        m = admin.post(f"/api/comms/channels/{room_ch['id']}/messages", json={"body": "Mic 2 is down", "priority": "urgent"}).json()
        evt = ws.receive_json()
        assert evt["type"] == "message.new" and evt["data"]["body"] == "Mic 2 is down"
    r = admin.post(f"/api/comms/messages/{m['id']}/attachments", files={"file": ("a.txt", b"hello", "text/plain")})
    assert r.status_code == 200 and r.json()["attachments"][0]["original_name"] == "a.txt"
    att = r.json()["attachments"][0]["id"]
    assert admin.get(f"/api/comms/attachments/{att}").content == b"hello"
    msgs = admin.get(f"/api/comms/channels/{room_ch['id']}/messages").json()
    assert msgs[-1]["body"] == "Mic 2 is down"
    # stored encrypted
    from atsuit import db
    with db.ro() as c:
        assert "Mic 2" not in c.execute("SELECT body_enc FROM messages").fetchone()[0]


def test_dm_privacy(admin):
    admin.post("/api/admin/accounts", json={"username": "amy", "password": "password1", "role": "tech"})
    admin.post("/api/admin/accounts", json={"username": "ben", "password": "password1", "role": "tech"})
    ppl = {p["display_name"]: p["id"] for p in admin.get("/api/comms/people").json()}
    dm = admin.post("/api/comms/dm", json={"account_id": ppl["amy"]}).json()["id"]
    admin.post(f"/api/comms/channels/{dm}/messages", json={"body": "secret"})
    admin.post("/api/auth/login", json={"username": "ben", "password": "password1"})
    assert admin.get(f"/api/comms/channels/{dm}/messages").status_code == 404
    admin.post("/api/auth/login", json={"username": "amy", "password": "password1"})
    assert admin.get(f"/api/comms/channels/{dm}/messages").json()[0]["body"] == "secret"


def test_help_requests(admin):
    rid = rooms(admin)["CC"]
    h = admin.post("/api/comms/help", json={"room_id": rid, "description": "Clicker dead"}).json()
    assert h["status"] == "open"
    assert admin.put(f"/api/comms/help/{h['id']}", json={"status": "resolved"}).json()["status"] == "resolved"


def test_timer(admin):
    rid = rooms(admin)["HD"]
    admin.post(f"/api/timers/{rid}/set", json={"duration_ms": 600000, "title": "Keynote"})
    s = admin.post(f"/api/timers/{rid}/start").json()
    assert s["running"] and s["title"] == "Keynote"
    time.sleep(0.2)
    s = admin.post(f"/api/timers/{rid}/pause").json()
    assert not s["running"] and 599000 < s["remaining_ms"] < 600000
    assert admin.post(f"/api/timers/{rid}/add", json={"delta_ms": 60000}).json()["remaining_ms"] > 650000
    assert admin.post(f"/api/timers/{rid}/reset").json()["remaining_ms"] == 600000
    admin.post("/api/auth/logout")
    assert admin.get(f"/api/timers/{rid}").status_code == 200  # public
    assert admin.post(f"/api/timers/{rid}/start").status_code == 401


def test_api_key_controls_timer(admin):
    rid = rooms(admin)["RH"]
    key = admin.post("/api/admin/api-keys", json={"name": "Companion"}).json()["key"]
    admin.post("/api/auth/logout")
    r = admin.post(f"/api/timers/{rid}/start", headers={"X-API-Key": key})
    assert r.status_code == 200 and r.json()["running"]


def test_fleet_enrol_heartbeat_commands(admin):
    code = admin.get("/api/fleet/enrolment").json()[0]["enrol_code"]
    assert admin.post("/api/nodes/enrol", json={"code": "WRONG", "name": "x"}).status_code == 403
    n = admin.post("/api/nodes/enrol", json={"code": code, "name": "atlap1", "kind": "tech"}).json()
    hdr = {"Authorization": f"Node {n['token']}"}
    assert admin.post("/api/nodes/heartbeat", json={"ip": "10.0.0.5", "version": "0.1.0"}, headers=hdr).status_code == 200
    nodes = admin.get("/api/fleet/nodes").json()
    assert nodes[0]["name"] == "ATLAP1" and nodes[0]["online"]
    rid = rooms(admin)["CC"]
    admin.put(f"/api/fleet/nodes/{n['node_id']}", json={"room_id": rid})
    admin.post(f"/api/fleet/nodes/{n['node_id']}/command", json={"kind": "set_url", "payload": {"url": "http://x/y"}})
    cmds = admin.get("/api/nodes/commands", headers=hdr).json()
    assert cmds[0]["kind"] == "set_url"
    admin.post(f"/api/nodes/commands/{cmds[0]['id']}/ack", json={"ok": True}, headers=hdr)
    assert admin.get("/api/nodes/commands", headers=hdr).json() == []
    assert admin.get("/api/nodes/me", headers=hdr).json()["room"]["name"] == "CC"
    # a node token can post chat as the node, but not reach admin
    assert admin.get("/api/admin/accounts", headers=hdr).status_code in (401, 403)


def test_node_limit_evaluation(admin):
    code = admin.get("/api/fleet/enrolment").json()[0]["enrol_code"]
    for i in range(5):
        assert admin.post("/api/nodes/enrol", json={"code": code, "name": f"n{i}"}).status_code == 200
    assert admin.post("/api/nodes/enrol", json={"code": code, "name": "n6"}).status_code == 402


def test_legacy_kiosk_agent(admin):
    assert admin.post("/heartbeat", json={"host": "atpi1", "ip": "10.100.70.132", "version": "2.0.3"}).text == "OK"
    node = admin.get("/api/fleet/nodes").json()[0]
    assert node["legacy"] == 1 and node["kind"] == "kiosk"
    assert admin.get("/agent/poll/ATPI1").json() == {"id": None, "url": None}
    admin.post(f"/api/fleet/nodes/{node['id']}/command", json={"kind": "set_url", "payload": {"url": "http://t/1"}})
    poll = admin.get("/agent/poll/atpi1").json()
    assert poll["url"] == "http://t/1"
    assert admin.post("/agent/ack", json={"host": "atpi1", "id": poll["id"]}).text == "ACK OK"
    assert admin.get("/agent/poll/atpi1").json()["url"] is None
    admin.put("/api/admin/settings", json={"legacy_fleet_api": False})
    assert admin.post("/heartbeat", json={"host": "atpi1"}).status_code == 404


def test_imports(admin):
    rooms_txt = ("CC|http://10.100.70.101:4001/external/HCC/|HCC STANDARD;\n"
                 "CC|http://10.100.70.101:8000/emulator/CC|BUTTONS CONTROL;\n"
                 "KING|http://10.100.70.101:4021/studio|STUDIO CLOCK;\n"
                 "from flask import Flask\napp = Flask(__name__)\n")
    r = admin.post("/api/admin/import/rooms-txt", files={"file": ("rooms.txt", rooms_txt.encode())}).json()
    assert r["links"] == 3 and r["rooms_created"] == ["KING"] and r["skipped_count"] == 1
    again = admin.post("/api/admin/import/rooms-txt", files={"file": ("rooms.txt", rooms_txt.encode())}).json()
    assert again["links"] == 0 and again["duplicates"] == 3
    tsv = ("BOARD/ITEM\tNAME\tHREF\tPING_URL\napp\tATLAP1\tHTTP://10.100.70.101\t\n"
           "app\tIP SCANNER\thttp://10.100.70.100:5000/\t\nboard\tADMIN\tpublic=0\t\n")
    r = admin.post("/api/admin/import/homarr", files={"file": ("h.tsv", tsv.encode())}).json()
    assert r["links"] == 2
    links = admin.get("/api/dashboard/links").json()
    assert {l["label"]: l["board"] for l in links if l["room_id"] is None} == {"ATLAP1": "public", "IP SCANNER": "admin"}
    r = admin.post("/api/admin/import/device-state",
                   files={"file": ("state.json", b'{"ATPI2": {"ip": "10.100.70.131", "mac": "aa"}}')}).json()
    assert r["nodes_created"] == 1


def test_roomcomms_import(admin, tmp_path):
    import hashlib, io, sqlite3, zipfile
    from cryptography.fernet import Fernet
    key = Fernet.generate_key()
    f = Fernet(key)
    p = tmp_path / "roomcomms.db"
    c = sqlite3.connect(p)
    c.executescript("""
    CREATE TABLE accounts(id INTEGER PRIMARY KEY, username TEXT, password_hash TEXT, display_name TEXT, role TEXT, active INTEGER);
    CREATE TABLE rooms(id INTEGER PRIMARY KEY, name TEXT, short_name TEXT);
    CREATE TABLE operators(id INTEGER PRIMARY KEY, name TEXT);
    CREATE TABLE messages(id INTEGER PRIMARY KEY, scope TEXT, scope_id INTEGER, sender TEXT, body TEXT, priority TEXT, created_at TEXT);
    CREATE TABLE help_requests(id INTEGER PRIMARY KEY, room_id INTEGER, room_name TEXT, requested_by TEXT, category TEXT, description TEXT, priority TEXT, status TEXT, created_at TEXT);
    """)
    salt = "cd" * 16
    c.execute("INSERT INTO accounts VALUES(1,'sam',?,'Sam','technician',1)",
              (f"{salt}${hashlib.pbkdf2_hmac('sha256', b'oldpass1', salt.encode(), 200000).hex()}",))
    c.execute("INSERT INTO rooms VALUES(7,'Q1','Q1')")
    c.execute("INSERT INTO messages VALUES(1,'room',7,'Sam',?,'normal','2026-01-01T00:00:00')", (f.encrypt(b"old room msg").decode(),))
    c.execute("INSERT INTO messages VALUES(2,'all',NULL,'Sam',?,'urgent','2026-01-01T00:00:01')", (f.encrypt(b"venue msg").decode(),))
    c.execute("INSERT INTO messages VALUES(3,'dm',1,'Sam',?,'normal','2026-01-01T00:00:02')", (f.encrypt(b"dm").decode(),))
    c.execute("INSERT INTO help_requests VALUES(1,7,'Q1','Sam','av','No sound','urgent','open','2026-01-01')")
    c.commit(); c.close()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.write(p, "data/roomcomms.db")
        z.writestr("data/.encryption_key", key)
    r = admin.post("/api/admin/import/roomcomms", files={"file": ("rc.zip", buf.getvalue())}).json()
    assert r["accounts"] == 1 and r["messages"] == 2 and r["messages_skipped_dm"] == 1 and r["rooms_created"] == ["Q1"]
    assert admin.post("/api/admin/import/roomcomms", files={"file": ("rc.zip", buf.getvalue())}).status_code == 409
    assert admin.post("/api/auth/login", json={"username": "sam", "password": "oldpass1"}).status_code == 200
    q1 = next(ch for ch in admin.get("/api/comms/channels").json() if ch["name"] == "Q1")
    assert admin.get(f"/api/comms/channels/{q1['id']}/messages").json()[0]["body"] == "old room msg"


def test_dashboard_permissions(admin):
    admin.post("/api/dashboard/links", json={"label": "Secret", "url": "http://x", "board": "admin"})
    admin.post("/api/dashboard/links", json={"label": "Public", "url": "http://y"})
    admin.post("/api/admin/accounts", json={"username": "viv", "password": "password1", "role": "viewer"})
    admin.post("/api/auth/login", json={"username": "viv", "password": "password1"})
    assert [l["label"] for l in admin.get("/api/dashboard/links").json()] == ["Public"]
    assert admin.post("/api/dashboard/links", json={"label": "x", "url": "http://z"}).status_code == 403


def test_captions_test_text_and_public_ws(admin):
    rid = rooms(admin)["CC"]
    with admin.websocket_connect(f"/ws?topics=captions:{rid},fleet") as ws:
        hello = ws.receive_json()
        assert f"captions:{rid}" in hello["topics"]
        admin.post(f"/api/captions/{rid}/test", json={"text": "Welcome everyone"})
        assert ws.receive_json()["data"]["text"] == "Welcome everyone"
    assert admin.get(f"/api/captions/{rid}/recent").json()["finals"] == ["Welcome everyone"]
    st = admin.get("/api/captions/status").json()
    assert st["engine"]["state"] == "off"


def test_anonymous_ws_cannot_read_chat(client, admin):
    admin.post("/api/auth/logout")
    with admin.websocket_connect("/ws?topics=room:1,site:1,fleet,timer:1") as ws:
        assert ws.receive_json()["topics"] == ["timer:1"]


def test_overlay_target_validation(admin):
    assert admin.post("/api/overlays/targets", json={"name": "x", "base_url": "file:///etc"}).status_code == 400
    t = admin.post("/api/overlays/targets", json={"name": "LAP88", "base_url": "http://127.0.0.1:9", "token": "abc"}).json()
    r = admin.post(f"/api/overlays/targets/{t['id']}/action", json={"overlay": "1", "action": "show"})
    assert r.status_code == 502
    assert admin.post(f"/api/overlays/targets/{t['id']}/action", json={"overlay": "all", "action": "seturl"}).status_code == 400


def test_node_agent_self_update_feed(admin):
    code = admin.get("/api/fleet/enrolment").json()[0]["enrol_code"]
    hdr = {"Authorization": f"Node {admin.post('/api/nodes/enrol', json={'code': code, 'name': 'lap9'}).json()['token']}"}
    bundled = admin.post("/api/nodes/heartbeat", json={}, headers=hdr).json()["agent"]
    assert bundled["version"]
    new = b'VERSION = "9.9.9"\nprint("hi")\n'
    r = admin.post("/api/fleet/agent", files={"file": ("atsuit_node.py", new)}).json()
    assert r["version"] == "9.9.9"
    assert admin.get("/api/nodes/agent", headers=hdr).json()["version"] == "9.9.9"
    assert admin.get("/api/nodes/agent/file", headers=hdr).content == new
    assert admin.post("/api/fleet/agent", files={"file": ("x.py", b"rm -rf /")}).status_code == 400
