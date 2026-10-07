import io
import json
import time
import zipfile


def room(admin, name="CC"):
    return next(r["id"] for r in admin.get("/api/bootstrap").json()["rooms"] if r["name"] == name)


def add(admin, rid, **kw):
    r = admin.post(f"/api/timers/{rid}/cues", json=kw)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_cue_list_crud_and_order(admin):
    rid = room(admin)
    a = add(admin, rid, cue="1", title="Welcome", duration_ms=600000)
    b = add(admin, rid, cue="2", title="Keynote", duration_ms=1800000)
    c = add(admin, rid, cue="1.5", title="Video", duration_ms=120000, after_id=a)
    assert [q["title"] for q in admin.get(f"/api/timers/{rid}/cues").json()] == ["Welcome", "Video", "Keynote"]
    assert admin.post(f"/api/timers/{rid}/cues/reorder", json={"ids": [b, a, c]}).status_code == 200
    assert [q["id"] for q in admin.get(f"/api/timers/{rid}/cues").json()] == [b, a, c]
    assert admin.post(f"/api/timers/{rid}/cues/reorder", json={"ids": [b, a]}).status_code == 400
    assert admin.put(f"/api/timers/{rid}/cues/{c}", json={"title": "Sting", "duration_ms": 30000, "end_action": "play-next"}).status_code == 200
    assert admin.post(f"/api/timers/{rid}/cues", json={"timer_type": "sideways"}).status_code == 400
    assert admin.delete(f"/api/timers/{rid}/cues/{b}").status_code == 200
    assert [q["title"] for q in admin.get(f"/api/timers/{rid}/cues").json()] == ["Welcome", "Sting"]


def test_go_next_previous_and_skip(admin):
    rid = room(admin)
    a = add(admin, rid, title="A", duration_ms=60000)
    add(admin, rid, title="B (skipped)", duration_ms=60000, skip=True)
    c = add(admin, rid, title="C", duration_ms=90000)
    s = admin.post(f"/api/timers/{rid}/go").json()  # nothing loaded: go starts the first cue
    assert s["cue"]["id"] == a and s["running"] and s["playback"] == "play" and s["next"]["id"] == c
    s = admin.post(f"/api/timers/{rid}/next").json()  # skips B, loads C without starting
    assert s["cue"]["id"] == c and not s["running"] and s["playback"] == "armed" and s["remaining_ms"] == 90000
    s = admin.post(f"/api/timers/{rid}/previous").json()
    assert s["cue"]["id"] == a
    s = admin.post(f"/api/timers/{rid}/load", json={"cue_id": c}).json()
    assert s["title"] == "C" and s["cue_index"] == 2 and s["cue_count"] == 3
    s = admin.post(f"/api/timers/{rid}/toggle").json()
    assert s["running"]
    s = admin.post(f"/api/timers/{rid}/toggle").json()
    assert s["playback"] == "pause"
    s = admin.post(f"/api/timers/{rid}/go").json()  # past the last cue: stop
    assert s["playback"] == "stop" and s["cue"] is None


def test_end_actions_run_on_the_server(admin):
    rid = room(admin, "HD")
    add(admin, rid, title="Short", duration_ms=300, end_action="play-next")
    b = add(admin, rid, title="Then this", duration_ms=60000, end_action="stop")
    admin.post(f"/api/timers/{rid}/start")
    deadline = time.time() + 5
    while time.time() < deadline:
        s = admin.get(f"/api/timers/{rid}").json()
        if s["cue"] and s["cue"]["id"] == b:
            break
        time.sleep(0.1)
    assert s["cue"]["id"] == b and s["running"]


def test_import_ontime_v4_project(admin):
    rid = room(admin, "RH")
    project = {"rundowns": {"default": {"id": "default", "order": ["g1"], "flatOrder": ["e1", "d1", "e2"], "entries": {
        "e1": {"type": "event", "id": "e1", "cue": "1", "title": "Doors", "duration": 900000, "timeStart": 32400000,
               "timerType": "count-down", "endAction": "load-next", "colour": "#779BE7", "timeWarning": 120000, "timeDanger": 60000},
        "d1": {"type": "delay", "id": "d1", "duration": 300000},
        "e2": {"type": "event", "id": "e2", "cue": "2", "title": "Panel", "duration": 2700000, "timerType": "count-up", "skip": True}}}}}
    r = admin.post(f"/api/timers/{rid}/cues/import", files={"file": ("db.json", json.dumps(project).encode())})
    assert r.json() == {"imported": 2, "flash_danger": None}
    cues = admin.get(f"/api/timers/{rid}/cues").json()
    assert cues[0]["title"] == "Doors" and cues[0]["time_start"] == "09:00" and cues[0]["end_action"] == "load-next"
    assert cues[1]["timer_type"] == "count-up" and cues[1]["skip"]


def test_ontime_websocket_feed(admin):
    rid = room(admin)
    add(admin, rid, cue="1", title="Opening", duration_ms=400000, colour="#f00")
    add(admin, rid, cue="2", title="Q&A", duration_ms=600000)
    admin.post(f"/api/timers/{rid}/go")
    admin.post(f"/api/timers/{rid}/message", json={"message": "Wrap up", "message_visible": True})
    with admin.websocket_connect(f"/ontime/{rid}/ws") as ws:
        assert ws.receive_json()["tag"] == "client-init"
        full = ws.receive_json()
        assert full["tag"] == "runtime-data"
        p = full["payload"]
        assert p["eventNow"]["title"] == "Opening" and p["eventNow"]["cue"] == "1" and p["eventNext"]["title"] == "Q&A"
        assert p["timer"]["playback"] == "play" and 390000 < p["timer"]["current"] <= 400000 and p["timer"]["phase"] == "default"
        assert p["message"]["timer"] == {"text": "Wrap up", "visible": True, "blink": False, "blackout": False, "secondarySource": None}
        assert p["rundown"]["numEvents"] == 2 and p["rundown"]["selectedEventIndex"] == 0
        patch = ws.receive_json()["payload"]  # the running clock keeps coming as patches
        assert "timer" in patch and "eventNow" not in patch
        ws.send_json({"tag": "ping", "payload": 1})
        for _ in range(10):
            m = ws.receive_json()
            if m["tag"] == "pong":
                break
        assert m["tag"] == "pong"


def test_custom_view_upload_and_shim(admin, client):
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("countdown/index.html", "<html><head><title>x</title></head><body><script src='app.js'></script></body></html>")
        f.writestr("countdown/app.js", "new WebSocket('ws://10.100.70.101:4002/ws')")
        f.writestr("countdown/../../evil.txt", "nope")
    r = admin.post("/api/timers-views?name=Big Countdown", files={"file": ("countdown.zip", z.getvalue())})
    assert r.status_code == 200, r.text
    assert r.json()["url"] == "/room/<room>/external/big-countdown/"
    page = client.get("/external/big-countdown/?room=1")
    assert page.status_code == 200 and '<script src="/static/ontime-shim.js"></script>' in page.text
    assert "4002/ws" in client.get("/external/big-countdown/app.js").text
    assert client.get("/external/big-countdown/../../atsuit.db").status_code == 404
    # The room in the path, so a view's own ?room= (a display name) is left alone.
    page = client.get("/room/2/external/big-countdown/?room=Main%20Stage")
    assert page.status_code == 200 and "ontime-shim.js" in page.text
    r = client.get("/room/2/external/big-countdown", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"].endswith("/room/2/external/big-countdown/")
    assert any(v["id"] == "view:big-countdown" for v in client.get("/api/timers-views").json())
    assert admin.post("/api/timers-views?name=Bad", files={"file": ("x.exe", b"MZ")}).status_code == 400
    assert admin.delete("/api/timers-views/big-countdown").status_code == 200
    assert client.get("/external/big-countdown/").status_code == 404


def test_venue_ontime_project_presets_and_flash(admin):
    """Shaped like the venue's own Ontime files: a group of timer presets, a
    clock, 12-hour time, and an automation that blinks the timer at danger
    (pointing at another room's Ontime port, which AT-SUIT ignores)."""
    rid = room(admin, "HD")
    entries = {"g": {"type": "group", "id": "g", "title": "TIMERS do not touch", "parent": None},
               "c": {"type": "event", "id": "c", "cue": ".01", "title": "CLOCK", "duration": 0, "timerType": "clock",
                     "endAction": "none", "timeWarning": 0, "timeDanger": 0, "parent": "g"},
               "m2": {"type": "event", "id": "m2", "cue": ".02", "title": "2 MIN", "duration": 120000, "timerType": "count-down",
                      "endAction": "none", "timeWarning": 300000, "timeDanger": 60000, "parent": "g"}}
    project = {"rundowns": {"default": {"id": "default", "order": ["g"], "flatOrder": ["g", "c", "m2"], "entries": entries}},
               "settings": {"version": "4.14.1", "timeFormat": "12"},
               "automation": {"enabledAutomations": True, "triggers": [
                   {"title": "auto flash", "trigger": "onDanger", "automationId": "a1"},
                   {"title": "start new", "trigger": "onStart", "automationId": "a2"}],
                   "automations": {"a1": {"outputs": [{"type": "http", "url": "http://10.0.0.9:4001/api/message/timer?blink=true"}]},
                                   "a2": {"outputs": [{"type": "http", "url": "http://10.0.0.9:4001/api/message/timer?blink=false"}]}}}}
    r = admin.post(f"/api/timers/{rid}/cues/import", files={"file": ("HCC working file (migrated).json", json.dumps(project).encode())})
    assert r.json() == {"imported": 2, "flash_danger": True}
    assert [q["title"] for q in admin.get(f"/api/timers/{rid}/cues").json()] == ["CLOCK", "2 MIN"]
    assert admin.get(f"/ontime/{rid}/data/settings").json()["timeFormat"] == "12"
    s = admin.get(f"/api/timers/{rid}").json()
    assert s["flash_danger"]
    # Blink on at danger, once; off again when the next timer starts.
    m2 = admin.get(f"/api/timers/{rid}/cues").json()[1]["id"]
    admin.post(f"/api/timers/{rid}/load", json={"cue_id": m2})
    admin.post(f"/api/timers/{rid}/start")
    admin.post(f"/api/timers/{rid}/add", json={"delta_ms": -70000})  # 50 s left, inside danger
    for _ in range(30):
        if admin.get(f"/api/timers/{rid}").json()["message_blink"]:
            break
        time.sleep(0.1)
    assert admin.get(f"/api/timers/{rid}").json()["message_blink"]
    admin.post(f"/api/timers/{rid}/message", json={"message_blink": False})  # the tech turns it off
    time.sleep(0.6)
    assert not admin.get(f"/api/timers/{rid}").json()["message_blink"]
    admin.post(f"/api/timers/{rid}/message", json={"message_blink": True})
    admin.post(f"/api/timers/{rid}/load", json={"cue_id": m2})
    admin.post(f"/api/timers/{rid}/start")
    for _ in range(30):
        if not admin.get(f"/api/timers/{rid}").json()["message_blink"]:
            break
        time.sleep(0.1)
    assert not admin.get(f"/api/timers/{rid}").json()["message_blink"]
    # A tech can switch it off for the room.
    admin.post(f"/api/timers/{rid}/thresholds", json={"flash_danger": False})
    assert not admin.get(f"/api/timers/{rid}").json()["flash_danger"]
