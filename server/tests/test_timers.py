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


def _apikey(admin):
    key = admin.post("/api/admin/api-keys", json={"name": "Companion"}).json()["key"]
    return {"X-API-Key": key}


def test_companion_presets_switches_and_messages(admin, client):
    rid = room(admin)
    k = _apikey(admin)
    client.cookies.clear()  # only the API key from here on, as Companion sends
    assert client.post(f"/api/timers/{rid}/preset", json={"minutes": 5}).status_code == 401
    s = client.post(f"/api/timers/{rid}/preset", headers=k, json={"minutes": 5}).json()
    assert s["running"] and s["duration_ms"] == 300000 and s["cue"] is None and s["warn_ms"] == 300000 and s["danger_ms"] == 60000
    for m in (3, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60):
        s = client.post(f"/api/timers/{rid}/preset/{m}", headers=k).json()
        assert s["duration_ms"] == m * 60000 and s["running"]
    s = client.post(f"/api/timers/{rid}/preset/10?start=false", headers=k).json()
    assert not s["running"] and s["playback"] == "armed" and s["remaining_ms"] == 600000
    assert client.post(f"/api/timers/{rid}/preset/0", headers=k).status_code == 400
    assert client.post(f"/api/timers/{rid}/preset", headers=k, json={"minutes": -1}).status_code == 422
    s = client.post(f"/api/timers/{rid}/add", headers=k, json={"delta_ms": 60000}).json()
    assert s["remaining_ms"] == 660000
    # Blink, clock and blackout: on, off, toggle, with or without a body.
    assert client.post(f"/api/timers/{rid}/blink/on", headers=k).json()["message_blink"]
    assert not client.post(f"/api/timers/{rid}/blink/toggle", headers=k).json()["message_blink"]
    assert client.post(f"/api/timers/{rid}/blink", headers=k).json()["message_blink"]
    assert not client.post(f"/api/timers/{rid}/blink", headers=k, json={"on": False}).json()["message_blink"]
    assert client.post(f"/api/timers/{rid}/blackout/on", headers=k).json()["blackout"]
    assert not client.post(f"/api/timers/{rid}/blackout/off", headers=k).json()["blackout"]
    assert client.post(f"/api/timers/{rid}/sideways/on", headers=k).status_code == 404
    assert client.post(f"/api/timers/{rid}/blink/maybe", headers=k).status_code == 404
    s = client.post(f"/api/timers/{rid}/clock/on", headers=k).json()
    assert s["show_clock"] and s["time_format"] == "24" and 0 <= s["clock_ms"] < 86400000
    with client.websocket_connect(f"/ontime/{rid}/ws") as ws:  # Ontime views show the time of day too
        ws.receive_json()
        assert ws.receive_json()["payload"]["eventNow"]["timerType"] == "clock"
    assert not client.post(f"/api/timers/{rid}/clock/toggle", headers=k).json()["show_clock"]
    client.post(f"/api/timers/{rid}/clock/on", headers=k)
    assert not client.post(f"/api/timers/{rid}/preset/5", headers=k).json()["show_clock"]  # a new timer brings it back
    # Messages.
    s = client.post(f"/api/timers/{rid}/message/show", headers=k, json={"text": "Wrap up", "blink": True}).json()
    assert s["message"] == "Wrap up" and s["message_visible"] and s["message_blink"]
    s = client.post(f"/api/timers/{rid}/message/hide", headers=k).json()
    assert not s["message_visible"] and s["message"] == "Wrap up"
    assert client.post(f"/api/timers/{rid}/message/show", headers=k).json()["message_visible"]  # no body: same text again
    # Cue routes still win over the switch route.
    assert client.post(f"/api/timers/{rid}/cues", headers=k, json={"title": "A", "duration_ms": 1000}).status_code == 200
    assert client.post(f"/api/timers/{rid}/cues/reorder", headers=k, json={"ids": [q["id"] for q in client.get(f"/api/timers/{rid}/cues").json()]}).status_code == 200


def test_clock_button_and_back(admin):
    rid = room(admin)
    a = add(admin, rid, title="A", duration_ms=60000)
    admin.post(f"/api/timers/{rid}/go")
    assert admin.post(f"/api/timers/{rid}/clock", json={}).json()["show_clock"]
    s = admin.post(f"/api/timers/{rid}/pause").json()
    assert s["show_clock"]  # pausing keeps the clock up
    assert not admin.post(f"/api/timers/{rid}/clock", json={}).json()["show_clock"]
    admin.post(f"/api/timers/{rid}/clock", json={"on": True})
    assert not admin.post(f"/api/timers/{rid}/load", json={"cue_id": a}).json()["show_clock"]


def test_branded_views_logos_and_builder(admin, client):
    rid = room(admin)
    ids = {v["id"] for v in client.get("/api/timers-views").json()}
    assert {"hcc", "overlay"} <= ids and "bdng" not in ids  # BDNG is an imported view now
    assert next(v["name"] for v in client.get("/api/timers-views").json() if v["id"] == "hcc") == "Standard"
    admin.put("/api/admin/settings", json={"branding": {"logo_url": "/static/site-logo.png"}})
    look = client.get("/api/timers-views/look/hcc").json()
    assert look["logos"] == {"top": ""} and look["site_logo"] == "/static/site-logo.png" and look["uses_site_logo"]
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    r = admin.post("/api/timers-views/look/bdng/logo/bottom", files={"file": ("sponsor.png", png)})
    assert r.status_code == 200 and r.json()["url"].startswith("/api/timers-views/logo/bdng-bottom.png")
    assert client.get(r.json()["url"]).content == png
    assert admin.post("/api/timers-views/look/bdng/logo/side", files={"file": ("x.png", png)}).status_code == 404
    assert admin.post("/api/timers-views/look/hcc/logo/top", files={"file": ("x.exe", b"MZ")}).status_code == 400
    assert client.get("/api/timers-views/logo/..%2Fatsuit.db").status_code == 404
    assert admin.put("/api/timers-views/look/bdng", json={"bottom_text": "Official timekeeping sponsor"}).status_code == 200
    look = client.get("/api/timers-views/look/bdng").json()
    assert look["options"]["bottom_text"] == "Official timekeeping sponsor" and look["logos"]["bottom"]
    assert admin.delete("/api/timers-views/look/bdng/logo/bottom").status_code == 200
    assert client.get("/api/timers-views/look/bdng").json()["logos"]["bottom"] == ""
    # A view built in the console.
    assert admin.post("/api/timers-designs", json={"name": "Green room", "background": "red"}).status_code == 422
    d = admin.post("/api/timers-designs", json={"name": "Green room", "background": "#0b3d2e", "show_clock": True}).json()
    assert d == {"id": "built:green-room", "slug": "green-room"}
    assert admin.post("/api/timers-designs", json={"name": "Green room"}).json()["slug"] == "green-room-2"
    assert admin.post("/api/timers-views/look/built:green-room/logo/logo", files={"file": ("l.svg", b"<svg/>")}).status_code == 200
    look = client.get("/api/timers-views/look/built:green-room").json()
    assert look["design"]["background"] == "#0b3d2e" and look["design"]["show_clock"] and look["design"]["show_title"]
    assert "Content-Security-Policy" in client.get(look["logos"]["logo"]).headers
    assert admin.put("/api/timers-designs/green-room", json={"name": "Green room", "text": "#ffcc00"}).status_code == 200
    assert client.get("/api/timers-views/look/built:green-room").json()["design"]["text"] == "#ffcc00"
    assert "built:green-room" in {v["id"] for v in client.get("/api/timers-views").json()}
    # Screens can be routed to all of them.
    code = admin.get("/api/fleet/enrolment").json()[0]["enrol_code"]
    n = admin.post("/api/nodes/enrol", json={"code": code, "name": "scr", "kind": "kiosk"}).json()
    for v in ("hcc", "bdng", "overlay", "built:green-room"):
        assert admin.put(f"/api/fleet/nodes/{n['node_id']}/screen", json={"room_id": rid, "view": v}).status_code == 200
    assert admin.put(f"/api/fleet/nodes/{n['node_id']}/screen", json={"room_id": rid, "view": "built:nope"}).status_code == 400
    assert admin.delete("/api/timers-designs/green-room").status_code == 200
    assert client.get("/api/timers-views/look/built:green-room").status_code == 404
    page = client.get(f"/timer/{rid}?view=hcc")
    assert page.status_code == 200 and "v-hcc" in page.text


def test_secondary_line_api_and_ontime_feed(admin, client):
    rid = room(admin)
    k = _apikey(admin)
    client.cookies.clear()
    s = client.get(f"/api/timers/{rid}").json()["secondary"]
    assert s == {"mode": "text", "visible": False, "text": "", "duration_ms": 0, "remaining_ms": 0, "running": False}
    assert client.post(f"/api/timers/{rid}/secondary/text", json={"text": "Q&A next"}).status_code == 401
    s = client.post(f"/api/timers/{rid}/secondary/text", headers=k, json={"text": " Q&A next "}).json()["secondary"]
    assert s["mode"] == "text" and s["visible"] and s["text"] == "Q&A next"
    assert client.post(f"/api/timers/{rid}/secondary/text", headers=k, json={"text": " "}).status_code == 400
    with client.websocket_connect(f"/ontime/{rid}/ws") as ws:  # Ontime views (HCC, BDNG) show it as the secondary message
        ws.receive_json()
        p = ws.receive_json()["payload"]
        assert p["message"]["timer"]["secondarySource"] == "secondary" and p["message"]["secondary"] == "Q&A next"
    s = client.post(f"/api/timers/{rid}/secondary/hide", headers=k).json()["secondary"]
    assert not s["visible"] and s["text"] == "Q&A next"
    assert client.post(f"/api/timers/{rid}/secondary/show", headers=k).json()["secondary"]["visible"]
    # A second countdown, with or without a body.
    s = client.post(f"/api/timers/{rid}/secondary/timer", headers=k, json={"minutes": 5}).json()["secondary"]
    assert s["mode"] == "timer" and s["running"] and s["duration_ms"] == 300000 and 299000 < s["remaining_ms"] <= 300000
    s = client.post(f"/api/timers/{rid}/secondary/timer/2?start=false", headers=k).json()["secondary"]
    assert not s["running"] and s["remaining_ms"] == 120000
    assert client.post(f"/api/timers/{rid}/secondary/timer/0", headers=k).status_code == 400
    assert client.post(f"/api/timers/{rid}/secondary/timer", headers=k, json={}).status_code == 400
    s = client.post(f"/api/timers/{rid}/secondary/add", headers=k, json={"delta_ms": 60000}).json()["secondary"]
    assert s["remaining_ms"] == 180000
    assert client.post(f"/api/timers/{rid}/secondary/toggle", headers=k).json()["secondary"]["running"]
    assert not client.post(f"/api/timers/{rid}/secondary/pause", headers=k).json()["secondary"]["running"]
    assert client.post(f"/api/timers/{rid}/secondary/start", headers=k).json()["secondary"]["running"]
    s = client.post(f"/api/timers/{rid}/secondary/reset", headers=k).json()["secondary"]
    assert not s["running"] and s["remaining_ms"] == 120000
    assert client.post(f"/api/timers/{rid}/secondary/sideways", headers=k).status_code == 404
    with client.websocket_connect(f"/ontime/{rid}/ws") as ws:  # ... and the countdown as aux timer 1
        ws.receive_json()
        p = ws.receive_json()["payload"]
        assert p["message"]["timer"]["secondarySource"] == "aux1" and p["auxtimer1"]["current"] == 120000
        assert p["auxtimer1"]["playback"] == "pause"
    client.post(f"/api/timers/{rid}/secondary/hide", headers=k)
    with client.websocket_connect(f"/ontime/{rid}/ws") as ws:
        ws.receive_json()
        assert ws.receive_json()["payload"]["message"]["timer"]["secondarySource"] is None
    # The main timer is left alone.
    assert client.get(f"/api/timers/{rid}").json()["playback"] == "stop"


def test_quick_messages(admin, client):
    r = admin.get("/api/timers-quick-messages").json()
    assert "Stand behind the mic" in r["messages"] and r["can_edit"] and r["messages"] == r["default"]
    r = admin.put("/api/timers-quick-messages", json={"messages": [" Wrap up ", "", "Wrap up", "Mic please"]})
    assert r.status_code == 200 and r.json()["messages"] == ["Wrap up", "Mic please"]
    # A tech laptop reads them but can't change them; an API key can read them too.
    code = admin.get("/api/fleet/enrolment").json()[0]["enrol_code"]
    tok = admin.post("/api/nodes/enrol", json={"code": code, "name": "lap", "kind": "tech"}).json()["token"]
    k = _apikey(admin)
    admin.cookies.clear()
    h = {"Authorization": f"Node {tok}"}
    r = client.get("/api/timers-quick-messages", headers=h).json()
    assert r["messages"] == ["Wrap up", "Mic please"] and not r["can_edit"]
    assert client.put("/api/timers-quick-messages", headers=h, json={"messages": ["x"]}).status_code == 403
    assert client.put("/api/timers-quick-messages", headers=k, json={"messages": ["x"]}).status_code == 403
    assert client.get("/api/timers-quick-messages").status_code == 401
    client.post("/api/auth/login", json={"username": "admin", "password": "correct-horse"})
    assert client.delete("/api/timers-quick-messages").json()["messages"][0] == "Please wrap up"
    assert client.get("/api/timers-quick-messages").json()["messages"][0] == "Please wrap up"


def test_status_bar_setting_and_view_defaults(admin, client):
    rid = room(admin)
    assert client.get("/api/timers-views/look/stage").json()["options"] == {"status_bar": True}
    assert client.get("/api/timers-views/look/overlay").json()["options"] == {"status_bar": False}
    assert client.get("/api/timers-views/look/hcc").json()["options"]["status_bar"]
    assert admin.put("/api/timers-views/look/hcc", json={"status_bar": False}).status_code == 200
    assert client.get("/api/timers-views/look/hcc").json()["options"] == {"status_bar": False}
    # The imported BDNG view's text: kept when something else changes, and the other way round.
    assert client.get("/api/timers-views/look/bdng").json()["options"]["bottom_text"] == "BDNG Official Timekeeping Sponsor"
    admin.put("/api/timers-views/look/bdng", json={"status_bar": False})
    admin.put("/api/timers-views/look/bdng", json={"bottom_text": "Sponsor"})
    assert client.get("/api/timers-views/look/bdng").json()["options"] == {"bottom_text": "Sponsor", "status_bar": False}
    assert admin.put("/api/timers-views/look/nope", json={"status_bar": False}).status_code == 404
    # Views built in the console: status bar and second line on, next cue off, unless chosen.
    admin.post("/api/timers-designs", json={"name": "Green room"})
    d = client.get("/api/timers-views/look/built:green-room").json()["design"]
    assert d["show_status"] and d["show_secondary"] and not d["show_next"]
    page = client.get(f"/timer/{rid}?view=stage").text
    assert 'id="status"' in page and 'id="sec"' in page


def test_bdng_is_an_imported_view(admin, client):
    rid = room(admin)
    # Before it's imported, view=bdng still works (screens set to it keep a timer): the Standard view.
    code = admin.get("/api/fleet/enrolment").json()[0]["enrol_code"]
    n = admin.post("/api/nodes/enrol", json={"code": code, "name": "scr", "kind": "kiosk"}).json()
    assert admin.put(f"/api/fleet/nodes/{n['node_id']}/screen", json={"room_id": rid, "view": "bdng"}).status_code == 200
    page = client.get(f"/timer/{rid}?view=bdng").text
    assert 'view === "bdng"' in page and 'view = "hcc"' in page
    # The shipped file: the Ontime original, no logos inside, downloadable and importable in one step.
    html = client.get("/api/timers-views/samples/bdng.html")
    assert html.status_code == 200 and "ONTIME_SPONSOR_CONFIG" in html.text and "connectSocket" in html.text
    assert ".png" not in html.text.replace("assets/top-logo.png", "").replace("assets/bottom-logo.png", "")
    assert client.get("/api/timers-views/samples/nope.html").status_code == 404
    r = admin.post("/api/timers-views/samples/bdng/import").json()
    assert r["id"] == "view:bdng"
    assert "view:bdng" in {v["id"] for v in client.get("/api/timers-views").json()}
    served = client.get(f"/room/{rid}/external/bdng/")
    assert served.status_code == 200 and "/static/ontime-shim.js" in served.text and "/static/viewbar.js" in served.text
    assert served.text.replace(timers_shim(), "") == html.text  # the file itself runs unchanged
    # Imported views: status bar off unless switched on.
    assert client.get("/api/timers-views/look/view:bdng").json()["options"] == {"status_bar": False}
    assert admin.put("/api/timers-views/look/view:bdng", json={"status_bar": True}).status_code == 200
    assert client.get("/api/timers-views/look/view:bdng").json()["options"] == {"status_bar": True}
    assert client.get("/api/timers-views/look/view:nope").status_code == 404
    assert admin.put("/api/timers-views/look/view:nope", json={"status_bar": True}).status_code == 404
    # It reads the room's timer from the Ontime feed, like on Ontime.
    admin.post(f"/api/timers/{rid}/preset/5")
    with client.websocket_connect(f"/ontime/{rid}/ws") as ws:
        ws.receive_json()
        assert ws.receive_json()["payload"]["timer"]["playback"] == "play"
    assert client.get(f"/ontime/{rid}/data/settings").json()["timeFormat"] in ("12", "24")


def timers_shim():
    from atsuit.modules.timers import SHIM
    return SHIM
