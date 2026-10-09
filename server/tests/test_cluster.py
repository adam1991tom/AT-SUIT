"""Helper servers: joining with a one-time code, placing a room's captions on a
helper, moving it when the helper drops, and the helper's own job runner."""
import asyncio
import struct
import time

import numpy as np

from atsuit import asr
from atsuit.modules import cluster


def _join(admin, name="Rack 2"):
    code = admin.post("/api/admin/helpers/code").json()["code"]
    r = admin.post("/api/helpers/join", json={"code": code, "name": name})
    assert r.status_code == 200, r.text
    return r.json()


def test_joining_takes_a_fresh_code_and_only_admins_manage_helpers(admin, client):
    assert admin.post("/api/helpers/join", json={"code": "ABCD-EFGH", "name": "x"}).status_code == 403  # no code made yet
    code = admin.post("/api/admin/helpers/code").json()["code"]
    assert len(code) == 9 and code[4] == "-"
    assert admin.post("/api/helpers/join", json={"code": "WRONG-ONE", "name": "x"}).status_code == 403
    first = admin.post("/api/helpers/join", json={"code": code.lower(), "name": "Rack 2"}).json()
    assert first["name"] == "Rack 2" and len(first["secret"]) > 30
    assert admin.post("/api/helpers/join", json={"code": code, "name": "again"}).status_code == 403  # one use only
    d = admin.get("/api/admin/helpers").json()
    assert d["mode"] == "share" and d["main"]["main"] and [h["name"] for h in d["helpers"]] == ["Rack 2"]
    assert d["helpers"][0]["online"] is False
    assert admin.put("/api/admin/helpers/mode", json={"mode": "offload"}).json()["mode"] == "offload"
    assert admin.put("/api/admin/helpers/mode", json={"mode": "everything"}).status_code == 400
    # a manager runs rooms, not servers
    admin.post("/api/admin/accounts", json={"username": "mia", "password": "password1", "role": "manager"})
    mia = client.__class__(client.app)
    mia.post("/api/auth/login", json={"username": "mia", "password": "password1"})
    assert mia.get("/api/admin/helpers").status_code == 403
    assert mia.post("/api/admin/helpers/code").status_code == 403
    # an unknown secret can't connect
    try:
        with client.websocket_connect("/ws/helper", headers={"Authorization": "Bearer nope"}):
            raise AssertionError("should have been refused")
    except Exception as exc:
        assert "4403" in repr(exc) or "403" in repr(exc) or "Disconnect" in type(exc).__name__
    assert admin.delete(f"/api/admin/helpers/{first['id']}").status_code == 200
    assert admin.get("/api/admin/helpers").json()["helpers"] == []
    log = [l["action"] for l in admin.get("/api/admin/audit").json()]
    assert {"helper.code", "helper.join", "helper.mode", "helper.remove"} <= set(log)


def test_a_helper_runs_a_rooms_captions_and_the_room_moves_when_it_drops(admin, client):
    # This server has speech recognition off (ATSUIT_ASR=0), so only the helper can caption.
    secret = _join(admin)["secret"]
    rid = admin.get("/api/bootstrap").json()["rooms"][0]["id"]
    assert admin.put(f"/api/captions/rooms/{rid}", json={"enabled": True, "vocabulary": "Lovelace", "record": False}).status_code == 200
    code = admin.get("/api/fleet/enrolment").json()[0]["enrol_code"]
    tok = client.post("/api/nodes/enrol", json={"code": code, "name": "mic1"}).json()["token"]
    pcm = (np.sin(np.arange(1600) / 5) * 8000).astype("<i2").tobytes()

    with client.websocket_connect("/ws/helper", headers={"Authorization": f"Bearer {secret}"}) as helper:
        cfg = helper.receive_json()
        assert cfg["t"] == "config" and "Lovelace" in cfg["vocabulary"]
        helper.send_json({"t": "hello", "name": "Rack 2", "version": "test", "capacity": 2, "load": 0.1, "asr": "ready"})
        for _ in range(50):
            if admin.get("/api/admin/helpers").json()["capacity"] == 2:
                break
            time.sleep(0.02)
        d = admin.get("/api/admin/helpers").json()
        assert d["capacity"] == 2 and d["helpers"][0]["online"] and d["helpers"][0]["speech"] == "ready"
        assert admin.get("/api/captions/status").json()["engine"]["state"] == "ready"  # the helper does the listening

        with client.websocket_connect(f"/ws?topics=captions:{rid}") as viewer:
            assert viewer.receive_json()["type"] == "hello"
            with client.websocket_connect(f"/ws/audio/{rid}?node_token={tok}") as mic:
                assert mic.receive_json()["type"] == "ready"
                opened = helper.receive_json()
                assert opened["t"] == "open" and opened["options"]["music_label"] is True
                job = opened["job"]
                mic.send_bytes(pcm)
                frame = helper.receive_bytes()
                assert struct.unpack(">I", frame[:4])[0] == job and len(frame) == 4 + len(pcm)
                helper.send_json({"t": "events", "job": job, "events": [{"type": "final", "text": "HELLO FROM RACK TWO", "words": []}]})
                time.sleep(0.2)
                mic.send_bytes(pcm)  # the next audio picks the captions up
                helper.receive_bytes()
                while (evt := viewer.receive_json())["type"] != "final":
                    pass
                assert evt["data"]["text"] == "HELLO FROM RACK TWO"
                placed = admin.get("/api/admin/helpers").json()["placed"]
                assert placed == [{"room_id": rid, "server": "Rack 2"}]

                # The helper goes; nothing else can caption, so the laptop is told why.
                helper.close()
                time.sleep(0.2)
                mic.send_bytes(pcm)
                err = mic.receive_json()
                assert err["type"] == "error"
    assert admin.get("/api/admin/helpers").json()["helpers"][0]["online"] is False


class FakeRecognizer:
    def __init__(self):
        self.n = 0

    def create_stream(self):
        return self

    def accept_waveform(self, rate, samples):
        self.n += 1

    def is_ready(self, s):
        return False

    def is_endpoint(self, s):
        return self.n >= 2

    def get_result(self, s):
        return "TESTING ONE TWO" if self.n else ""

    def reset(self, s):
        self.n = 0


def test_the_helper_runs_jobs_it_is_given(tmp_path, monkeypatch):
    from atsuit import helper

    async def go():
        sent = []

        async def send(m):
            sent.append(m)

        asr.engine = asr.Engine()
        asr.engine.recognizer, asr.engine.state = FakeRecognizer(), "ready"
        w = helper.Worker(send)
        assert w.stats("hello")["capacity"] > 0 and w.stats()["asr"] == "ready"
        await w.on_text({"t": "open", "job": 7, "options": {}})
        pcm = np.zeros(1600, dtype="<i2").tobytes()
        await w.on_bytes(struct.pack(">I", 7) + pcm)
        await w.on_bytes(struct.pack(">I", 99) + pcm)  # a job it doesn't have: ignored
        await w.on_bytes(struct.pack(">I", 7) + pcm)
        for _ in range(100):
            if any(e["type"] == "final" for m in sent if m.get("t") == "events" for e in m["events"]):
                break
            await asyncio.sleep(0.01)
        ev = [m for m in sent if m.get("t") == "events"]
        assert [e["type"] for m in ev for e in m["events"]] == ["partial", "final"] and {m["job"] for m in ev} == {7}
        assert ev[-1]["events"][-1] == {"type": "final", "text": "TESTING ONE TWO", "words": []}
        await w.on_text({"t": "close", "job": 7})
        assert w.jobs == {} and w.tasks == {}
        # not ready: the main server hears straight away so it can place the room elsewhere
        asr.engine.state = "loading"
        await w.on_text({"t": "open", "job": 8, "options": {}})
        for _ in range(50):
            if any(m.get("t") == "error" for m in sent):
                break
            await asyncio.sleep(0.01)
        assert any(m.get("t") == "error" and m["job"] == 8 for m in sent)
        await w.close_all()

    monkeypatch.setenv("ATSUIT_DATA", str(tmp_path))
    from atsuit import config
    config.reload()
    asyncio.run(go())
    assert helper.ws_url("https://a.b:8443") == "wss://a.b:8443/ws/helper"
    assert helper.ws_url("http://10.0.0.1:8080") == "ws://10.0.0.1:8080/ws/helper"


def test_placement_prefers_the_least_busy_server(monkeypatch):
    class L:
        def __init__(self, i, cap, jobs):
            self.id, self.capacity, self.jobs, self.load, self.ready = i, cap, dict.fromkeys(range(jobs)), 0, True
    monkeypatch.setattr(cluster, "local_ready", lambda: True)
    monkeypatch.setattr(cluster.config.cfg, "asr_max_rooms", 4)
    monkeypatch.setattr(cluster, "local_rooms", lambda: 2)
    monkeypatch.setattr(cluster, "mode", lambda c=None: "share")
    cluster.links.clear()
    assert cluster._choose() is None  # no helpers: here
    cluster.links.update({1: L(1, 4, 3), 2: L(2, 4, 1)})
    assert cluster._choose().id == 2  # 1 of 4 beats our 2 of 4
    cluster.links[2].jobs = dict.fromkeys(range(2))
    assert cluster._choose() is None  # a tie stays here
    monkeypatch.setattr(cluster, "mode", lambda c=None: "offload")
    assert cluster._choose().id == 2  # offload: helpers first
    assert cluster._choose({2}).id == 1
    cluster.links[1].jobs = dict.fromkeys(range(4))
    assert cluster._choose({2}) is None  # helpers full: we step in
    monkeypatch.setattr(cluster, "local_rooms", lambda: 4)
    assert cluster._choose({2}) is False  # everyone full
    cluster.links.clear()
