"""The AT-SUIT Node app's overlay, controlled from any tech, a laptop in the
room or Companion."""


def _laptop(admin, code, name, kind="tech"):
    n = admin.post("/api/nodes/enrol", json={"code": code, "name": name, "kind": kind}).json()
    return n["node_id"], {"Authorization": f"Node {n['token']}"}


def test_overlay_remote_control(admin, client):
    code = admin.get("/api/fleet/enrolment").json()[0]["enrol_code"]
    rooms = {r["name"]: r["id"] for r in admin.get("/api/bootstrap").json()["rooms"]}
    main_id, main = _laptop(admin, code, "hd-main")
    backup_id, backup = _laptop(admin, code, "hd-backup")
    other_id, other = _laptop(admin, code, "cc-main")
    kiosk_id, _ = _laptop(admin, code, "foyer", "kiosk")
    hd = rooms["HD"]
    admin.post("/api/nodes/me/start", headers=main, json={"operator": "Amy", "room_id": hd, "mode": "main"})
    admin.post("/api/nodes/me/start", headers=backup, json={"operator": "Ben", "room_id": hd, "mode": "backup"})
    admin.post("/api/nodes/me/start", headers=other, json={"operator": "Cat", "room_id": rooms["CC"], "mode": "main"})
    admin.post("/api/nodes/heartbeat", headers=main, json={"version": "app-0.3.0"})

    # The room's tech laptops, main PC first.
    lst = admin.get(f"/api/rooms/{hd}/overlays").json()
    assert [(x["name"], x["mode"], x["operator"]) for x in lst] == [("HD-MAIN", "main", "Amy"), ("HD-BACKUP", "backup", "Ben")]
    assert lst[0]["app"] and lst[0]["online"] and lst[0]["want"]["on"] is False and lst[0]["state"] is None
    assert admin.get("/api/rooms/99999/overlays").status_code == 404

    # The backup PC turns the main PC's overlay on (its own node token is enough).
    with client.websocket_connect(f"/ws?topics=room:{hd}", headers=main) as ws:
        assert ws.receive_json()["type"] == "hello"
        r = client.put(f"/api/fleet/nodes/{main_id}/overlay", headers=backup, json={"on": True, "position": "bottom-bar", "size": "large"})
        assert r.status_code == 200, r.text
        evt = ws.receive_json()
        assert evt["type"] == "overlay.changed" and evt["data"] == {"id": main_id, "room_id": hd}
    cmds = admin.get("/api/nodes/commands?kinds=overlay", headers=main).json()
    assert len(cmds) == 1 and cmds[0]["kind"] == "overlay"
    assert cmds[0]["payload"] == {"on": True, "url": "", "position": "bottom-bar", "size": "large", "display": 0,
                                  "opacity": 0.85, "room_id": hd, "by": "Ben"}
    assert admin.get("/api/nodes/commands", headers=main).json()[0]["kind"] == "overlay"  # the page's unfiltered poll gets it too
    assert admin.get("/api/nodes/commands?kinds=set_url,reload", headers=main).json() == []

    # Companion (API key) changes just the URL; everything else is kept, and the older command is dropped.
    key = admin.post("/api/admin/api-keys", json={"name": "Companion"}).json()["key"]
    r = client.put(f"/api/fleet/nodes/{main_id}/overlay", headers={"X-API-Key": key}, json={"url": "https://ontime.local/timer"})
    assert r.status_code == 200 and r.json()["want"]["position"] == "bottom-bar" and r.json()["want"]["on"] is True
    cmds = admin.get("/api/nodes/commands?kinds=overlay", headers=main).json()
    assert len(cmds) == 1 and cmds[0]["payload"]["url"] == "https://ontime.local/timer"

    # Bad input.
    put = lambda body, nid=main_id: client.put(f"/api/fleet/nodes/{nid}/overlay", headers={"X-API-Key": key}, json=body)
    assert put({"url": "file:///etc/passwd"}).status_code == 400
    assert put({"url": "javascript:alert(1)"}).status_code == 400
    assert put({"position": "middle"}).status_code == 422
    assert put({"opacity": 0.01}).status_code == 422
    assert put({"on": True}, kiosk_id).status_code == 400  # a kiosk has no overlay
    assert put({"on": True}, 99999).status_code == 404

    # The app reports what it shows; the room list has both.
    assert client.put("/api/nodes/me/overlay", json={"on": True}).status_code in (401, 403)  # people can't report for a laptop
    rep = {"on": True, "url": "https://ontime.local/timer", "target": "https://ontime.local/timer", "position": "bottom-bar",
           "size": "large", "display": 0, "opacity": 0.85, "room_id": hd}
    assert client.put("/api/nodes/me/overlay", headers=main, json=rep).status_code == 200
    # A heartbeat doesn't wipe it.
    admin.post("/api/nodes/heartbeat", headers=main, json={"info": {"screen": "1920x1080"}})
    mine = admin.get("/api/nodes/me/overlay", headers=main).json()
    assert mine["state"]["on"] is True and mine["state"]["target"] == "https://ontime.local/timer"
    assert mine["want"]["url"] == "https://ontime.local/timer"
    assert admin.get(f"/api/fleet/nodes/{main_id}/overlay").json()["state"]["position"] == "bottom-bar"

    # Turning it off queues an "off" command.
    assert put({"on": False}).json()["want"]["on"] is False
    assert admin.get("/api/nodes/commands?kinds=overlay", headers=main).json()[0]["payload"]["on"] is False
    # Acked like any other command.
    cid = admin.get("/api/nodes/commands?kinds=overlay", headers=main).json()[0]["id"]
    assert admin.post(f"/api/nodes/commands/{cid}/ack", headers=main, json={"ok": True}).status_code == 200
    assert admin.get("/api/nodes/commands?kinds=overlay", headers=main).json() == []


def test_overlay_site_scoping(admin, client):
    code = admin.get("/api/fleet/enrolment").json()[0]["enrol_code"]
    hd = {r["name"]: r["id"] for r in admin.get("/api/bootstrap").json()["rooms"]}["HD"]
    node_id, h = _laptop(admin, code, "hd-main")
    admin.post("/api/nodes/me/start", headers=h, json={"operator": "Amy", "room_id": hd, "mode": "main"})
    # A tech at another site can neither see nor control this laptop.
    from atsuit import db

    admin.post("/api/admin/accounts", json={"username": "zed", "password": "password1", "role": "tech"})
    with db.tx() as c:
        from atsuit.modules.core import create_site

        site2 = create_site(c, "Other Venue", "UTC")
        c.execute("UPDATE accounts SET site_id=? WHERE username='zed'", (site2,))
    client.post("/api/auth/login", json={"username": "zed", "password": "password1"})
    assert client.put(f"/api/fleet/nodes/{node_id}/overlay", json={"on": True}).status_code == 404
    assert client.get(f"/api/fleet/nodes/{node_id}/overlay").status_code == 404
    assert client.get(f"/api/rooms/{hd}/overlays").status_code == 404
    # Viewers can't control overlays at all.
    client.post("/api/auth/login", json={"username": "admin", "password": "correct-horse"})
    client.post("/api/admin/accounts", json={"username": "vic", "password": "password1", "role": "viewer"})
    client.post("/api/auth/login", json={"username": "vic", "password": "password1"})
    assert client.put(f"/api/fleet/nodes/{node_id}/overlay", json={"on": True}).status_code == 403
