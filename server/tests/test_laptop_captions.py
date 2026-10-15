"""Captions made on the laptop: the app gets the model from this server and sends text, not audio."""
import json

from atsuit import config
from atsuit.modules import captions

TOKENS = ["<blk>", "▁LOVE", "LACE", "▁N", "H", "S", "▁HELLO", "▁WORLD", "▁UM"]


def _laptop(admin, name="hd-main"):
    code = admin.get("/api/fleet/enrolment").json()[0]["enrol_code"]
    n = admin.post("/api/nodes/enrol", json={"code": code, "name": name, "kind": "tech"}).json()
    return n["token"], {"Authorization": f"Node {n['token']}"}


def _rid(admin, name="HD"):
    return next(r["id"] for r in admin.get("/api/bootstrap").json()["rooms"] if r["name"] == name)


def _fake_model():
    d = config.cfg.models / config.cfg.asr_model
    d.mkdir(parents=True, exist_ok=True)
    (d / "tokens.txt").write_text("\n".join(f"{t} {i}" for i, t in enumerate(TOKENS)))
    for part in ("encoder", "decoder", "joiner"):
        (d / f"{part}-epoch-99.int8.onnx").write_bytes(part.encode() * 10)


def test_laptops_get_the_model_and_vocabulary(admin, monkeypatch):
    _, hdr = _laptop(admin)
    captions._prep.update(state="idle", key="")
    assert admin.get("/api/nodes/captions/model").status_code in (401, 403)  # people can't, laptops can

    # No model on the server yet: it starts fetching it and the laptop waits.
    assert admin.get("/api/nodes/captions/model", headers=hdr).json()["state"] == "off"  # this server hands out no model
    monkeypatch.setattr(config.cfg, "laptop_models", True)  # e.g. hosted: it never listens, but laptops get the model
    started = []
    monkeypatch.setattr(captions.asr, "download_model", lambda *a, **k: started.append(a))
    r = admin.get("/api/nodes/captions/model", headers=hdr).json()
    assert r["state"] == "downloading"

    _fake_model()
    captions._prep.update(state="idle", key="")
    admin.put(f"/api/captions/{_rid(admin)}/settings", json={"vocabulary": "Lovelace\nNHS"})
    m = admin.get("/api/nodes/captions/model", headers=hdr).json()
    assert m["state"] == "ready" and m["model"] == config.cfg.asr_model and m["hotwords_score"] == 2.5
    files = {f["name"]: f for f in m["files"]}
    assert set(files) == {"encoder.onnx", "decoder.onnx", "joiner.onnx", "tokens.txt", "hotwords.txt"}
    assert files["encoder.onnx"]["size"] == 70 and files["hotwords.txt"]["small"] and not files["encoder.onnx"]["small"]

    hot = admin.get("/api/nodes/captions/model/hotwords.txt", headers=hdr)
    assert hot.status_code == 200 and set(hot.text.splitlines()) == {"love LACE", "n H S"}
    assert admin.get("/api/nodes/captions/model/encoder.onnx", headers=hdr).content == b"encoder" * 10
    assert admin.get("/api/nodes/captions/model/../config.json", headers=hdr).status_code == 404
    assert admin.get("/api/nodes/captions/model/encoder.onnx").status_code in (401, 403)

    # New vocabulary: a new key, so laptops fetch the small files again.
    admin.put(f"/api/captions/{_rid(admin)}/settings", json={"vocabulary": "Lovelace"})
    m2 = admin.get("/api/nodes/captions/model", headers=hdr).json()
    assert m2["state"] == "ready" and m2["key"] != m["key"]
    assert admin.get("/api/nodes/captions/model/hotwords.txt", headers=hdr).text.splitlines() == ["love LACE"]

    # Turned off: laptops send audio to the server as before.
    assert admin.put("/api/admin/settings", json={"captions_on_laptops": False}).status_code == 200
    assert admin.get("/api/admin/settings").json()["captions_on_laptops"] is False
    assert admin.get("/api/nodes/captions/model", headers=hdr).json()["state"] == "off"
    assert admin.get("/api/nodes/me", headers=hdr).json()["captions_on_laptops"] is False


def test_a_laptop_sends_text_and_the_screens_get_captions(admin):
    tok, _ = _laptop(admin)
    rid = _rid(admin)
    admin.put(f"/api/captions/{rid}/settings", json={"gain_db": 4})
    with admin.websocket_connect(f"/ws?topics=captions:{rid}") as viewer:
        assert viewer.receive_json()["type"] == "hello"
        with admin.websocket_connect(f"/ws/audio/{rid}?node_token={tok}&mode=local") as mic:
            ready = mic.receive_json()
            assert ready["type"] == "ready" and ready["mode"] == "local"
            assert ready["audio"]["gain_db"] == 4 and len(ready["audio"]["eq_bands_hz"]) == len(ready["audio"]["eq_band_gains_db"])
            status = viewer.receive_json()
            assert status["type"] == "status" and status["data"]["live"] and status["data"]["local"]
            assert captions.rooms[rid].local and captions.rooms[rid].session is None

            mic.send_text(json.dumps({"type": "level", "db": -18.5, "rta": [-30.0] * 31}))
            evt = viewer.receive_json()
            assert evt["type"] == "level" and evt["data"]["db"] == -18.5 and len(evt["data"]["rta"]) == 31

            mic.send_text("not json")  # ignored, the connection stays up
            mic.send_text(json.dumps({"type": "partial", "text": "HELLO", "tokens": [" HELLO"], "ys_probs": [-0.1]}))
            evt = viewer.receive_json()
            assert evt["type"] == "partial" and evt["data"]["text"].lower() == "hello"
            mic.send_text(json.dumps({"type": "final", "text": "HELLO WORLD", "tokens": [" HELLO", " WORLD"], "ys_probs": [-0.1, -0.2]}))
            evt = viewer.receive_json()
            assert evt["type"] == "final" and evt["data"]["text"].lower() == "hello world"

            # A tech changes the room's EQ: the laptop hears about it at once.
            admin.put(f"/api/captions/{rid}/settings", json={"eq_bands": [{"index": 3, "gain_db": 5}]})
            msg = mic.receive_json()
            assert msg["type"] == "audio" and msg["audio"]["eq_band_gains_db"][3] == 5
        evt = viewer.receive_json()
        assert evt["type"] == "status" and not evt["data"]["live"]
    assert not captions.rooms[rid].local
    assert "hello world" in captions.rooms[rid].finals[-1].lower()


def test_caption_text_stays_on_the_laptops(admin):
    tok, hdr = _laptop(admin)
    rid = _rid(admin)
    admin.put(f"/api/captions/{rid}/settings", json={"record": True})
    assert admin.put("/api/admin/settings", json={"captions_private": True, "captions_on_laptops": False}).status_code == 200
    me = admin.get("/api/nodes/me", headers=hdr).json()
    assert me["captions_private"] is True and me["transcript_keep_hours"] == 12

    # Nothing falls back to the server: it won't take audio, and won't start a transcript.
    with admin.websocket_connect(f"/ws/audio/{rid}?node_token={tok}") as mic:
        assert mic.receive_json() == {"type": "error", "message": "Captions here are made on the laptops: use the AT-SUIT app"}
    assert admin.post(f"/api/captions/{rid}/transcript/start").status_code == 409

    with admin.websocket_connect(f"/ws?topics=captions:{rid}") as viewer:
        assert viewer.receive_json()["type"] == "hello"
        with admin.websocket_connect(f"/ws/audio/{rid}?node_token={tok}&mode=local") as mic:
            ready = mic.receive_json()
            assert ready["type"] == "ready" and ready["private"] is True
            assert viewer.receive_json()["data"]["live"]
            # Text the laptop sends anyway is dropped unread; the level still shows on the console.
            mic.send_text(json.dumps({"type": "final", "text": "SECRET PLANS", "tokens": [], "ys_probs": []}))
            mic.send_text(json.dumps({"type": "level", "db": -20, "rta": []}))
            evt = viewer.receive_json()
            assert evt["type"] == "level"
        assert not viewer.receive_json()["data"]["live"]
    st = captions.rooms[rid]
    assert not any("SECRET" in f for f in st.finals) and st.transcript is None
    assert "SECRET" not in str(admin.get(f"/api/captions/{rid}/recent").json())
