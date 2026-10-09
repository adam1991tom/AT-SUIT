"""Nothing works without a licence key; a subscription warns, has a grace period, then
locks, but never during a show."""
import time

import pytest
from licensed_server import licence_key

DAY = 86400
SETUP = {"organisation": "O", "site_name": "S", "admin_username": "admin", "admin_password": "correct-horse", "rooms": ["CC"]}


@pytest.fixture()
def clock(monkeypatch):
    """Move the licence clock: clock.t is what licence thinks the time is."""
    from atsuit import licence

    class Clock:
        t = time.time()

    monkeypatch.setattr(licence, "_now", lambda: Clock.t)
    return Clock


def _tech(c):
    c.post("/api/admin/accounts", json={"username": "tech1", "password": "password1", "role": "tech"})


def _as(c, user, pw):
    c.post("/api/auth/logout")
    assert c.post("/api/auth/login", json={"username": user, "password": pw}).status_code == 200


def _enforce(**kw):
    from atsuit import licence

    return licence.enforce(**kw)


def test_a_new_server_is_locked_until_a_key_is_added(client):
    assert client.post("/api/setup", json=SETUP).status_code == 200
    b = client.get("/api/bootstrap").json()
    assert b["licence_locked"] is True and b["licence"]["state"] == "none"
    # Only the Licence page works: the API answers 402, pages say "not licensed", live connections are refused.
    assert client.get("/api/admin/accounts").status_code == 402
    assert client.get("/api/timers").status_code == 402
    for page in ("/node", "/screen", "/timer/1", "/captions/1"):
        r = client.get(page)
        assert r.status_code == 200 and "isn't licensed" in r.text, page
    assert client.get("/api/licence/status").json() == {"locked": True}
    with pytest.raises(Exception):
        with client.websocket_connect("/ws?topics=timers") as ws:
            ws.receive_text()
    assert client.get("/api/admin/licence").json()["locked"] is True
    # A bad key doesn't unlock it; a good one does, straight away.
    assert client.put("/api/admin/licence", json={"key": "garbage.key"}).status_code == 400
    assert client.put("/api/admin/licence", json={"key": licence_key()}).status_code == 200
    assert client.get("/api/admin/accounts").status_code == 200
    assert client.get("/api/licence/status").json() == {"locked": False}
    assert "isn't licensed" not in client.get("/node").text


def test_setup_refuses_a_bad_key(client):
    assert client.post("/api/setup", json={**SETUP, "licence_key": "garbage.key"}).status_code == 400
    assert client.get("/api/health").json()["setup_complete"] is False


def test_warnings_before_the_end(admin, clock):
    _tech(admin)
    assert admin.put("/api/admin/licence", json={"key": licence_key(expires=int(clock.t + 40 * DAY))}).status_code == 200
    assert admin.get("/api/bootstrap").json()["licence_notice"] is None
    clock.t += 20 * DAY  # 20 days left: admins hear about it, techs don't yet
    n = admin.get("/api/bootstrap").json()["licence_notice"]
    assert n["level"] == "warn" and n["admin"] and "20 days" in n["text"]
    _as(admin, "tech1", "password1")
    assert admin.get("/api/bootstrap").json()["licence_notice"] is None
    clock.t += 15 * DAY  # 5 days left: everyone
    n = admin.get("/api/bootstrap").json()["licence_notice"]
    assert n["level"] == "warn" and not n["admin"] and "Ask your admin" in n["text"]


def test_grace_then_lock_only_at_a_quiet_moment(admin, clock):
    from atsuit import db

    _tech(admin)
    end = int(clock.t + 10 * DAY)
    assert admin.put("/api/admin/licence", json={"key": licence_key(expires=end)}).status_code == 200
    # Ended: GRACE_DAYS of grace with everything working, and everyone told.
    clock.t = end + 2 * DAY
    b = admin.get("/api/bootstrap").json()
    assert not b["licence_locked"] and b["licence"]["state"] == "grace" and b["licence_notice"]["level"] == "bad"
    assert admin.get("/api/admin/accounts").status_code == 200
    assert not _enforce(at_start=True)
    # Grace over: due to lock, but not mid-afternoon, and not while a timer runs.
    clock.t = end + 15 * DAY
    assert admin.get("/api/admin/licence").json()["state"] == "lapsed"
    assert "locks tonight" in admin.get("/api/bootstrap").json()["licence_notice"]["text"]
    assert not _enforce(hour=14)
    rid = admin.get("/api/bootstrap").json()["rooms"][0]["id"]
    assert admin.get(f"/api/timers/{rid}").status_code == 200  # makes the room's timer
    with db.tx() as c:
        assert c.execute("UPDATE timers SET running=1").rowcount == 1
    assert not _enforce(hour=3) and not _enforce(at_start=True)
    assert admin.get("/api/admin/accounts").status_code == 200
    with db.tx() as c:
        c.execute("UPDATE timers SET running=0, first_started_at=NULL")
    # 3 am, no show on: it locks. Nothing is deleted, and a new key unlocks it at once.
    assert _enforce(hour=3)
    assert admin.get("/api/admin/accounts").status_code == 402
    assert admin.get("/api/bootstrap").json()["licence_locked"] is True
    assert admin.put("/api/admin/licence", json={"key": licence_key(issued=int(clock.t), expires=int(clock.t + 365 * DAY))}).status_code == 200
    assert admin.get("/api/admin/accounts").status_code == 200
    assert {a["username"] for a in admin.get("/api/admin/accounts").json()} >= {"admin", "tech1"}


def test_an_expired_key_cant_be_installed(admin, clock):
    r = admin.put("/api/admin/licence", json={"key": licence_key(issued=int(clock.t - 400 * DAY), expires=int(clock.t - 30 * DAY))})
    assert r.status_code == 400 and "expired" in r.json()["detail"]


def test_winding_the_clock_back_doesnt_help(admin, clock):
    end = int(clock.t + 10 * DAY)
    admin.put("/api/admin/licence", json={"key": licence_key(expires=end)})
    clock.t = end + 30 * DAY
    _enforce(hour=3)
    assert admin.get("/api/admin/accounts").status_code == 402
    clock.t = end - 5 * DAY  # someone sets the server's clock back
    _enforce(at_start=True)
    from atsuit import licence
    licence.forget()
    assert admin.get("/api/admin/accounts").status_code == 402
    assert admin.get("/api/admin/licence").json()["state"] == "lapsed"


def test_a_newer_key_resets_a_clock_that_ran_ahead(admin, clock):
    real = clock.t
    clock.t = real + 3 * 365 * DAY  # the server's clock once jumped years ahead
    _enforce()
    clock.t = real
    r = admin.put("/api/admin/licence", json={"key": licence_key(issued=int(real), expires=int(real + 365 * DAY))})
    assert r.status_code == 200 and r.json()["state"] == "active"
    assert admin.get("/api/admin/accounts").status_code == 200


def test_installs_from_before_keys_get_time_to_add_one(admin, clock):
    from atsuit import db, licence

    with db.tx() as c:  # as if set up on an older version, with no key
        for k in ("licence_checked", "licence_in_use", "licence_key", "licence_clock"):
            c.execute("DELETE FROM settings WHERE key=?", (k,))
        licence.prepare(c)
    licence.forget()
    b = admin.get("/api/bootstrap").json()
    assert not b["licence_locked"] and b["licence"]["state"] == "grace"
    assert "needs a licence key" in b["licence_notice"]["text"]
    assert admin.get("/api/admin/accounts").status_code == 200
    # When that runs out it locks like a lapsed licence: at a quiet moment.
    clock.t += 15 * DAY
    licence.forget()
    assert admin.get("/api/admin/accounts").status_code == 200
    assert _enforce(at_start=True)
    assert admin.get("/api/admin/accounts").status_code == 402


def test_an_upgraded_install_with_a_lapsed_key_gets_time_too(admin, clock):
    from atsuit import db, licence

    end = int(clock.t + 10 * DAY)
    admin.put("/api/admin/licence", json={"key": licence_key(expires=end)})
    clock.t = end + 30 * DAY
    with db.tx() as c:  # as if this key was installed on an older version, which had no lock
        c.execute("DELETE FROM settings WHERE key='licence_checked'")
        licence.prepare(c)
    licence.forget()
    assert not _enforce(at_start=True)
    b = admin.get("/api/bootstrap").json()
    assert not b["licence_locked"] and b["licence"]["state"] == "grace" and b["licence_notice"]["level"] == "bad"
    assert admin.get("/api/admin/accounts").status_code == 200


def test_the_vendor_key_cant_be_swapped_by_a_setting(monkeypatch):
    from atsuit import licence

    monkeypatch.undo()  # the real vendor key, not the test one
    monkeypatch.setenv("ATSUIT_VENDOR_PUBKEY", "x" * 43)
    assert licence._vendor_raw()[1] == "vendor_pubkey.txt"
    assert not licence.parse(licence_key()).valid
