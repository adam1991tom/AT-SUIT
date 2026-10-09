"""Updates from GitHub releases: the server checks, update.sh --auto asks it what to do."""
import httpx

from atsuit import VERSION, db, updates


def release(version, assets=()):
    return {"version": version, "tag": f"v{version}", "name": f"v{version}", "url": "https://github.com/x/y/releases/tag/v" + version,
            "published_at": "2026-10-09T12:00:00Z", "notes": "Fixes", "assets": list(assets)}


def bump(v, by=1):
    a, b, c = (int(x) for x in v.split("."))
    return f"{a}.{b}.{c + by}"


def plan():
    return updates.plan()


def test_versions_compare_as_numbers():
    assert updates.newer("1.0.10", "1.0.9") and updates.newer("v2.0.0", "1.9.9")
    assert not updates.newer("1.0.2", "v1.0.2") and not updates.newer("nightly", "1.0.0")


def test_settings_and_check(admin, monkeypatch):
    st = admin.get("/api/admin/updates").json()
    assert st["current"] == VERSION and st["mode"] == "auto" and st["repo"] == "adam1991tom/AT-SUIT" and not st["has_token"]
    assert st["latest"] is None and not st["available"] and not st["host_updater"]
    seen = {}

    def fake(repo, token):
        seen.update(repo=repo, token=token)
        return release(bump(VERSION))
    monkeypatch.setattr(updates, "fetch_latest", fake)
    assert admin.put("/api/admin/updates", json={"token": "not a token!"}).status_code == 400
    assert admin.put("/api/admin/updates", json={"mode": "sometimes"}).status_code == 400
    st = admin.put("/api/admin/updates", json={"mode": "notify", "repo": "https://github.com/acme/at-suit/", "token": "ghp_abc123"}).json()
    assert st["mode"] == "notify" and st["repo"] == "acme/at-suit" and st["has_token"]
    st = admin.post("/api/admin/updates/check").json()
    assert seen == {"repo": "acme/at-suit", "token": "ghp_abc123"}
    assert st["available"] and st["latest"]["version"] == bump(VERSION) and st["error"] == ""
    assert "ghp_abc123" not in admin.get("/api/admin/updates").text  # the token never comes back

    def down(repo, token):
        raise RuntimeError("GitHub refused (401). Check the token, or try again later.")
    monkeypatch.setattr(updates, "fetch_latest", down)
    st = admin.post("/api/admin/updates/check").json()
    assert "401" in st["error"] and st["latest"]["version"] == bump(VERSION)  # what it knew stays
    # laptops look on GitHub only while updates are on
    assert admin.get("/api/nodes/app").json()["github"] == "acme/at-suit"
    admin.put("/api/admin/updates", json={"mode": "off", "token": ""})
    assert admin.get("/api/nodes/app").json()["github"] is None
    assert not admin.get("/api/admin/updates").json()["has_token"]
    admin.post("/api/auth/logout")
    assert admin.get("/api/admin/updates").status_code in (401, 403)


def test_the_host_updater_installs_only_when_it_should(admin, monkeypatch):
    new = bump(VERSION)
    monkeypatch.setattr(updates, "fetch_latest", lambda repo, token: release(new))
    assert plan()["ACTION"] == "none"  # nothing known yet
    admin.post("/api/admin/updates/check")
    p = plan()
    assert p == {"ACTION": "install", "VERSION": new, "TAG": f"v{new}", "REPO": "adam1991tom/AT-SUIT", "TOKEN": ""}
    assert admin.get("/api/admin/updates").json()["host_updater"]

    # never during a show: a running or paused timer holds it
    rid = admin.get("/api/bootstrap").json()["rooms"][0]["id"]
    admin.post(f"/api/timers/{rid}/set", json={"duration_ms": 60000, "title": "Keynote"})
    admin.post(f"/api/timers/{rid}/start")
    assert plan()["ACTION"] == "none" and "show is on" in plan()["REASON"]
    admin.post(f"/api/timers/{rid}/pause")
    assert plan()["ACTION"] == "none"
    admin.post(f"/api/timers/{rid}/reset")
    assert plan()["ACTION"] == "install"

    # notify: waits for Install now
    admin.put("/api/admin/updates", json={"mode": "notify"})
    assert plan()["ACTION"] == "none" and "Install now" in plan()["REASON"]
    st = admin.post("/api/admin/updates/install").json()
    assert st["requested"] == new and plan()["ACTION"] == "install"

    # a failed install isn't retried by itself, but Install now tries again
    updates.report("failed", new, "didn't come up healthy")
    st = admin.get("/api/admin/updates").json()
    assert st["failed"] == [new] and st["requested"] is None and st["last_result"]["result"] == "failed"
    admin.put("/api/admin/updates", json={"mode": "auto"})
    assert plan()["ACTION"] == "none" and "failed" in plan()["REASON"]
    admin.post("/api/admin/updates/install")
    assert plan()["ACTION"] == "install"
    updates.report("ok", new, "from " + VERSION)
    st = admin.get("/api/admin/updates").json()
    assert st["failed"] == [] and st["last_result"]["result"] == "ok"

    # off: nothing, unless asked
    admin.put("/api/admin/updates", json={"mode": "off"})
    assert plan()["ACTION"] == "none"
    # up to date: nothing to install, and Install now says so
    monkeypatch.setattr(updates, "fetch_latest", lambda repo, token: release(VERSION))
    admin.put("/api/admin/updates", json={"mode": "auto"})
    admin.post("/api/admin/updates/check")
    assert plan()["ACTION"] == "none" and "up to date" in plan()["REASON"]
    assert admin.post("/api/admin/updates/install").status_code == 400


def test_the_server_fetches_its_own_versions_windows_app(admin, monkeypatch):
    exe = f"AT-SUIT-Node-Setup-{VERSION}.exe"
    yml = f"version: {VERSION}\nfiles:\n  - url: {exe}\npath: {exe}\nsha512: abc\n".encode()
    files = {"latest.yml": yml, exe: b"MZ installer", f"{exe}.blockmap": b"blockmap"}
    assets = [{"name": n, "url": f"https://api.github.com/assets/{n}", "size": len(b)} for n, b in files.items()]

    def handler(request):
        assert request.headers["accept"] == "application/octet-stream"
        return httpx.Response(200, content=files[request.url.path.rsplit("/", 1)[1]])
    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    # a newer release than this server: not yet (laptops get the app that matches their server)
    monkeypatch.setattr(updates, "fetch_latest", lambda repo, token: release(bump(VERSION), assets))
    assert admin.post("/api/admin/updates/check").json()["app"] is None
    # once the server runs that release, its app is fetched and published for the laptops
    monkeypatch.setattr(updates, "fetch_latest", lambda repo, token: release(VERSION, assets))
    st = admin.post("/api/admin/updates/check").json()
    assert st["app"] == VERSION and st["app_error"] == ""
    assert admin.get("/api/nodes/app/latest.yml").content == yml
    assert admin.get(f"/api/nodes/app/{exe}").content == b"MZ installer"
    assert admin.get("/api/nodes/app").json()["version"] == VERSION
