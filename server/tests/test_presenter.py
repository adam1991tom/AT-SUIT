"""Presenter module: events, sessions, the presenter portal, review, show
files, schedule import, room sync and sending a room's sessions to its timer."""
import hashlib
import io


def rooms(c):
    return {r["name"]: r["id"] for r in c.get("/api/bootstrap").json()["rooms"]}


def make_event(admin):
    r = admin.post("/api/presenter/events", json={"name": "Derm Conf 2026", "starts_on": "2026-10-12", "status": "live"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_running_order_portal_and_review(admin, client):
    eid, rid = make_event(admin), rooms(admin)["HD"]
    s = admin.post("/api/presenter/sessions", json={"event_id": eid, "room_id": rid, "title": "Keynote",
                                                    "starts_at": "2026-10-12T09:30", "ends_at": "2026-10-12T10:15"}).json()
    pr = admin.post("/api/presenter/presenters", json={"event_id": eid, "session_id": s["id"], "full_name": "Dr Ada Lovelace",
                                                       "email": "ada@example.com"}).json()
    assert pr["email"] == "ada@example.com" and len(pr["token"]) >= 30
    # The presenter's own page: no sign-in, only their link.
    client.cookies.clear()
    page = client.get(f"/api/present/{pr['token']}").json()
    assert page["full_name"] == "Dr Ada Lovelace" and page["session"]["room"] == "HD" and page["files"] == []
    assert client.get("/api/present/not-a-real-token-at-all-xx").status_code == 404
    deck = b"PK\x03\x04 fake pptx" * 100
    r = client.post(f"/api/present/{pr['token']}/upload", files={"file": ("Keynote final.pptx", deck)})
    assert r.status_code == 200 and r.json()["files"][0]["review_status"] == "pending"
    assert client.post(f"/api/present/{pr['token']}/checkin").json()["checked_in_at"]
    assert client.get("/api/presenter/events").status_code == 401  # the portal is all a presenter can reach
    # The AV team sees it and approves.
    admin.post("/api/auth/login", json={"username": "admin", "password": "correct-horse"})
    ev = admin.get(f"/api/presenter/events/{eid}").json()
    sess = ev["sessions"][0]
    assert sess["ready"] == {"presenters": 1, "checked_in": 1, "uploaded": 1, "approved": 0, "rejected": 0, "pending": 1, "show_files": 0}
    queue = admin.get(f"/api/presenter/events/{eid}/review").json()
    fid = queue[0]["id"]
    assert queue[0]["version"] == 1 and queue[0]["is_latest"] and queue[0]["room_name"] == "HD"
    assert admin.get(f"/api/presenter/files/{fid}").content == deck
    admin.put(f"/api/presenter/files/{fid}/review", json={"status": "rejected", "note": "Wrong aspect ratio"})
    client.cookies.clear()
    assert client.get(f"/api/present/{pr['token']}").json()["files"][0]["review_note"] == "Wrong aspect ratio"
    client.post(f"/api/present/{pr['token']}/upload", files={"file": ("Keynote v2.pptx", deck + b"2")})
    admin.post("/api/auth/login", json={"username": "admin", "password": "correct-horse"})
    queue = admin.get(f"/api/presenter/events/{eid}/review").json()
    assert [q["version"] for q in queue] == [2, 1] and queue[0]["is_latest"] and not queue[1]["is_latest"]
    admin.put(f"/api/presenter/files/{queue[0]['id']}/review", json={"status": "approved"})
    csv_text = admin.get(f"/api/presenter/events/{eid}/schedule.csv").text
    assert "Keynote,Dr Ada Lovelace,yes,Keynote v2.pptx,approved" in csv_text
    # A new link stops the old one.
    old = pr["token"]
    admin.post(f"/api/presenter/presenters/{pr['id']}/new-link")
    assert admin.get(f"/api/present/{old}").status_code == 404


def test_show_files_room_sync_and_timer(admin, client):
    eid, rid = make_event(admin), rooms(admin)["CC"]
    s1 = admin.post("/api/presenter/sessions", json={"event_id": eid, "room_id": rid, "title": "Welcome",
                                                     "starts_at": "2026-10-12T09:00", "ends_at": "2026-10-12T09:20"}).json()
    s2 = admin.post("/api/presenter/sessions", json={"event_id": eid, "room_id": rid, "title": "Panel",
                                                     "starts_at": "2026-10-12T09:20", "ends_at": "2026-10-12T10:00"}).json()
    pr = admin.post("/api/presenter/presenters", json={"event_id": eid, "session_id": s1["id"], "full_name": "Sam"}).json()
    admin.post(f"/api/presenter/presenters/{pr['id']}/files", files={"file": ("welcome.pdf", b"%PDF-1.7 hi")})
    fid = admin.get(f"/api/presenter/events/{eid}/review").json()[0]["id"]
    for name in ("walk-in.mp4", "sting.mp3"):
        assert admin.post(f"/api/presenter/sessions/{s2['id']}/show-files?kind=video", files={"file": (name, name.encode())}).status_code == 200
    show = admin.get(f"/api/presenter/rooms/{rid}/schedule").json()[1]["show_files"]
    assert [f["original_name"] for f in show] == ["walk-in.mp4", "sting.mp3"]
    admin.post(f"/api/presenter/sessions/{s2['id']}/show-files/order", json={"ids": [show[1]["id"], show[0]["id"]]})
    assert admin.get(f"/api/presenter/rooms/{rid}/schedule").json()[1]["show_files"][0]["original_name"] == "sting.mp3"

    code = admin.post(f"/api/presenter/rooms/{rid}/sync-code").json()["sync_code"]
    client.cookies.clear()
    m = client.get(f"/api/presenter/sync/{code}").json()
    assert m["room_name"] == "CC" and m["files"] == []  # nothing approved yet
    assert len(m["show_files"]) == 2
    assert client.get(f"/api/presenter/sync/{code}/files/{fid}").status_code == 404
    assert client.get("/api/presenter/sync/WRONG-CODE").status_code == 404
    admin.post("/api/auth/login", json={"username": "admin", "password": "correct-horse"})
    admin.put(f"/api/presenter/files/{fid}/review", json={"status": "approved"})
    client.cookies.clear()
    m = client.get(f"/api/presenter/sync/{code.lower()}").json()
    assert m["files"][0]["sha256"] == hashlib.sha256(b"%PDF-1.7 hi").hexdigest()
    assert client.get(f"/api/presenter/sync/{code}/files/{fid}").content == b"%PDF-1.7 hi"
    # Another room's code can't fetch this room's files.
    admin.post("/api/auth/login", json={"username": "admin", "password": "correct-horse"})
    other = admin.post(f"/api/presenter/rooms/{rooms(admin)['RH']}/sync-code").json()["sync_code"]
    assert client.get(f"/api/presenter/sync/{other}/files/{fid}").status_code == 404

    # A node in the room syncs with its own token.
    code_e = admin.get("/api/fleet/enrolment").json()[0]["enrol_code"]
    tok = admin.post("/api/nodes/enrol", json={"code": code_e, "name": "cc-pc", "kind": "tech"}).json()["token"]
    hdr = {"Authorization": f"Node {tok}"}
    assert admin.get("/api/presenter/sync", headers=hdr).status_code == 409  # no room picked today
    admin.put("/api/nodes/me/room", headers=hdr, json={"room_id": rid})
    assert admin.get("/api/presenter/sync", headers=hdr).json()["room_name"] == "CC"
    assert admin.get(f"/api/presenter/sync-node/files/{fid}", headers=hdr).content == b"%PDF-1.7 hi"

    # The day's sessions become the room's timer cues.
    r = admin.post(f"/api/presenter/rooms/{rid}/to-timer", json={"day": "2026-10-12"})
    assert r.json() == {"cues": 2}
    cues = admin.get(f"/api/timers/{rid}/cues").json()
    assert [(q["title"], q["time_start"], q["duration_ms"], q["note"]) for q in cues] == [
        ("Welcome", "09:00", 20 * 60000, "Sam"), ("Panel", "09:20", 40 * 60000, "")]
    assert admin.post(f"/api/presenter/rooms/{rid}/to-timer", json={"day": "2026-01-01"}).status_code == 400


def test_schedule_import_from_spreadsheet(admin):
    from openpyxl import Workbook

    eid = make_event(admin)
    wb = Workbook()
    ws = wb.active
    ws.append(["Harrogate running order"])
    ws.append([])
    ws.append(["Date", "Room", "Time", "Session", "Speaker", "Email"])
    ws.append(["Day 1", "HD", "09:00 - 09:45", "Opening", "Jo Bloggs; Ann Lee", "jo@example.com"])
    ws.append(["Day 2", "Studio 9", "2:15pm - 3pm", "Workshop", "", ""])
    buf = io.BytesIO()
    wb.save(buf)
    r = admin.post(f"/api/presenter/events/{eid}/import", files={"file": ("order.xlsx", buf.getvalue())})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["method"] == "table"
    assert [(x["room_name"], x["starts_at"], x["ends_at"], x["title"], x["room_known"]) for x in body["rows"]] == [
        ("HD", "2026-10-12T09:00", "2026-10-12T09:45", "Opening", True),
        ("Studio 9", "2026-10-13T14:15", "2026-10-13T15:00", "Workshop", False)]
    done = admin.post(f"/api/presenter/imports/{body['id']}/commit", json={"rows": body["rows"]}).json()
    assert done == {"sessions": 2, "unknown_rooms": ["Studio 9"]}
    ev = admin.get(f"/api/presenter/events/{eid}").json()
    assert [p["full_name"] for p in ev["sessions"][0]["presenters"]] == ["Jo Bloggs", "Ann Lee"]
    assert admin.post(f"/api/presenter/imports/{body['id']}/commit", json={"rows": []}).status_code == 409
    # CSV works the same; a PDF needs the AI, which isn't set up here.
    csv_data = b"Room,Start,End,Title,Presenter\nCC,2026-10-12 11:00,2026-10-12 11:30,Lunch talk,Max\n"
    rows = admin.post(f"/api/presenter/events/{eid}/import", files={"file": ("o.csv", csv_data)}).json()["rows"]
    assert rows[0]["starts_at"] == "2026-10-12T11:00" and rows[0]["presenter_name"] == "Max"
    r = admin.post(f"/api/presenter/events/{eid}/import", files={"file": ("o.pdf", b"%PDF-1.4 nothing")})
    assert r.status_code == 422


def test_presenter_is_scoped(admin, client):
    eid = make_event(admin)
    admin.post("/api/admin/accounts", json={"username": "tech1", "password": "long-password-1", "display_name": "Tech", "role": "tech"})
    client.cookies.clear()
    client.post("/api/auth/login", json={"username": "tech1", "password": "long-password-1"})
    assert client.get(f"/api/presenter/events/{eid}").status_code == 200
    assert client.post("/api/presenter/events", json={"name": "x"}).status_code == 403  # events are for admins
    assert client.delete(f"/api/presenter/events/{eid}").status_code == 403
    assert client.get("/api/presenter/settings").status_code == 403
