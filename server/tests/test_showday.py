"""A whole show day through the API, start to finish.

One venue (rooms CC, HD, RH), an admin, a manager, a tech, two tech laptops
(main and backup in HD), a Linux stage screen, and the stage timer seen from a
screen that isn't signed in. Each step is a short block with a comment; where
the real server differs from the plan for the day, the comment says so.
"""
import io
import sqlite3
import zipfile

from fastapi.testclient import TestClient

ADMIN_PW = "correct-horse"


def _session(client, username=None, password=None):
    """A separate browser (its own cookies) on the same server; signed in if a username is given."""
    c = TestClient(client.app)
    if username:
        r = c.post("/api/auth/login", json={"username": username, "password": password})
        assert r.status_code == 200, r.text
    return c


def _node(token):
    return {"Authorization": f"Node {token}"}


def test_show_day(admin, client, tmp_path):
    # ---------------------------------------------------------------- 1. setup
    # The `admin` fixture ran the setup wizard (rooms CC, HD, RH), which signs the admin in.
    # Sign in again explicitly, as the admin would at the start of the day.
    assert admin.post("/api/auth/logout").status_code == 200
    assert admin.get("/api/bootstrap").status_code == 401
    assert admin.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PW}).status_code == 200
    boot = admin.get("/api/bootstrap").json()
    assert boot["me"]["role"] == "admin"
    rooms = {r["name"]: r["id"] for r in boot["rooms"]}
    assert list(rooms) == ["CC", "HD", "RH"]
    hd, cc = rooms["HD"], rooms["CC"]
    site_id = boot["sites"][0]["id"]
    stage = _session(client)  # a stage screen's browser: never signs in

    # ------------------------------------------------------ 2. manager and tech
    # The admin adds a manager, who signs in from their own browser.
    r = admin.post("/api/admin/accounts", json={"username": "mia", "password": "mia-password", "display_name": "Mia",
                                                "role": "manager"})
    assert r.status_code == 200, r.text
    mgr = _session(client, "mia", "mia-password")
    assert mgr.get("/api/bootstrap").json()["me"]["role"] == "manager"
    # The manager adds a tech account, adds a room and renames one.
    r = mgr.post("/api/admin/accounts", json={"username": "tom", "password": "tom-password", "display_name": "Tom",
                                              "role": "tech"})
    assert r.status_code == 200, r.text
    tom_id = r.json()["id"]
    r = mgr.post("/api/admin/rooms", json={"site_id": site_id, "name": "Breakout 4"})
    assert r.status_code == 200, r.text
    assert mgr.put(f"/api/admin/rooms/{rooms['RH']}", json={"site_id": site_id, "name": "Riverside Hall"}).status_code == 200
    names = [r["name"] for r in admin.get("/api/bootstrap").json()["rooms"]]
    assert "Breakout 4" in names and "Riverside Hall" in names and "RH" not in names
    # The renamed room's chat channel follows the new name.
    assert any(ch["name"] == "Riverside Hall" and ch["kind"] == "room" for ch in admin.get("/api/comms/channels").json())
    # The manager can't touch the licence, API keys or backups (admin-only).
    assert mgr.get("/api/admin/licence").status_code == 403
    assert mgr.put("/api/admin/licence", json={"key": "x"}).status_code == 403
    assert mgr.get("/api/admin/api-keys").status_code == 403
    assert mgr.post("/api/admin/api-keys", json={"name": "Companion"}).status_code == 403
    assert mgr.get("/api/admin/backup").status_code == 403
    # Nor make admins, nor see the enrolment code that adds laptops.
    assert mgr.post("/api/admin/accounts", json={"username": "boss", "password": "password1", "role": "admin"}).status_code == 403
    assert mgr.get("/api/fleet/enrolment").status_code == 403

    # ------------------------------------------- 3. tech laptops start the day
    code = admin.get("/api/fleet/enrolment").json()[0]["enrol_code"]
    lap_a, lap_b = _session(client), _session(client)  # each laptop runs its own browser
    a = lap_a.post("/api/nodes/enrol", json={"code": code, "name": "hd-main", "kind": "tech"}).json()
    b = lap_b.post("/api/nodes/enrol", json={"code": code, "name": "hd-backup", "kind": "tech"}).json()
    ha, hb = _node(a["token"]), _node(b["token"])
    assert a["name"] == "HD-MAIN" and b["name"] == "HD-BACKUP"
    # A fresh laptop has no room until the tech picks one.
    assert lap_a.get("/api/nodes/me", headers=ha).json()["room"] is None
    # Each tech types their name, picks HD and says main or backup. Laptop B starts as main and then
    # switches itself to backup with the mode endpoint.
    assert lap_a.post("/api/nodes/me/start", headers=ha, json={"operator": "Amy", "room_id": hd, "mode": "main"}).status_code == 200
    assert lap_b.post("/api/nodes/me/start", headers=hb, json={"operator": "Ben", "room_id": hd, "mode": "main"}).status_code == 200
    assert lap_b.put("/api/nodes/me/mode", headers=hb, json={"mode": "backup"}).status_code == 200
    assert lap_b.put("/api/nodes/me/mode", headers=hb, json={"mode": "loud"}).status_code == 422
    me_a, me_b = lap_a.get("/api/nodes/me", headers=ha).json(), lap_b.get("/api/nodes/me", headers=hb).json()
    assert (me_a["node"]["operator"], me_a["node"]["mode"], me_a["room"]["id"]) == ("Amy", "main", hd)
    assert (me_b["node"]["operator"], me_b["node"]["mode"], me_b["room"]["id"]) == ("Ben", "backup", hd)
    # Starting the day signs the laptop's browser in with a cookie: the tech is "Amy", a tech.
    me = lap_a.get("/api/bootstrap").json()["me"]
    assert me["kind"] == "node" and me["name"] == "Amy" and me["role"] == "tech"
    # Later in the day a laptop can't move itself to another room, by either route.
    assert lap_a.put("/api/nodes/me/room", headers=ha, json={"room_id": cc}).status_code == 403
    assert lap_a.post("/api/nodes/me/start", headers=ha, json={"operator": "Amy", "room_id": cc, "mode": "main"}).status_code == 403
    # ...nor through the console's node edit (that is for accounts: manager or admin).
    assert lap_a.put(f"/api/fleet/nodes/{a['node_id']}", headers=ha, json={"room_id": cc}).status_code == 403
    # The admin moves the backup laptop to CC from the console; the laptop sees it.
    assert admin.put(f"/api/fleet/nodes/{b['node_id']}", json={"room_id": cc}).status_code == 200
    assert lap_b.get("/api/nodes/me", headers=hb).json()["room"]["id"] == cc
    assert lap_b.post("/api/nodes/heartbeat", headers=hb, json={"version": "app-0.3.0"}).json()["room_id"] == cc
    fleet = {n["name"]: n for n in admin.get("/api/fleet/nodes").json()}
    assert fleet["HD-MAIN"]["room_id"] == hd and fleet["HD-BACKUP"]["room_id"] == cc
    assert fleet["HD-BACKUP"]["operator"] == "Ben" and fleet["HD-BACKUP"]["mode"] == "backup"

    # ------------------------------------------------- 4. the Linux stage screen
    scr = _session(client)
    k = scr.post("/api/nodes/enrol", json={"code": code, "name": "hd-stage", "kind": "kiosk"}).json()
    hk = _node(k["token"])
    # A kiosk can't "start a day" like a tech laptop.
    assert scr.post("/api/nodes/me/start", headers=hk, json={"operator": "X", "room_id": hd, "mode": "main"}).status_code == 400
    # Routed from the console: HD's Standard timer view.
    assert admin.put(f"/api/fleet/nodes/{k['node_id']}/screen", json={"room_id": hd, "view": "hcc"}).status_code == 200
    beat = scr.post("/api/nodes/heartbeat", headers=hk, json={"version": "screen-agent 0.2.0"}).json()
    assert beat["room_id"] == hd and beat["screen_view"] == "hcc" and "screen_agent" in beat
    # Then pushed a page with a set_url command; the screen polls it, acks it, and reports what it shows.
    page = f"http://testserver/timer/{hd}"
    r = admin.post(f"/api/fleet/nodes/{k['node_id']}/command", json={"kind": "set_url", "payload": {"url": page}})
    assert r.status_code == 200 and r.json()["command_id"]
    assert admin.post(f"/api/fleet/nodes/{k['node_id']}/command",
                      json={"kind": "set_url", "payload": {"url": "file:///etc/passwd"}}).status_code == 400
    cmds = scr.get("/api/nodes/commands?kinds=set_url,reload", headers=hk).json()
    assert [(c["kind"], c["payload"]["url"]) for c in cmds] == [("set_url", page)]
    assert scr.post(f"/api/nodes/commands/{cmds[0]['id']}/ack", headers=hk, json={"ok": True}).status_code == 200
    assert scr.get("/api/nodes/commands", headers=hk).json() == []
    kiosk = next(n for n in admin.get("/api/fleet/nodes").json() if n["id"] == k["node_id"])
    assert kiosk["kind"] == "kiosk" and kiosk["room_id"] == hd and kiosk["current_url"] == page and kiosk["online"]

    # ------------------------------------------------------------- 5. timers
    # Amy builds HD's cue list from her laptop (a node token is tech enough).
    ids = []
    for cue, title, mins in (("1", "Doors", 5), ("2", "Keynote", 30), ("3", "Q&A", 10)):
        r = lap_a.post(f"/api/timers/{hd}/cues", headers=ha, json={"cue": cue, "title": title, "duration_ms": mins * 60000})
        assert r.status_code == 200, r.text
        ids.append(r.json()["id"])
    assert [q["title"] for q in stage.get(f"/api/timers/{hd}/cues").json()] == ["Doors", "Keynote", "Q&A"]  # public
    # The stage screen, not signed in, can read the timer but not drive it.
    assert stage.get(f"/api/timers/{hd}").json()["playback"] == "stop"
    assert stage.post(f"/api/timers/{hd}/go").status_code == 401
    # GO with nothing loaded plays the first cue.
    s = lap_a.post(f"/api/timers/{hd}/go", headers=ha).json()
    assert s["cue"]["id"] == ids[0] and s["running"] and s["next"]["id"] == ids[1]
    pub = stage.get(f"/api/timers/{hd}").json()
    assert pub["title"] == "Doors" and pub["playback"] == "play" and pub["room"] == "HD"
    # GO again plays the next cue.
    lap_a.post(f"/api/timers/{hd}/go", headers=ha)
    pub = stage.get(f"/api/timers/{hd}").json()
    assert pub["cue"]["id"] == ids[1] and pub["title"] == "Keynote" and pub["running"] and pub["cue_index"] == 1
    # Pause.
    s = lap_a.post(f"/api/timers/{hd}/pause", headers=ha).json()
    left = s["remaining_ms"]
    pub = stage.get(f"/api/timers/{hd}").json()
    assert pub["playback"] == "pause" and not pub["running"] and pub["remaining_ms"] == left
    # Add a minute (paused, so it's exact).
    lap_a.post(f"/api/timers/{hd}/add", headers=ha, json={"delta_ms": 60000})
    pub = stage.get(f"/api/timers/{hd}").json()
    assert pub["remaining_ms"] == left + 60000 and pub["added_ms"] == 60000
    # Carry on.
    lap_a.post(f"/api/timers/{hd}/start", headers=ha)
    assert stage.get(f"/api/timers/{hd}").json()["playback"] == "play"
    # A quick message: pick one of the ready-made ones, show it, hide it.
    quick = lap_a.get("/api/timers-quick-messages", headers=ha).json()
    assert quick["messages"] and quick["can_edit"] is False  # only managers edit the list
    msg = quick["messages"][0]
    lap_a.post(f"/api/timers/{hd}/message/show", headers=ha, json={"text": msg})
    pub = stage.get(f"/api/timers/{hd}").json()
    assert pub["message"] == msg and pub["message_visible"]
    lap_a.post(f"/api/timers/{hd}/message/hide", headers=ha)
    pub = stage.get(f"/api/timers/{hd}").json()
    assert not pub["message_visible"] and not pub["message_blink"]
    # Stop clears the stage.
    lap_a.post(f"/api/timers/{hd}/stop", headers=ha)
    pub = stage.get(f"/api/timers/{hd}").json()
    assert pub["playback"] == "stop" and pub["cue"] is None and not pub["running"] and pub["title"] == ""

    # --------------------------------------------------------------- 6. chat
    chans = lap_a.get("/api/comms/channels", headers=ha).json()
    hd_ch = next(c for c in chans if c["kind"] == "room" and c["room_id"] == hd)["id"]
    crew_ch = next(c for c in chans if c["kind"] == "site")["id"]  # "All crew"
    m1 = lap_a.post(f"/api/comms/channels/{hd_ch}/messages", headers=ha, json={"body": "Lectern mic is flat"}).json()
    m2 = lap_a.post(f"/api/comms/channels/{crew_ch}/messages", headers=ha,
                    json={"body": "Lunch is in the foyer", "priority": "important"}).json()
    assert m1["sender_name"] == "Amy" and m2["sender_name"] == "Amy"  # from the tech's name, not the laptop's
    # The admin reads both (and they count as unread until read).
    unread = {c["id"]: c["unread"] for c in admin.get("/api/comms/channels").json()}
    assert unread[hd_ch] >= 1 and unread[crew_ch] >= 1
    assert admin.get(f"/api/comms/channels/{hd_ch}/messages").json()[-1]["body"] == "Lectern mic is flat"
    last = admin.get(f"/api/comms/channels/{crew_ch}/messages").json()[-1]
    assert last["body"] == "Lunch is in the foyer" and last["priority"] == "important"
    admin.post(f"/api/comms/channels/{crew_ch}/read", json={"up_to": m2["id"]})
    assert next(c for c in admin.get("/api/comms/channels").json() if c["id"] == crew_ch)["unread"] == 0
    # A DM. Plan said "a DM"; in the real code DMs are between accounts only: a tech laptop
    # (node) gets 400 "Sign in as a person", so the admin DMs Tom (the manager's new tech).
    assert lap_a.post("/api/comms/dm", headers=ha, json={"account_id": tom_id}).status_code == 400
    dm = admin.post("/api/comms/dm", json={"account_id": tom_id}).json()["id"]
    admin.post(f"/api/comms/channels/{dm}/messages", json={"body": "Can you cover RH after lunch?"})
    tom = _session(client, "tom", "tom-password")
    assert tom.get(f"/api/comms/channels/{dm}/messages").json()[0]["body"] == "Can you cover RH after lunch?"
    tom.post(f"/api/comms/channels/{dm}/messages", json={"body": "Yes"})
    assert [m["body"] for m in admin.get(f"/api/comms/channels/{dm}/messages").json()] == ["Can you cover RH after lunch?", "Yes"]
    # Nobody else sees it: not the manager, not the laptop.
    assert mgr.get(f"/api/comms/channels/{dm}/messages").status_code == 404
    assert lap_a.get(f"/api/comms/channels/{dm}/messages", headers=ha).status_code == 404

    # --------------------------------------------------------------- 7. help
    h = lap_a.post("/api/comms/help", headers=ha, json={"room_id": hd, "category": "audio",
                                                         "description": "Radio mic 3 dropping out", "priority": "urgent"}).json()
    assert h["status"] == "open" and h["requested_by"] == "Amy" and h["room_name"] == "HD"
    assert len(admin.get("/api/comms/help?status=open").json()) == 1
    assert stage.get(f"/api/comms/help/board/{hd}").json()["calls"][0]["description"] == "Radio mic 3 dropping out"
    # "On the way": the real status is "acknowledged" (statuses are open / acknowledged / resolved).
    r = admin.put(f"/api/comms/help/{h['id']}", json={"status": "on-the-way"})
    assert r.status_code == 400
    h = admin.put(f"/api/comms/help/{h['id']}", json={"status": "acknowledged"}).json()
    assert h["status"] == "acknowledged" and h["assigned_to"] == "admin" and h["acknowledged_at"]
    assert admin.get("/api/comms/help?status=open").json() == []
    assert stage.get(f"/api/comms/help/board/{hd}").json()["calls"][0]["status"] == "acknowledged"  # still on the board
    h = admin.put(f"/api/comms/help/{h['id']}", json={"status": "resolved"}).json()
    assert h["status"] == "resolved" and h["resolved_at"]
    assert admin.get("/api/comms/help?status=open").json() == []
    assert stage.get(f"/api/comms/help/board/{hd}").json() == {"room_id": hd, "calls": [], "open_elsewhere": 0}

    # ------------------------------------------------------------ 8. captions
    assert admin.get("/api/captions/status").json()["engine"]["state"] == "off"  # ASR off in tests
    # Start saving a transcript first, so the test line lands in it.
    t = lap_a.post(f"/api/captions/{hd}/transcript/start", headers=ha).json()
    assert t["ok"] and t["id"]
    line = "Good morning and welcome to the show"
    assert lap_a.post(f"/api/captions/{hd}/test", headers=ha, json={"text": line}).json() == {"ok": True}
    rec = stage.get(f"/api/captions/{hd}/recent").json()  # public: caption screens catch up with it
    assert rec["finals"][-1] == line
    hist = lap_a.get(f"/api/captions/{hd}/history", headers=ha).json()
    assert hist[0]["text"] == line and all(w["confidence"] == 1.0 for w in hist[0]["words"])
    assert lap_a.post(f"/api/captions/{hd}/transcript/stop", headers=ha).json() == {"ok": True}
    tr = next(x for x in admin.get(f"/api/captions/transcripts?room_id={hd}").json() if x["id"] == t["id"])
    assert tr["ended_at"] and tr["size"] > 0
    assert line in admin.get(f"/api/captions/transcripts/{t['id']}").text
    # Clear the screens again for the next session.
    lap_a.post(f"/api/captions/{hd}/clear", headers=ha)
    assert stage.get(f"/api/captions/{hd}/recent").json()["finals"] == []

    # ------------------------------------------------------------- 9. overlay
    r = admin.put(f"/api/fleet/nodes/{a['node_id']}/overlay", json={"on": True, "position": "bottom-bar", "size": "large"})
    assert r.status_code == 200, r.text
    mine = lap_a.get("/api/nodes/me/overlay", headers=ha).json()
    assert mine["want"]["on"] is True and mine["want"]["position"] == "bottom-bar" and mine["want"]["room_id"] == hd
    cmds = lap_a.get("/api/nodes/commands?kinds=overlay", headers=ha).json()
    assert len(cmds) == 1 and cmds[0]["payload"]["on"] is True and cmds[0]["payload"]["by"] == "admin"
    assert lap_a.post(f"/api/nodes/commands/{cmds[0]['id']}/ack", headers=ha, json={"ok": True}).status_code == 200
    # The app reports what it shows; the console sees it, and HD's overlay list has the main laptop.
    rep = {"on": True, "url": "", "target": f"http://testserver/timer/{hd}?view=overlay", "position": "bottom-bar",
           "size": "large", "display": 0, "opacity": 0.85, "room_id": hd}
    assert lap_a.put("/api/nodes/me/overlay", headers=ha, json=rep).status_code == 200
    assert admin.get(f"/api/fleet/nodes/{a['node_id']}/overlay").json()["state"]["on"] is True
    assert [x["name"] for x in admin.get(f"/api/rooms/{hd}/overlays").json()] == ["HD-MAIN"]  # HD-BACKUP is in CC now
    # A screen has no overlay.
    assert admin.put(f"/api/fleet/nodes/{k['node_id']}/overlay", json={"on": True}).status_code == 400

    # -------------------------------------------------------------- 10. backup
    r = admin.get("/api/admin/backup")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert z.testzip() is None and {"atsuit.db", "VERSION"} <= set(z.namelist())
    snap = tmp_path / "restore-check.db"
    snap.write_bytes(z.read("atsuit.db"))
    with sqlite3.connect(snap) as db:
        users = {row[0] for row in db.execute("SELECT username FROM accounts")}
        assert {"admin", "mia", "tom"} <= users
        assert db.execute("SELECT COUNT(*) FROM cues WHERE room_id=?", (hd,)).fetchone()[0] == 3
        assert db.execute("SELECT status FROM help_requests").fetchone()[0] == "resolved"

    # ----------------------------------------------------------- 11. audit log
    log = admin.get("/api/admin/audit").json()
    seen = {(x["actor"], x["action"]) for x in log}
    for want in [("admin", "setup.complete"), ("admin", "auth.login"), ("admin", "account.add"),
                 ("mia", "auth.login"), ("Mia", "account.add"), ("Mia", "room.add"), ("Mia", "room.edit"),
                 ("node:HD-MAIN", "node.enrol"), ("node:HD-STAGE", "node.enrol"), ("Amy", "node.start"), ("Ben", "node.start"),
                 ("admin", "node.edit"), ("admin", "node.screen"), ("admin", "node.set_url"), ("admin", "node.overlay"),
                 ("admin", "backup.download")]:
        assert want in seen, want
    # The manager reads the audit log too.
    assert mgr.get("/api/admin/audit").status_code == 200

    # ---------------------------------------------------------- 12. end of day
    for lap, hdr, name in ((lap_a, ha, "HD-MAIN"), (lap_b, hb, "HD-BACKUP")):
        assert lap.get("/api/bootstrap").status_code == 200  # still signed in by the day's cookie
        r = lap.post("/api/nodes/me/finish", headers=hdr)
        assert r.status_code == 200
        assert lap.get("/api/bootstrap").status_code == 401  # signed out
        me = lap.get("/api/nodes/me", headers=hdr).json()  # the enrolment itself remains for tomorrow
        assert me["node"]["operator"] == ""
    fleet = {n["name"]: n for n in admin.get("/api/fleet/nodes").json()}
    assert fleet["HD-MAIN"]["operator"] == "" and fleet["HD-BACKUP"]["operator"] == ""
    # The rooms stay for the rest of the working day (only the name is cleared).
    assert fleet["HD-MAIN"]["room_id"] == hd and fleet["HD-BACKUP"]["room_id"] == cc


def test_node_edit_keeps_room_unless_sent_and_kiosk_has_no_mode(admin):
    admin.post("/api/auth/login", json={"username": "admin", "password": "correct-horse"})
    code = admin.get("/api/fleet/enrolment").json()[0]["enrol_code"]
    room = admin.get("/api/bootstrap").json()["rooms"][0]["id"]
    from fastapi.testclient import TestClient
    kiosk = TestClient(admin.app)
    tok = kiosk.post("/api/nodes/enrol", json={"code": code, "name": "scr-1", "kind": "kiosk"}).json()["token"]
    nid = next(n["id"] for n in admin.get("/api/fleet/nodes").json() if n["name"].lower() == "scr-1")
    assert admin.put(f"/api/fleet/nodes/{nid}", json={"room_id": room}).status_code == 200
    # A rename alone leaves the screen in its room.
    assert admin.put(f"/api/fleet/nodes/{nid}", json={"name": "scr-2"}).status_code == 200
    n = next(n for n in admin.get("/api/fleet/nodes").json() if n["id"] == nid)
    assert n["name"].lower() == "scr-2" and n["room_id"] == room
    # An explicit null takes it out.
    admin.put(f"/api/fleet/nodes/{nid}", json={"room_id": None})
    assert next(n for n in admin.get("/api/fleet/nodes").json() if n["id"] == nid)["room_id"] is None
    # Main/backup is only for tech laptops.
    r = kiosk.put("/api/nodes/me/mode", json={"mode": "backup"}, headers={"Authorization": f"Node {tok}"})
    assert r.status_code == 400
