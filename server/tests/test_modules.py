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


def test_reactions_files_and_file_only_messages(admin):
    from atsuit import config
    ch = next(c for c in admin.get("/api/comms/channels").json() if c["kind"] == "room")["id"]
    m = admin.post(f"/api/comms/channels/{ch}/messages", json={"body": ""}).json()  # a picture on its own
    admin.post(f"/api/comms/messages/{m['id']}/attachments", files={"file": ("cue.png", b"\x89PNG fake", "image/png")})
    r = admin.post(f"/api/comms/messages/{m['id']}/reactions", json={"emoji": "👍"}).json()
    assert r["reactions"] == [{"emoji": "👍", "count": 1, "who": [r["reactions"][0]["who"][0]], "names": [r["reactions"][0]["names"][0]]}]
    assert r["attachments"][0]["mime"] == "image/png"
    assert admin.post(f"/api/comms/messages/{m['id']}/reactions", json={"emoji": "👍"}).json()["reactions"] == []  # again takes it away
    assert admin.post(f"/api/comms/messages/{m['id']}/reactions", json={"emoji": "<b>"}).status_code == 400
    admin.post(f"/api/comms/messages/{m['id']}/reactions", json={"emoji": "✅"})
    stored = list(config.cfg.uploads.iterdir())
    assert len(stored) == 1
    admin.delete(f"/api/comms/messages/{m['id']}")
    assert not stored[0].exists()  # deleting a message deletes its files
    assert admin.get(f"/api/comms/channels/{ch}/messages").json()[-1]["reactions"] == []


def test_admin_deletes_dm_chats(admin):
    from atsuit import config
    admin.post("/api/admin/accounts", json={"username": "amy", "password": "password1", "display_name": "Amy", "role": "tech"})
    admin.post("/api/admin/accounts", json={"username": "ben", "password": "password1", "display_name": "Ben", "role": "tech"})
    admin.post("/api/auth/login", json={"username": "amy", "password": "password1"})
    ppl = {p["display_name"]: p["id"] for p in admin.get("/api/comms/people").json()}
    dm = admin.post("/api/comms/dm", json={"account_id": ppl["Ben"]}).json()["id"]
    m = admin.post(f"/api/comms/channels/{dm}/messages", json={"body": "private"}).json()
    admin.post(f"/api/comms/messages/{m['id']}/attachments", files={"file": ("a.txt", b"x", "text/plain")})
    assert admin.get("/api/admin/comms/dms").status_code == 403  # techs can't
    amy = admin.get("/api/bootstrap").json()["me"]["id"]
    with admin.websocket_connect(f"/ws?topics=dm:{amy}") as ws:  # Amy's open chat hears about it
        assert ws.receive_json()["type"] == "hello"
        admin.post("/api/auth/login", json={"username": "admin", "password": "correct-horse"})
        rows = admin.get("/api/admin/comms/dms").json()
        assert rows == [{"id": dm, "members": ["Amy", "Ben"], "member_ids": rows[0]["member_ids"], "messages": 1, "files": 1, "last_at": rows[0]["last_at"]}]
        assert "private" not in str(rows)  # admins see who and how much, not what
        assert admin.delete(f"/api/admin/comms/dms/{dm}").json() == {"ok": True}
        evt = ws.receive_json()
        assert evt["type"] == "channel.deleted" and evt["data"] == {"channel_id": dm}
    assert admin.get("/api/admin/comms/dms").json() == []
    assert list(config.cfg.uploads.iterdir()) == []
    assert admin.delete(f"/api/admin/comms/dms/{dm}").status_code == 404


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


def test_tech_laptop_picks_room_each_day(admin, monkeypatch):
    from atsuit.modules import fleet

    code = admin.get("/api/fleet/enrolment").json()[0]["enrol_code"]
    tok = admin.post("/api/nodes/enrol", json={"code": code, "name": "atlap9", "kind": "tech"}).json()["token"]
    h = {"Authorization": f"Node {tok}"}
    me = admin.get("/api/nodes/me", headers=h).json()
    assert me["room"] is None and [r["name"] for r in me["rooms"]] == ["CC", "HD", "RH"]
    rid = me["rooms"][1]["id"]
    assert admin.put("/api/nodes/me/room", headers=h, json={"room_id": 99999}).status_code == 404
    assert admin.put("/api/nodes/me/room", headers=h, json={"room_id": rid}).status_code == 200
    assert admin.get("/api/nodes/me", headers=h).json()["room"]["id"] == rid
    assert admin.post("/api/nodes/heartbeat", headers=h, json={}).json()["room_id"] == rid
    # The next working day the laptop has no room until the tech picks one again.
    monkeypatch.setattr(fleet, "work_day", lambda c, site_id: "2099-01-01")
    assert admin.get("/api/nodes/me", headers=h).json()["room"] is None
    assert admin.post("/api/nodes/heartbeat", headers=h, json={}).json()["room_id"] is None
    assert [n for n in admin.get("/api/fleet/nodes").json() if n["name"] == "ATLAP9"][0]["room_id"] is None
    # Enrolment is once: the same token still works on the new day.
    assert admin.put("/api/nodes/me/room", headers=h, json={"room_id": rid}).status_code == 200
    assert admin.get("/api/nodes/me", headers=h).json()["room"]["id"] == rid


def test_tech_starts_with_name_room_and_main_or_backup(admin, client):
    """No password on a tech laptop: the tech types their name, picks the
    room and says whether it's the main or backup PC. The laptop's own
    enrolment signs them in, and chat comes from their name."""
    code = admin.get("/api/fleet/enrolment").json()[0]["enrol_code"]
    tok = admin.post("/api/nodes/enrol", json={"code": code, "name": "hd-main", "kind": "tech"}).json()["token"]
    h = {"Authorization": f"Node {tok}"}
    rid = admin.get("/api/nodes/me", headers=h).json()["rooms"][1]["id"]
    client.cookies.clear()
    assert client.post("/api/nodes/me/start", json={"operator": "Amy", "room_id": rid, "mode": "main"}).status_code == 401
    assert client.post("/api/nodes/me/start", headers=h, json={"operator": "Amy", "room_id": rid, "mode": "loud"}).status_code == 422
    assert client.post("/api/nodes/me/start", headers=h, json={"operator": "  ", "room_id": rid, "mode": "main"}).status_code == 422
    r = client.post("/api/nodes/me/start", headers=h, json={"operator": "  Amy  Smith ", "room_id": rid, "mode": "main"})
    assert r.status_code == 200 and "atsuit_node" in r.cookies
    # From now on the cookie is enough, as for a signed-in person.
    me = client.get("/api/bootstrap").json()["me"]
    assert me["kind"] == "node" and me["name"] == "Amy Smith" and me["role"] == "tech"
    ch = next(c for c in client.get("/api/comms/channels").json() if c["kind"] == "room" and c["room_id"] == rid)
    assert client.post(f"/api/comms/channels/{ch['id']}/messages", json={"body": "Mic 2 flat"}).json()["sender_name"] == "Amy Smith"
    n = next(n for n in admin.get("/api/fleet/nodes").json() if n["name"] == "HD-MAIN")
    assert (n["operator"], n["mode"], n["room_id"]) == ("Amy Smith", "main", rid)
    assert client.put("/api/nodes/me/mode", json={"mode": "backup"}).status_code == 200
    assert client.get("/api/nodes/me").json()["node"]["mode"] == "backup"
    # The room is locked for the day: the tech can't move the laptop to another room.
    other = admin.get("/api/nodes/me", headers=h).json()["rooms"][0]["id"]
    assert client.put("/api/nodes/me/room", json={"room_id": other}).status_code == 403
    # Signing out clears the name; the room stays for the day.
    client.post("/api/nodes/me/finish")
    assert client.get("/api/bootstrap").status_code == 401
    me = admin.get("/api/nodes/me", headers=h).json()
    assert me["room"]["id"] == rid and me["node"]["operator"] == "" and me["node"]["mode"] == "backup"
    assert client.post("/api/nodes/me/start", headers=h, json={"operator": "Bob", "room_id": other, "mode": "main"}).status_code == 403
    assert client.post("/api/nodes/me/start", headers=h, json={"operator": "Bob", "room_id": rid, "mode": "main"}).status_code == 200
    # Only an admin moves it, from the console.
    assert client.put("/api/fleet/nodes/1", json={"room_id": other}).status_code in (401, 403)
    client.cookies.clear()
    assert client.post("/api/auth/login", json={"username": "admin", "password": "correct-horse"}).status_code == 200
    nid = next(n for n in admin.get("/api/fleet/nodes").json() if n["name"] == "HD-MAIN")["id"]
    assert admin.put(f"/api/fleet/nodes/{nid}", json={"room_id": other}).status_code == 200
    assert admin.get("/api/nodes/me", headers=h).json()["room"]["id"] == other
    # A kiosk can't start a day.
    k = admin.post("/api/nodes/enrol", json={"code": code, "name": "foyer", "kind": "kiosk"}).json()["token"]
    assert client.post("/api/nodes/me/start", headers={"Authorization": f"Node {k}"}, json={"operator": "X", "room_id": rid, "mode": "main"}).status_code == 400


def test_kiosk_keeps_its_room(admin, monkeypatch):
    from atsuit.modules import fleet

    code = admin.get("/api/fleet/enrolment").json()[0]["enrol_code"]
    n = admin.post("/api/nodes/enrol", json={"code": code, "name": "kiosk1", "kind": "kiosk"}).json()
    rid = admin.get("/api/bootstrap").json()["rooms"][0]["id"]
    assert admin.put(f"/api/fleet/nodes/{n['node_id']}", json={"room_id": rid}).status_code == 200
    monkeypatch.setattr(fleet, "work_day", lambda c, site_id: "2099-01-01")
    assert admin.get("/api/nodes/me", headers={"Authorization": f"Node {n['token']}"}).json()["room"]["id"] == rid


def test_windows_app_release(admin, client):
    assert client.get("/api/nodes/app").json() == {"version": None}
    yml = b"version: 0.2.0\nfiles:\n  - url: AT-SUIT-Node-Setup-0.2.0.exe\npath: AT-SUIT-Node-Setup-0.2.0.exe\nsha512: abc\n"
    files = [("files", ("latest.yml", yml)), ("files", ("AT-SUIT-Node-Setup-0.2.0.exe", b"MZ fake installer"))]
    assert admin.post("/api/fleet/app", files=[("files", ("../evil.sh", b"x"))]).status_code == 400
    r = admin.post("/api/fleet/app", files=files).json()
    assert r == {"version": "0.2.0", "file": "AT-SUIT-Node-Setup-0.2.0.exe", "ready": True}
    assert client.get("/api/nodes/app/latest.yml").content == yml
    assert client.get("/api/nodes/app/AT-SUIT-Node-Setup-0.2.0.exe").content == b"MZ fake installer"
    assert client.get("/api/nodes/app/..%2Fatsuit.db").status_code == 404
    client.post("/api/auth/logout")
    assert client.post("/api/fleet/app", files=files).status_code in (401, 403)


def test_backstage_help_board_is_public_and_live(admin):
    rid = rooms(admin)["HD"]
    with admin.websocket_connect(f"/ws?topics=timer:{rid}") as ws:
        assert ws.receive_json()["type"] == "hello"
        h = admin.post("/api/comms/help", json={"room_id": rid, "category": "audio", "description": "Mic 3 dropping out"}).json()
        evt = ws.receive_json()
        assert evt["type"] == "help" and evt["data"]["calls"][0]["description"] == "Mic 3 dropping out"
        admin.put(f"/api/comms/help/{h['id']}", json={"status": "resolved"})
        assert ws.receive_json()["data"]["calls"] == []
    admin.post("/api/comms/help", json={"room_id": rooms(admin)["CC"], "description": "Clicker"})
    admin.post("/api/auth/logout")
    board = admin.get(f"/api/comms/help/board/{rid}").json()  # a screen, not signed in
    assert board == {"room_id": rid, "calls": [], "open_elsewhere": 1}
    page = admin.get(f"/timer/{rid}?view=backstage").text
    assert "studio" in page and admin.get(f"/timer/{rid}").text != page


def test_shared_computer_login_is_short(admin, client):
    """An admin signing in on a tech laptop gets a 30-minute session and no lasting cookie."""
    from datetime import datetime, timezone
    from atsuit import db

    client.cookies.clear()
    r = client.post("/api/auth/login", json={"username": "admin", "password": "correct-horse", "shared": True})
    assert r.status_code == 200
    assert "max-age" not in r.headers["set-cookie"].lower()
    with db.tx() as c:
        exp = c.execute("SELECT expires_at FROM sessions ORDER BY rowid DESC LIMIT 1").fetchone()[0]
    left = datetime.fromisoformat(exp.replace("Z", "+00:00")) - datetime.now(timezone.utc)
    assert 25 * 60 < left.total_seconds() <= 30 * 60 + 5


def test_manager_runs_rooms_and_techs_but_not_the_site(admin, client):
    """A manager sets up rooms, adds techs, moves laptops between rooms and reads the
    audit log, but can't touch site settings, the licence, branding, views or nodes."""
    assert admin.post("/api/admin/accounts", json={"username": "mia", "password": "password1", "role": "manager"}).status_code == 200
    code = admin.get("/api/fleet/enrolment").json()[0]["enrol_code"]
    node = admin.post("/api/nodes/enrol", json={"code": code, "name": "lap1", "kind": "tech"}).json()
    sid = admin.get("/api/admin/sites").json()[0]["id"]
    admin_id = next(a["id"] for a in admin.get("/api/admin/accounts").json() if a["username"] == "admin")
    client.cookies.clear()
    assert client.post("/api/auth/login", json={"username": "mia", "password": "password1"}).status_code == 200
    # allowed
    rid = client.post("/api/admin/rooms", json={"site_id": sid, "name": "Breakout 4"}).json()["id"]
    assert client.put(f"/api/admin/rooms/{rid}", json={"site_id": sid, "name": "Breakout Four"}).status_code == 200
    tid = client.post("/api/admin/accounts", json={"username": "tom", "password": "password1", "role": "tech"}).json()["id"]
    assert client.put(f"/api/admin/accounts/{tid}", json={"username": "tom", "role": "viewer"}).status_code == 200
    assert client.put(f"/api/fleet/nodes/{node['node_id']}", json={"room_id": rid}).status_code == 200
    assert any(l["action"] == "room.add" for l in client.get("/api/admin/audit").json())
    assert "enrol_code" not in client.get("/api/admin/sites").json()[0]
    # not allowed
    assert client.post("/api/admin/accounts", json={"username": "boss", "password": "password1", "role": "admin"}).status_code == 403
    assert client.put(f"/api/admin/accounts/{tid}", json={"username": "tom", "role": "manager"}).status_code == 403
    assert client.put(f"/api/admin/accounts/{admin_id}", json={"username": "admin", "role": "tech"}).status_code == 403
    assert client.delete(f"/api/admin/accounts/{admin_id}").status_code == 403
    assert client.put(f"/api/fleet/nodes/{node['node_id']}", json={"room_id": rid, "name": "renamed"}).status_code == 403
    for method, url in [("get", "/api/admin/settings"), ("put", "/api/admin/licence"), ("post", "/api/admin/sites"), ("get", "/api/fleet/enrolment"),
                        ("delete", f"/api/fleet/nodes/{node['node_id']}"), ("post", "/api/timers-designs"), ("get", "/api/admin/backup"), ("post", "/api/admin/api-keys")]:
        r = getattr(client, method)(url, **({"json": {}} if method in ("post", "put") else {}))
        assert r.status_code == 403, (method, url, r.status_code)
    # a manager can't raise or drop their own access
    me = client.get("/api/auth/me").json()["id"]
    assert client.put(f"/api/admin/accounts/{me}", json={"username": "mia", "role": "admin"}).status_code == 400


def test_wrong_passwords_are_logged_and_slowed(admin, client):
    from atsuit.modules import core

    core._login_fails.clear()
    client.cookies.clear()
    for _ in range(core.LOGIN_TRIES):
        assert client.post("/api/auth/login", json={"username": "admin", "password": "nope-nope"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "admin", "password": "correct-horse"}).status_code == 429
    core._login_fails.clear()
    assert client.post("/api/auth/login", json={"username": "admin", "password": "correct-horse"}).status_code == 200
    assert sum(1 for l in client.get("/api/admin/audit").json() if l["action"] == "auth.fail") == core.LOGIN_TRIES


def test_unread_counts_survive_a_reload(admin, client):
    """Unread chat is counted on the server, per account and per tech laptop."""
    rid = admin.get("/api/bootstrap").json()["rooms"][0]["id"]
    ch = next(c for c in admin.get("/api/comms/channels").json() if c["kind"] == "room" and c["room_id"] == rid)
    code = admin.get("/api/fleet/enrolment").json()[0]["enrol_code"]
    tok = admin.post("/api/nodes/enrol", json={"code": code, "name": "lap-u", "kind": "tech"}).json()["token"]
    h = {"Authorization": f"Node {tok}"}
    assert client.post("/api/nodes/me/start", headers=h, json={"operator": "Amy", "room_id": rid, "mode": "main"}).status_code == 200
    unread = lambda: next(c for c in client.get("/api/comms/channels", headers=h).json() if c["id"] == ch["id"])["unread"]
    before = unread()
    client.cookies.clear()
    client.post("/api/auth/login", json={"username": "admin", "password": "correct-horse"})
    mid = client.post(f"/api/comms/channels/{ch['id']}/messages", json={"body": "Mic 2 flat"}).json()["id"]
    mine = next(c for c in client.get("/api/comms/channels").json() if c["id"] == ch["id"])["unread"]
    client.cookies.clear()
    assert unread() == before + 1 and mine == 0  # your own message isn't unread for you
    assert client.post(f"/api/comms/channels/{ch['id']}/read", headers=h, json={"up_to": mid}).status_code == 200
    assert unread() == 0


def test_site_wide_appearance_is_admin_only(admin, client):
    assert client.get("/api/public/branding").json()["appearance"]["theme"] == "dark"
    assert admin.put("/api/admin/settings", json={"appearance": {"theme": "futuristic", "corners": "square"}}).status_code == 200
    assert admin.put("/api/admin/settings", json={"appearance": {"theme": "pink"}}).status_code == 400
    a = client.get("/api/public/branding").json()["appearance"]
    assert a["theme"] == "futuristic" and a["corners"] == "square" and a["density"] == "normal"
    assert admin.post("/api/admin/accounts", json={"username": "mo", "password": "password1", "role": "manager"}).status_code == 200
    client.cookies.clear()
    client.post("/api/auth/login", json={"username": "mo", "password": "password1"})
    assert client.put("/api/admin/settings", json={"appearance": {"theme": "light"}}).status_code == 403
