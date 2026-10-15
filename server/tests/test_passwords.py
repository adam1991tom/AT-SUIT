"""Tech passwords: set by an admin or manager, changed at the first sign-in,
and "forgot" asks the admins and managers for a reset to the default."""


def _tech_laptop(admin):
    code = admin.get("/api/fleet/enrolment").json()[0]["enrol_code"]
    r = admin.post("/api/nodes/enrol", json={"code": code, "name": "ATLAP1", "kind": "tech"})
    return {"Authorization": f"Node {r.json()['token']}"}


def _other(client):
    return client.__class__(client.app)


def test_first_sign_in_changes_the_password(strict):
    admin = strict
    assert admin.post("/api/admin/accounts", json={"username": "ben", "display_name": "Ben", "role": "tech"}).status_code == 200
    ben = _other(admin)
    # nobody gave a password: it's the default, and that has to be changed straight away
    r = ben.post("/api/auth/login", json={"username": "ben", "password": "ChangeMe"})
    assert r.status_code == 428 and ben.get("/api/auth/me").status_code == 401
    for bad in ("short", "ChangeMe", "changeme", "ben"):
        assert ben.post("/api/auth/login", json={"username": "ben", "password": "ChangeMe", "new_password": bad}).status_code in (400, 422)
    assert ben.post("/api/auth/login", json={"username": "ben", "password": "ChangeMe", "new_password": "bens-own-pw"}).status_code == 200
    assert ben.get("/api/auth/me").json()["name"] == "Ben"
    assert _other(admin).post("/api/auth/login", json={"username": "ben", "password": "ChangeMe"}).status_code == 401
    assert _other(admin).post("/api/auth/login", json={"username": "ben", "password": "bens-own-pw"}).status_code == 200

    # a password an admin sets has to be changed too
    bid = next(a["id"] for a in admin.get("/api/admin/accounts").json() if a["username"] == "ben")
    admin.put(f"/api/admin/accounts/{bid}", json={"username": "ben", "display_name": "Ben", "role": "tech", "password": "admin-set-1"})
    assert ben.get("/api/auth/me").status_code == 401  # signed out everywhere
    assert _other(admin).post("/api/auth/login", json={"username": "ben", "password": "admin-set-1"}).status_code == 428


def test_forgot_asks_the_managers_and_they_reset_it(strict):
    admin = strict
    admin.post("/api/admin/accounts", json={"username": "amy", "display_name": "Amy", "role": "tech", "password": "amy-first-1"})
    amy = _other(admin)
    amy.post("/api/auth/login", json={"username": "amy", "password": "amy-first-1", "new_password": "amy-chosen-1"})

    # the same answer for a real and a made-up username
    anon = _other(admin)
    assert anon.post("/api/auth/forgot", json={"username": "nobody"}).json() == {"ok": True, "at_once": False}
    assert anon.post("/api/auth/forgot", json={"username": "amy"}).json() == {"ok": True, "at_once": False}
    # nothing changes until a manager acts
    assert _other(admin).post("/api/auth/login", json={"username": "amy", "password": "amy-chosen-1"}).status_code == 200
    asks = admin.get("/api/bootstrap").json()["reset_asks"]
    assert [a["name"] for a in asks] == ["Amy"]

    aid = asks[0]["id"]
    assert admin.post(f"/api/admin/accounts/{aid}/reset").json() == {"ok": True, "password": "ChangeMe"}
    assert admin.get("/api/bootstrap").json()["reset_asks"] == []
    assert amy.get("/api/auth/me").status_code == 401
    assert _other(admin).post("/api/auth/login", json={"username": "amy", "password": "amy-chosen-1"}).status_code == 401
    assert _other(admin).post("/api/auth/login", json={"username": "amy", "password": "ChangeMe"}).status_code == 428

    # a tech can't reset anyone, and techs aren't told about requests
    tech = _other(admin)
    tech.post("/api/auth/login", json={"username": "amy", "password": "ChangeMe", "new_password": "amy-again-1"})
    assert tech.post(f"/api/admin/accounts/{aid}/reset").status_code == 403
    assert tech.get("/api/bootstrap").json()["reset_asks"] == []

    # asking over and over from one place is slowed down
    codes = [anon.post("/api/auth/forgot", json={"username": "amy"}).status_code for _ in range(6)]
    assert codes[-1] == 429


def test_forgot_can_reset_at_once_but_never_an_admin(strict):
    admin = strict
    admin.put("/api/admin/settings", json={"forgot_resets_at_once": True})
    admin.post("/api/admin/accounts", json={"username": "cal", "display_name": "Cal", "role": "tech", "password": "cal-first-1"})
    _other(admin).post("/api/auth/login", json={"username": "cal", "password": "cal-first-1", "new_password": "cal-chosen-1"})
    assert _other(admin).post("/api/auth/forgot", json={"username": "cal"}).json()["at_once"] is True
    assert _other(admin).post("/api/auth/login", json={"username": "cal", "password": "ChangeMe"}).status_code == 428
    # an admin's password only ever waits for another admin
    _other(admin).post("/api/auth/forgot", json={"username": "admin"})
    assert _other(admin).post("/api/auth/login", json={"username": "admin", "password": "correct-horse"}).status_code == 200
    assert [a["name"] for a in admin.get("/api/bootstrap").json()["reset_asks"]] == ["admin"]


def test_tech_laptop_signs_in_with_the_account(strict):
    admin = strict
    node = _tech_laptop(admin)
    room = admin.get("/api/bootstrap").json()["rooms"][0]["id"]
    admin.post("/api/admin/accounts", json={"username": "dee", "display_name": "Dee", "role": "tech", "password": "dee-first-1"})
    admin.post("/api/admin/accounts", json={"username": "vee", "display_name": "Vee", "role": "viewer", "password": "vee-first-1"})
    lap = _other(admin)
    assert lap.get("/api/nodes/me", headers=node).json()["tech_passwords"] is True
    start = {"room_id": room, "mode": "main"}
    assert lap.post("/api/nodes/me/start", headers=node, json={**start, "operator": "Anyone"}).status_code == 422
    assert lap.post("/api/nodes/me/start", headers=node, json={**start, "username": "dee", "password": "wrong-one"}).status_code == 401
    assert lap.post("/api/nodes/me/start", headers=node, json={**start, "username": "dee", "password": "dee-first-1"}).status_code == 428
    r = lap.post("/api/nodes/me/start", headers=node, json={**start, "username": "dee", "password": "dee-first-1", "new_password": "dee-chosen-1"})
    assert r.status_code == 200, r.text
    assert lap.get("/api/nodes/me", headers=node).json()["node"]["operator"] == "Dee"
    # a viewer account can't run a tech laptop
    lap.post("/api/auth/login", json={"username": "vee", "password": "vee-first-1", "new_password": "vee-chosen-1"})
    assert lap.post("/api/nodes/me/start", headers=node, json={**start, "username": "vee", "password": "vee-chosen-1"}).status_code == 403

    # with tech passwords off, the name is enough (how sites set up before 1.0.10 carry on)
    admin.put("/api/admin/settings", json={"tech_passwords": False})
    assert lap.post("/api/nodes/me/start", headers=node, json={**start, "operator": "Guest Tech"}).status_code == 200


def test_upgraded_sites_keep_name_sign_in(tmp_path, monkeypatch):
    """The migration turns tech passwords off on a site that was already set up."""
    import sqlite3

    from atsuit import db

    monkeypatch.setenv("ATSUIT_DATA", str(tmp_path))
    from atsuit import config

    config.reload()
    keep = db.MIGRATIONS
    i = next(n for n, m in enumerate(db.MIGRATIONS) if "must_change" in m)
    monkeypatch.setattr(db, "MIGRATIONS", keep[:i])
    db.migrate()
    with db.tx() as c:
        db.set_setting(c, "setup_complete", True)
    monkeypatch.setattr(db, "MIGRATIONS", keep)
    db.migrate()
    with db.ro() as c:
        assert db.get_setting(c, "tech_passwords", True) is False
    c = sqlite3.connect(config.cfg.db_path)
    assert "must_change" in [r[1] for r in c.execute("PRAGMA table_info(accounts)")]
