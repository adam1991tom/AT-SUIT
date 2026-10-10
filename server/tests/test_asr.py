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


def test_vocabulary_steers_the_recogniser(tmp_path, monkeypatch, capfd):
    """Vocabulary words reach sherpa-onnx and change what it hears: with "Yellow Lambs" in the
    vocabulary, the test recording's "yellow lamps" comes out as LAMBS."""
    monkeypatch.setenv("ATSUIT_DATA", str(tmp_path))
    monkeypatch.setenv("ATSUIT_ASR", "1")
    (tmp_path / "models").symlink_to(MODELS)
    from atsuit import asr, config
    config.reload()
    wav = next(Path(MODELS).glob("*/test_wavs/0.wav"), None) or Path(os.environ["ATSUIT_TEST_WAV"])
    with wave.open(str(wav)) as w:
        pcm = w.readframes(w.getnframes())
    import numpy as np
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768

    def hear(vocabulary):
        eng = asr.Engine()
        eng.load(vocabulary=vocabulary, download=False)
        assert eng.state == "ready", eng.status()
        r = eng.recognizer
        s = r.create_stream()
        s.accept_waveform(16000, audio)
        s.accept_waveform(16000, np.zeros(32000, dtype=np.float32))
        s.input_finished()
        while r.is_ready(s):
            r.decode_stream(s)
        return r.get_result(s), eng

    plain, _ = hear([])
    assert "YELLOW LAMPS" in plain
    boosted, eng = hear(["Yellow Lambs", "Zoë"])
    assert "YELLOW LAMBS" in boosted, boosted
    assert eng.skipped_vocab == ["Zoë"]
    assert "Cannot find ID" not in capfd.readouterr().err
