"""Handover notes: a tech leaves a note for the room, the next shift sees it until someone ticks it off."""


def _laptop(admin, code, name):
    n = admin.post("/api/nodes/enrol", json={"code": code, "name": name, "kind": "tech"}).json()
    return {"Authorization": f"Node {n['token']}"}


def test_handover_notes(admin, client):
    code = admin.get("/api/fleet/enrolment").json()[0]["enrol_code"]
    rooms = {r["name"]: r["id"] for r in admin.get("/api/bootstrap").json()["rooms"]}
    hd, cc = rooms["HD"], rooms["CC"]
    day = _laptop(admin, code, "hd-main")
    other = _laptop(admin, code, "cc-main")
    admin.post("/api/nodes/me/start", headers=day, json={"operator": "Amy", "room_id": hd, "mode": "main"})
    admin.post("/api/nodes/me/start", headers=other, json={"operator": "Cat", "room_id": cc, "mode": "main"})

    assert client.get(f"/api/rooms/{hd}/notes", headers=day).json() == {"open": [], "done": []}

    # Amy leaves two notes; the room hears about it.
    with client.websocket_connect(f"/ws?topics=room:{hd}", headers=day) as ws:
        assert ws.receive_json()["type"] == "hello"
        r = client.post(f"/api/rooms/{hd}/notes", headers=day, json={"body": "  Clicker 2 needs batteries  "})
        assert r.status_code == 200, r.text
        assert ws.receive_json() == {"topic": f"room:{hd}", "type": "notes.changed", "data": {"room_id": hd}}
    first = r.json()
    assert first["body"] == "Clicker 2 needs batteries" and first["author"] == "Amy" and not first["done"]
    pin = client.post(f"/api/rooms/{hd}/notes", headers=day, json={"body": "Lectern mic is channel 4", "pinned": True}).json()
    assert client.post(f"/api/rooms/{hd}/notes", headers=day, json={"body": "   "}).status_code in (400, 422)

    # Stored encrypted, and listed pinned first, then newest.
    from atsuit import db
    with db.ro() as c:
        assert "Clicker" not in c.execute("SELECT body_enc FROM room_notes WHERE id=?", (first["id"],)).fetchone()[0]
    notes = admin.get(f"/api/rooms/{hd}/notes").json()
    assert [n["id"] for n in notes["open"]] == [pin["id"], first["id"]]

    # A laptop in another room can't read or change this room's notes.
    assert client.get(f"/api/rooms/{hd}/notes", headers=other).status_code == 403
    assert client.put(f"/api/notes/{first['id']}", headers=other, json={"done": True}).status_code == 403
    assert client.post(f"/api/rooms/{hd}/notes", headers=other, json={"body": "hi"}).status_code == 403

    # The next shift (an account, or the same laptop with a new tech) ticks one off; it moves to done.
    r = admin.put(f"/api/notes/{first['id']}", json={"done": True})
    assert r.status_code == 200 and r.json()["done"] and r.json()["done_by"] == "admin"
    notes = client.get(f"/api/rooms/{hd}/notes", headers=day).json()
    assert [n["id"] for n in notes["open"]] == [pin["id"]] and [n["id"] for n in notes["done"]] == [first["id"]]
    # Undone by mistake: back again.
    admin.put(f"/api/notes/{first['id']}", json={"done": False})
    assert len(client.get(f"/api/rooms/{hd}/notes", headers=day).json()["open"]) == 2

    # Edit and unpin.
    r = client.put(f"/api/notes/{pin['id']}", headers=day, json={"body": "Lectern mic is channel 5", "pinned": False}).json()
    assert r["body"] == "Lectern mic is channel 5" and not r["pinned"] and r["edited_at"]

    # Delete, with an audit entry that doesn't repeat the note.
    assert client.delete(f"/api/notes/{pin['id']}", headers=day).json() == {"ok": True}
    assert client.delete(f"/api/notes/{pin['id']}", headers=day).status_code == 404
    with db.ro() as c:
        a = c.execute("SELECT * FROM audit_log WHERE action='note.delete'").fetchone()
    assert a and "Lectern" not in a["detail"]
    assert admin.get("/api/rooms/99999/notes").status_code == 404

    # Signed out, nothing.
    client.cookies.clear()
    assert client.get(f"/api/rooms/{hd}/notes").status_code == 401
