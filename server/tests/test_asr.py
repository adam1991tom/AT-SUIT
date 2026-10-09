"""End-to-end captioning with the real model. Runs when ATSUIT_TEST_MODELS
points at a folder holding the sherpa-onnx model (CI downloads it)."""
import os
import time
import wave
from pathlib import Path

import pytest
from licensed_server import licence_key

MODELS = os.getenv("ATSUIT_TEST_MODELS")
pytestmark = pytest.mark.skipif(not MODELS, reason="set ATSUIT_TEST_MODELS to run")


def test_node_audio_becomes_captions(tmp_path, monkeypatch):
    monkeypatch.setenv("ATSUIT_DATA", str(tmp_path))
    monkeypatch.setenv("ATSUIT_ASR", "1")
    (tmp_path / "models").symlink_to(MODELS)
    from atsuit import config, security
    config.reload()
    security.reset_keys()
    from fastapi.testclient import TestClient
    from atsuit.main import create_app
    from atsuit import asr

    with TestClient(create_app()) as c:
        c.post("/api/setup", json={"organisation": "O", "site_name": "S", "admin_username": "admin",
                                   "admin_password": "correct-horse", "rooms": ["CC"], "licence_key": licence_key()})
        for _ in range(120):
            if asr.engine.state in ("ready", "error", "unavailable"):
                break
            time.sleep(0.5)
        assert asr.engine.state == "ready", asr.engine.status()
        code = c.get("/api/fleet/enrolment").json()[0]["enrol_code"]
        tok = c.post("/api/nodes/enrol", json={"code": code, "name": "mic1"}).json()["token"]
        rid = c.get("/api/bootstrap").json()["rooms"][0]["id"]
        wav = next(Path(MODELS).glob("*/test_wavs/0.wav"), None) or Path(os.environ["ATSUIT_TEST_WAV"])
        with wave.open(str(wav)) as w:
            assert w.getframerate() == 16000 and w.getnchannels() == 1
            pcm = w.readframes(w.getnframes())
        silence = b"\x00\x00" * 16000 * 3
        texts = []
        with c.websocket_connect(f"/ws?topics=captions:{rid}") as viewer:
            assert viewer.receive_json()["type"] == "hello"
            with c.websocket_connect(f"/ws/audio/{rid}?node_token={tok}") as mic:
                assert mic.receive_json()["type"] == "ready"
                data = pcm + silence
                for i in range(0, len(data), 3200):
                    mic.send_bytes(data[i:i + 3200])
                deadline = time.time() + 30
                while time.time() < deadline:
                    evt = viewer.receive_json()
                    if evt["type"] == "final":
                        texts.append(evt["data"]["text"])
                        break
        final = " ".join(texts).upper()
        assert "YELLOW LAMPS" in final, final
