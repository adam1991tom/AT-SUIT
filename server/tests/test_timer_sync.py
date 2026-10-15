"""A laptop that lost the server keeps the timer going and sends what the tech did when it's back."""
import time

import pytest

from atsuit.modules import timers


@pytest.fixture(autouse=True)
def _clean():
    timers._changed_by_people.clear()
    yield


def room(admin, name="CC"):
    return next(r["id"] for r in admin.get("/api/bootstrap").json()["rooms"] if r["name"] == name)


def sync(admin, rid, ops):
    r = admin.post(f"/api/timers/{rid}/sync", json={"ops": ops})
    assert r.status_code == 200, r.text
    return r.json()


def test_offline_changes_apply_as_of_when_they_were_made(admin):
    rid = room(admin)
    now = time.time()
    # Offline 10 minutes ago: a 20 minute quick timer, started; 4 minutes later a minute added and a message shown.
    out = sync(admin, rid, [
        {"path": "preset", "body": {"minutes": 20, "start": True}, "at": now - 600},
        {"path": "add", "body": {"delta_ms": 60000}, "at": now - 360},
        {"path": "message/show", "body": {"text": "5 minutes", "blink": True}, "at": now - 350},
        {"path": "secondary/timer", "body": {"minutes": 5}, "at": now - 300},
    ])
    assert out["applied"] == 4 and out["skipped"] == 0
    s = out["state"]
    assert s["running"] and s["duration_ms"] == 1_200_000 and s["added_ms"] == 60000
    # 20 min + 1 min - the 10 minutes since it started
    assert abs(s["remaining_ms"] - 660_000) < 3000
    assert s["message_visible"] and s["message"] == "5 minutes" and s["message_blink"]
    assert s["secondary"]["running"] and abs(s["secondary"]["remaining_ms"] - 0) < 3000
    assert admin.get(f"/api/timers/{rid}").json()["playback"] == "play"

    # Paused offline 2 minutes ago: the screens go back to what the laptop showed then.
    s = sync(admin, rid, [{"path": "pause", "body": {}, "at": time.time() - 120}])["state"]
    assert not s["running"] and abs(s["remaining_ms"] - 780_000) < 3000


def test_the_latest_change_wins(admin):
    rid = room(admin)
    t0 = time.time()
    admin.post(f"/api/timers/{rid}/preset/10")  # someone at the server starts a timer now
    out = sync(admin, rid, [
        {"path": "preset", "body": {"minutes": 30, "start": True}, "at": t0 - 60},  # older: left out
        {"path": "message/show", "body": {"text": "Hello"}, "at": time.time() + 0.01},  # newer: kept
    ])
    assert out["applied"] == 1 and out["skipped"] == 1
    assert out["state"]["duration_ms"] == 600_000 and out["state"]["message"] == "Hello"


def test_bad_or_stale_changes_are_left_out(admin, client):
    rid = room(admin)
    now = time.time()
    out = sync(admin, rid, [
        {"path": "explode", "body": {}, "at": now},
        {"path": "add", "body": {"delta_ms": "lots"}, "at": now},
        {"path": "load", "body": {"cue_id": 99999}, "at": now},
        {"path": "start", "body": {}, "at": now - timers.SYNC_WINDOW - 60},
        {"path": "sync", "body": {}, "at": now},
        {"path": "toggle", "body": {}, "at": now + 3600},  # a laptop clock in the future counts as now
    ])
    assert out["applied"] == 1 and out["skipped"] == 5
    assert admin.post(f"/api/timers/{rid}/sync", json={"ops": [{"path": "x", "at": now}] * 501}).status_code == 422
    anon = client.__class__(client.app)
    assert anon.post(f"/api/timers/{rid}/sync", json={"ops": []}).status_code in (401, 403)
