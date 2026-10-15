"""Show reports, help calls that nobody answers, and the pre-show check."""
import time

from pypdf import PdfReader
import io


def _rooms(admin):
    return {r["name"]: r["id"] for r in admin.get("/api/bootstrap").json()["rooms"]}


def test_show_report(admin, client, tmp_path):
    hd = _rooms(admin)["HD"]
    # A cue runs over, a quick timer finishes early, a stage message, a help call answered.
    q = admin.post(f"/api/timers/{hd}/cues", json={"cue": "1", "title": "Keynote: Priya Shah", "duration_ms": 1000}).json()
    assert admin.post(f"/api/timers/{hd}/load", json={"cue_id": q["id"]}).status_code == 200
    assert admin.post(f"/api/timers/{hd}/start").status_code == 200
    assert admin.post(f"/api/timers/{hd}/message/show", json={"text": "Wrap up please"}).status_code == 200
    time.sleep(1.4)
    assert admin.post(f"/api/timers/{hd}/stop").status_code == 200
    assert admin.post(f"/api/timers/{hd}/preset/10").status_code == 200
    assert admin.post(f"/api/timers/{hd}/add", json={"delta_ms": 60000}).status_code == 200
    assert admin.post(f"/api/timers/{hd}/stop").status_code == 200
    h = admin.post("/api/comms/help", json={"room_id": hd, "category": "Audio", "description": "Lectern mic is dead"}).json()
    assert admin.put(f"/api/comms/help/{h['id']}", json={"status": "acknowledged"}).status_code == 200
    assert admin.put(f"/api/comms/help/{h['id']}", json={"status": "resolved"}).status_code == 200

    d = admin.get(f"/api/reports/{hd}").json()
    assert d["room"] == "HD"
    runs = d["runs"]
    assert [r["title"] for r in runs] == ["Keynote: Priya Shah", ""]
    assert runs[0]["over_ms"] > 0 and runs[0]["cue"] == "1"
    assert runs[1]["over_ms"] == 0 and runs[1]["early_ms"] > 600000 and runs[1]["added_ms"] == 60000
    assert d["summary"]["over"] == 1 and d["summary"]["help"] == 1 and d["summary"]["answer_s"] is not None
    assert [m["text"] for m in d["stage_messages"]] == ["Wrap up please"]

    r = admin.get(f"/api/reports/{hd}/pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    text = "".join(pg.extract_text() for pg in PdfReader(io.BytesIO(r.content)).pages)
    assert "Keynote: Priya Shah" in text and "Lectern mic is dead" in text and "Wrap up please" in text
    (tmp_path / "report.pdf").write_bytes(r.content)

    # Kept: saved by hand, listed, downloadable; and made by itself for the day before.
    saved = admin.post(f"/api/reports/{hd}/save").json()
    lst = admin.get("/api/reports").json()["reports"]
    assert [x["id"] for x in lst] == [saved["id"]]
    assert admin.get(f"/api/reports/saved/{saved['id']}.pdf").content[:4] == b"%PDF"
    from atsuit.modules import reports
    assert reports.save_due(time.time() + 86400) == []  # already kept
    assert admin.delete(f"/api/reports/saved/{saved['id']}").status_code == 200
    assert admin.get("/api/reports").json()["reports"] == []
    auto = reports.save_due(time.time() + 86400)  # the next morning: only HD had a show
    assert [x["room_id"] for x in auto] == [hd]
    assert [x["room_name"] for x in admin.get("/api/reports").json()["reports"]] == ["HD"]

    # Techs can't read reports.
    admin.post("/api/admin/accounts", json={"username": "tom", "password": "tom-password", "display_name": "Tom", "role": "tech"})
    from fastapi.testclient import TestClient
    tech = TestClient(client.app)
    tech.post("/api/auth/login", json={"username": "tom", "password": "tom-password"})
    assert tech.get(f"/api/reports/{hd}").status_code == 403


def test_help_escalation(admin):
    import asyncio

    from atsuit.modules import comms

    hd = _rooms(admin)["HD"]
    h = admin.post("/api/comms/help", json={"room_id": hd, "category": "Video", "description": "No picture"}).json()
    now = time.time()
    assert asyncio.run(comms.escalate(now + 30)) == []
    first = asyncio.run(comms.escalate(now + 125))
    assert [x["id"] for x in first] == [h["id"]] and first[0]["escalations"] == 1
    assert asyncio.run(comms.escalate(now + 130)) == []  # once per period
    assert asyncio.run(comms.escalate(now + 9999))[0]["escalations"] == 3  # three times at most
    assert asyncio.run(comms.escalate(now + 99999)) == []
    assert admin.get(f"/api/comms/help/board/{hd}").json()["calls"][0]["escalations"] == 3
    # An answered call isn't sent again, and 0 minutes turns it off.
    h2 = admin.post("/api/comms/help", json={"room_id": hd, "category": "Audio", "description": "Buzz"}).json()
    admin.put(f"/api/comms/help/{h2['id']}", json={"status": "acknowledged"})
    assert asyncio.run(comms.escalate(now + 9999)) == []
    assert admin.put("/api/admin/settings", json={"help_escalate_minutes": 0}).status_code == 200
    admin.post("/api/comms/help", json={"room_id": hd, "category": "Lights", "description": "Dark"})
    assert asyncio.run(comms.escalate(now + 9999)) == []
    assert admin.get("/api/admin/settings").json()["help_escalate_minutes"] == 0


def test_preshow_check(admin, client):
    from fastapi.testclient import TestClient

    rooms = _rooms(admin)
    hd, cc = rooms["HD"], rooms["CC"]
    code = admin.get("/api/fleet/enrolment").json()[0]["enrol_code"]
    lap, scr, idle = TestClient(client.app), TestClient(client.app), TestClient(client.app)
    a = lap.post("/api/nodes/enrol", json={"code": code, "name": "hd-main", "kind": "tech"}).json()
    k = scr.post("/api/nodes/enrol", json={"code": code, "name": "hd-stage", "kind": "kiosk"}).json()
    idle.post("/api/nodes/enrol", json={"code": code, "name": "spare", "kind": "tech"})
    ha, hk = {"Authorization": f"Node {a['token']}"}, {"Authorization": f"Node {k['token']}"}
    assert lap.post("/api/nodes/me/start", headers=ha, json={"operator": "Amy", "room_id": hd, "mode": "main"}).status_code == 200
    lap.post("/api/nodes/heartbeat", headers=ha, json={"version": "1.0.7"})
    scr.post("/api/nodes/heartbeat", headers=hk, json={})
    assert scr.put("/api/nodes/me/screen", headers=hk, json={"room_id": hd, "view": ""}).status_code == 200
    admin.put(f"/api/captions/rooms/{cc}", json={"enabled": False})

    d = admin.get("/api/preshow").json()
    room = {r["name"]: {i["name"]: i for i in r["items"]} for r in d["rooms"]}
    assert room["HD"]["HD-MAIN"]["state"] == "good" and "Amy" in room["HD"]["HD-MAIN"]["detail"]
    assert room["HD"]["HD-STAGE"]["state"] == "warn"  # on, but showing nothing
    assert room["HD"]["Caption mic"]["state"] == "bad"  # not live
    assert room["CC"]["Caption mic"]["state"] == "off"
    assert [i["name"] for i in d["unplaced"]] == ["SPARE"] and d["unplaced"][0]["state"] == "off"  # not in use
    scr.put("/api/nodes/me/screen", headers=hk, json={"room_id": hd, "view": "hcc"})
    d = admin.get("/api/preshow").json()
    assert {i["name"]: i for i in d["rooms"][[r["name"] for r in d["rooms"]].index("HD")]["items"]}["HD-STAGE"]["detail"] == "Standard"
    assert d["counts"]["good"] == 2
