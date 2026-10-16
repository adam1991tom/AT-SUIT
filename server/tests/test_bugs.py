"""Report a bug: reports go to the console's Bug reports page, not to an email app, and to GitHub
only when an admin sets that up."""
import io

import pytest

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64


@pytest.fixture(autouse=True)
def fresh_limits():
    from atsuit.modules import bugs
    bugs._recent.clear()
    yield
    bugs._recent.clear()


def _laptop(admin, name="hd-main", operator="Amy", room="HD"):
    code = admin.get("/api/fleet/enrolment").json()[0]["enrol_code"]
    n = admin.post("/api/nodes/enrol", json={"code": code, "name": name, "kind": "tech"}).json()
    h = {"Authorization": f"Node {n['token']}"}
    rooms = {r["name"]: r["id"] for r in admin.get("/api/bootstrap").json()["rooms"]}
    admin.post("/api/nodes/me/start", headers=h, json={"operator": operator, "room_id": rooms[room], "mode": "main"})
    return h


def test_report_reaches_the_console(admin, client):
    day = _laptop(admin)
    # Admins hear about it as it arrives.
    with client.websocket_connect("/ws?topics=admins") as ws:
        assert "admins" in ws.receive_json()["topics"]
        r = client.post("/api/bugs", headers=day, data={"title": "  Stage timer froze  ", "description": "Pressed start twice",
                                                        "page": "/node#timer", "user_agent": "AT-SUITE-App/1.10.4", "app_version": "1.10.4"},
                        files={"screenshot": ("shot.png", io.BytesIO(PNG), "image/png")})
        assert r.status_code == 200, r.text
        evt = ws.receive_json()
    assert evt["type"] == "bug.new" and evt["data"]["title"] == "Stage timer froze" and evt["data"]["room_name"] == "HD"
    rid = r.json()["id"]

    lst = admin.get("/api/admin/bugs").json()
    assert lst["open"] == 1 and lst["settings"]["github_ready"] is False
    rep = lst["reports"][0]
    assert rep["title"] == "Stage timer froze" and rep["reporter"] == "Amy" and rep["reporter_kind"] == "node"
    assert rep["has_screenshot"] and rep["status"] == "open" and rep["github_url"] is None

    full = admin.get(f"/api/admin/bugs/{rid}").json()
    assert full["description"] == "Pressed start twice" and full["page"] == "/node#timer" and full["app_version"] == "1.10.4"
    from atsuit import VERSION
    assert full["system"]["version"] == VERSION and "schema" in full["system"]
    shot = admin.get(f"/api/admin/bugs/{rid}/screenshot")
    assert shot.status_code == 200 and shot.headers["content-type"] == "image/png" and shot.content == PNG

    # Stored encrypted.
    from atsuit import db
    with db.ro() as c:
        row = c.execute("SELECT title_enc, description_enc FROM bug_reports WHERE id=?", (rid,)).fetchone()
    assert "froze" not in row[0] and "twice" not in row[1]

    # Resolved, opened again, then deleted with its screenshot.
    r = admin.put(f"/api/admin/bugs/{rid}", json={"status": "resolved"})
    assert r.json()["status"] == "resolved" and r.json()["resolved_by"] == "admin"
    assert admin.get("/api/admin/bugs").json()["open"] == 0
    assert admin.get("/api/admin/bugs?status=open").json()["reports"] == []
    assert admin.put(f"/api/admin/bugs/{rid}", json={"status": "open"}).json()["resolved_at"] is None
    assert admin.put(f"/api/admin/bugs/{rid}", json={"status": "gone"}).status_code == 422
    from atsuit import config
    assert len(list(config.cfg.uploads.glob("bug-*"))) == 1
    assert admin.delete(f"/api/admin/bugs/{rid}").status_code == 200
    assert admin.get(f"/api/admin/bugs/{rid}").status_code == 404
    assert not list(config.cfg.uploads.glob("bug-*"))


def test_who_can_report_and_read(admin, client):
    day = _laptop(admin)
    # A console account reports without a screenshot.
    r = admin.post("/api/bugs", data={"title": "Rooms page is slow"})
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    assert admin.get(f"/api/admin/bugs/{rid}").json()["reporter_kind"] == "account"
    # Only admins read them; nobody signed out can send one.
    for path in ("/api/admin/bugs", f"/api/admin/bugs/{rid}", "/api/admin/bugs/settings"):
        assert client.get(path, headers=day).status_code == 403
    # A title is needed, and a screenshot must really be a picture.
    assert client.post("/api/bugs", headers=day, data={"title": "   "}).status_code == 400
    assert client.post("/api/bugs", headers=day, data={"description": "no title"}).status_code == 422
    r = client.post("/api/bugs", headers=day, data={"title": "Bad shot"}, files={"screenshot": ("x.png", io.BytesIO(b"<svg onload=alert(1)>"), "image/png")})
    assert r.status_code == 400
    # An empty file field is just no screenshot.
    r = client.post("/api/bugs", headers=day, data={"title": "No shot"}, files={"screenshot": ("", io.BytesIO(b""), "application/octet-stream")})
    assert r.status_code == 200, r.text
    assert not admin.get(f"/api/admin/bugs/{r.json()['id']}").json()["has_screenshot"]
    client.cookies.clear()
    assert client.post("/api/bugs", data={"title": "x"}).status_code == 401


def test_too_many_reports(admin, client):
    from atsuit.modules import bugs
    day = _laptop(admin)
    for i in range(bugs.PER_HOUR):
        assert client.post("/api/bugs", headers=day, data={"title": f"Bug {i}"}).status_code == 200
    assert client.post("/api/bugs", headers=day, data={"title": "One more"}).status_code == 429
    # Someone else can still report.
    assert admin.post("/api/bugs", data={"title": "Mine"}).status_code == 200


def test_nothing_goes_to_github_unless_set_up(admin, monkeypatch):
    from atsuit.modules import bugs
    sent = []
    monkeypatch.setattr(bugs, "post_issue", lambda *a: sent.append(a) or "https://github.com/o/r/issues/1")
    rid = admin.post("/api/bugs", data={"title": "No GitHub"}).json()["id"]
    assert sent == []
    assert admin.post(f"/api/admin/bugs/{rid}/github").status_code == 400

    # Bad settings are refused, and the token is never shown again.
    assert admin.put("/api/admin/bugs/settings", json={"github_repo": "not a repo"}).status_code == 400
    assert admin.put("/api/admin/bugs/settings", json={"github_repo": "o/r", "token": "has spaces"}).status_code == 400
    s = admin.put("/api/admin/bugs/settings", json={"github_repo": "https://github.com/o/r", "token": "ghp_abc123", "auto_github": False}).json()
    assert s == {"github_repo": "o/r", "has_token": True, "auto_github": False, "github_ready": True}
    assert "ghp_abc123" not in admin.get("/api/admin/bugs/settings").text
    from atsuit import db
    with db.ro() as c:
        assert "ghp_abc123" not in c.execute("SELECT value FROM settings WHERE key='bug_reports'").fetchone()[0]
    # Saving without a token keeps it.
    assert admin.put("/api/admin/bugs/settings", json={"github_repo": "o/r", "auto_github": False}).json()["has_token"]


def test_send_to_github(admin, client, monkeypatch):
    from atsuit.modules import bugs
    sent = []

    def fake(repo, token, title, body):
        sent.append((repo, token, title, body))
        return f"https://github.com/{repo}/issues/{len(sent)}"
    monkeypatch.setattr(bugs, "post_issue", fake)
    admin.put("/api/admin/bugs/settings", json={"github_repo": "o/r", "token": "ghp_abc123"})
    day = _laptop(admin, operator="Amy Secretname")
    rid = client.post("/api/bugs", headers=day, data={"title": "Captions stop", "description": "After ten minutes", "user_agent": "UA/1"}).json()["id"]
    assert sent == []  # not by itself: an admin presses the button

    out = admin.post(f"/api/admin/bugs/{rid}/github").json()
    assert out["github_url"] == "https://github.com/o/r/issues/1" and out["github_error"] is None
    repo, token, title, body = sent[0]
    assert (repo, token, title) == ("o/r", "ghp_abc123", "Bug: Captions stop")
    assert "After ten minutes" in body and "Version:" in body and "UA/1" in body
    # No names, organisation or room go to GitHub.
    for private in ("Amy", "Secretname", "Test Org", "HD"):
        assert private not in body
    # Pressing it again doesn't open a second issue.
    admin.post(f"/api/admin/bugs/{rid}/github")
    assert len(sent) == 1

    # A refusal is kept on the report, and said.
    def refuse(*a):
        raise RuntimeError("GitHub refused the token.")
    monkeypatch.setattr(bugs, "post_issue", refuse)
    rid2 = admin.post("/api/bugs", data={"title": "Second"}).json()["id"]
    r = admin.post(f"/api/admin/bugs/{rid2}/github")
    assert r.status_code == 502 and "refused" in r.json()["detail"]
    assert admin.get(f"/api/admin/bugs/{rid2}").json()["github_error"] == "GitHub refused the token."


def test_auto_github_on_arrival(admin, monkeypatch):
    from atsuit.modules import bugs
    sent = []
    monkeypatch.setattr(bugs, "post_issue", lambda repo, token, title, body: sent.append(title) or "https://github.com/o/r/issues/9")
    admin.put("/api/admin/bugs/settings", json={"github_repo": "o/r", "token": "ghp_abc123", "auto_github": True})
    rid = admin.post("/api/bugs", data={"title": "Auto"}).json()["id"]
    import time
    for _ in range(50):
        if admin.get(f"/api/admin/bugs/{rid}").json()["github_url"]:
            break
        time.sleep(0.05)
    assert sent == ["Bug: Auto"]
    assert admin.get(f"/api/admin/bugs/{rid}").json()["github_url"] == "https://github.com/o/r/issues/9"
