"""Caption behaviour ported from AT-LiveCaption, tested with synthetic input
(no speech model, no sound card, no internet)."""
import json
import math

import numpy as np
import pytest

from atsuit import asr, dsp
from atsuit.modules import captions


# ------------------------------------------------------------------- dsp --
def sine(freq, seconds=1.0, amp=0.1, sr=16000):
    t = np.arange(int(sr * seconds)) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def rms_db(x):
    return 20 * math.log10(float(np.sqrt(np.mean(np.square(x.astype(np.float64))))))


def test_gain_and_clipping():
    eq = dsp.GraphicEQ(gain_db=6)
    x = sine(1000)
    assert abs((rms_db(eq.process(x)) - rms_db(x)) - 6.0) < 0.05
    eq.set_gain_db(99)                      # clamped to +20 dB
    assert eq.gain_db == dsp.MAX_GAIN_DB
    assert float(np.max(np.abs(eq.process(sine(1000, amp=0.9))))) <= 1.0


def test_eq_band_boosts_its_own_frequency_only():
    i1k = dsp.EQ_BANDS_HZ.index(1000)
    bands = [0.0] * len(dsp.EQ_BANDS_HZ)
    bands[i1k] = 12.0
    eq = dsp.GraphicEQ(band_gains_db=bands)
    at_1k = rms_db(eq.process(sine(1000, 2))[8000:]) - rms_db(sine(1000, 2)[8000:])
    eq2 = dsp.GraphicEQ(band_gains_db=bands)
    at_100 = rms_db(eq2.process(sine(100, 2))[8000:]) - rms_db(sine(100, 2)[8000:])
    assert 10.5 < at_1k < 12.5, at_1k
    assert abs(at_100) < 0.5, at_100
    eq.set_band_gain_db(i1k, 50)            # clamped to +12
    assert eq.band_gains_db()[i1k] == dsp.MAX_BAND_GAIN_DB


def test_eq_is_continuous_across_chunks():
    """Chunk boundaries mustn't click: chunked output equals one-shot output."""
    bands = [3, -2, 0, 4, 0, -6, 5, 2]
    x = (sine(300, 1) + sine(2500, 1, 0.05)).astype(np.float32)
    whole = dsp.GraphicEQ(band_gains_db=bands).process(x)
    eq = dsp.GraphicEQ(band_gains_db=bands)
    parts = np.concatenate([eq.process(x[i:i + 1600]) for i in range(0, len(x), 1600)])
    assert np.allclose(whole, parts, atol=1e-5)


def test_flat_eq_is_a_passthrough_and_response_matches_rbj():
    x = sine(440)
    assert np.allclose(dsp.GraphicEQ().process(x), x, atol=1e-6)
    assert dsp.design_fir([0.0] * len(dsp.EQ_BANDS_HZ)) is None
    resp = dsp.eq_response([0, 0, 0, 0, 0, 6, 0, 0], np.array([1000.0, 100.0]))
    assert abs(20 * math.log10(resp[0]) - 6) < 0.01 and abs(20 * math.log10(resp[1])) < 0.2


def test_meter_and_rta():
    assert dsp.dbfs(np.zeros(1600, dtype=np.float32)) == dsp.SILENCE_FLOOR_DB
    full = sine(1000, 0.1, amp=1.0)
    assert abs(dsp.dbfs(full) - (-3.0)) < 0.1          # a full-scale sine is -3 dBFS RMS
    bands = dsp.compute_rta_bands(sine(1000, 0.1, amp=1.0))
    assert len(bands) == dsp.RTA_NUM_BANDS
    peak = int(np.argmax(bands))
    edges = dsp.rta_band_edges()
    assert edges[peak] <= 1000 < edges[peak + 1] and bands[peak] > -5
    assert dsp.compute_rta_bands(np.zeros(4, dtype=np.float32)) == [dsp.RTA_FLOOR_DB] * dsp.RTA_NUM_BANDS


# ------------------------------------------------------------- caption text --
def test_acronyms_music_and_words():
    assert asr.join_spelled_acronyms("THE U K AND N H S") == "THE UK AND NHS"
    assert asr.join_spelled_acronyms("A DOG") == "A DOG"
    assert asr.is_filler_spam("UM UM UH") and asr.is_filler_spam("hmm")
    assert not asr.is_filler_spam("UM HELLO") and not asr.is_filler_spam("")
    words = asr.words_from_tokens([" HE", "LLO", " U", " K"], [math.log(0.9), math.log(0.9), math.log(0.5), math.log(0.3)])
    assert [w["text"] for w in words] == ["HELLO", "U", "K"] and words[0]["confidence"] == 0.9
    merged = asr.merge_acronym_words(words)
    assert merged[1] == {"text": "UK", "confidence": 0.4}
    assert asr.tidy("U K", [], {"join_acronyms": False})[0] == "U K"


class FakeRecognizer:
    """Plays back a script of (text, endpoint) results, one per chunk fed."""

    def __init__(self, script):
        self.script = list(script)
        self.i = -1
        self.resets = 0

    def create_stream(self):
        return self

    def accept_waveform(self, sr, samples):
        assert sr == 16000 and samples.dtype == np.float32
        self.i += 1

    def is_ready(self, s):
        return False

    def decode_stream(self, s):
        pass

    def _cur(self):
        return self.script[min(self.i, len(self.script) - 1)]

    def is_endpoint(self, s):
        return self._cur()[1]

    def get_result_as_json_string(self, s):
        text = self._cur()[0]
        toks = [" " + w for w in text.split()]
        return json.dumps({"text": text, "tokens": toks, "ys_probs": [math.log(0.8)] * len(toks)})

    def reset(self, s):
        self.resets += 1


def feed_all(session, n):
    out = []
    for _ in range(n):
        out += session.feed(b"\x00\x00" * 160)
    return out


def test_commit_logic_partials_then_final():
    rec = FakeRecognizer([("HELLO", False), ("HELLO", False), ("HELLO THE U K", False), ("HELLO THE U K", True), ("", False), ("", True)])
    s = asr.Session(rec)
    ev = feed_all(s, 6)
    # repeats aren't re-sent; acronyms are joined; a silent endpoint sends nothing
    assert [(e["type"], e["text"]) for e in ev] == [("partial", "HELLO"), ("partial", "HELLO THE UK"), ("final", "HELLO THE UK")]
    assert ev[-1]["words"][-1]["text"] == "UK" and rec.resets == 2


def test_music_label_is_a_room_option():
    s = asr.Session(FakeRecognizer([("UM UM", True)]))
    assert feed_all(s, 1)[0] == {"type": "final", "text": "[MUSIC]", "words": []}
    s = asr.Session(FakeRecognizer([("UM UM", True)]), options={"music_label": False})
    assert feed_all(s, 1)[0]["text"] == "UM UM"


def test_running_room_switches_to_new_vocabulary_at_a_pause():
    eng = asr.Engine()
    old, new = FakeRecognizer([("ONE", False), ("ONE", True)]), FakeRecognizer([("TWO", True)])
    eng.recognizer, eng.state, eng.generation = old, "ready", 1
    s = eng.session()
    feed_all(s, 1)
    eng.recognizer, eng.generation = new, 2   # vocabulary saved: engine reloaded
    feed_all(s, 1)                             # the pause: ONE is final, then the switch
    assert s.r is new and s.generation == 2
    assert feed_all(s, 1)[0]["text"] == "TWO"


def test_hotwords_file_from_tokens(tmp_path):
    tokens = tmp_path / "tokens.txt"
    tokens.write_text("\n".join(f"{t} {i}" for i, t in enumerate(["<blk>", "▁LOVE", "LACE", "▁N", "H", "S", "▁DERM", "A"])))
    out = tmp_path / "hot.txt"
    skipped = asr.build_hotwords(["Lovelace", "NHS", "Zoë", " "], tokens, out)
    assert set(out.read_text().splitlines()) == {"▁LOVE LACE", "▁N H S"} and skipped == ["Zoë"]


# --------------------------------------------------------- srt / vtt export --
def test_srt_and_vtt_export():
    text = "[23:59:58] Good evening\n[00:00:01] and welcome to the show today everyone\nnoise line\n"
    srt = captions.to_srt(text)
    assert srt.startswith("1\n00:00:00,000 --> 00:00:03,000\nGood evening\n")
    assert "2\n00:00:03,000 --> 00:00:05,800\nand welcome" in srt            # crosses midnight
    vtt = captions.to_vtt(text)
    assert vtt.startswith("WEBVTT\n\n1\n00:00:00.000 --> 00:00:03.000")
    assert captions.to_vtt("") == "WEBVTT\n"


# ------------------------------------------------------------------ api --
def _rid(admin, name="HD"):
    return next(r["id"] for r in admin.get("/api/bootstrap").json()["rooms"] if r["name"] == name)


def _tech(admin, client):
    admin.post("/api/admin/accounts", json={"username": "ben", "password": "ben-password-1", "display_name": "Ben", "role": "tech"})
    t = client.__class__(client.app)
    assert t.post("/api/auth/login", json={"username": "ben", "password": "ben-password-1"}).status_code == 200
    return t


def test_techs_edit_room_caption_settings_and_screens_follow(admin, client):
    rid = _rid(admin)
    tech = _tech(admin, client)
    anon = client.__class__(client.app)
    s = tech.get(f"/api/captions/{rid}/settings").json()
    assert s["appearance"]["audience"]["max_lines"] == 3 and s["appearance"]["overlay"]["background_opacity"] == 0.6
    assert s["eq_bands_hz"] == dsp.EQ_BANDS_HZ and s["hotwords_score"] == 2.5 and s["music_label"] is True
    assert anon.get(f"/api/captions/{rid}/settings").status_code == 401
    with anon.websocket_connect(f"/ws?topics=captions:{rid}") as ws:
        assert ws.receive_json()["type"] == "hello"
        r = tech.put(f"/api/captions/{rid}/settings", json={"appearance": {"bar": {"font_size": 72, "text_color": "#FF7A1A", "max_lines": 99}},
                                                           "gain_db": 6, "eq_bands": [{"index": 2, "gain_db": 30}], "music_label": False})
        assert r.status_code == 200, r.text
        evt = ws.receive_json()
        assert evt["type"] == "appearance" and evt["data"]["bar"]["font_size"] == 72 and evt["data"]["bar"]["max_lines"] == 20
    out = r.json()
    assert out["gain_db"] == 6 and out["eq_band_gains_db"][2] == 12 and out["music_label"] is False
    # caption screens are public and get the looks with their catch-up
    rec = anon.get(f"/api/captions/{rid}/recent").json()
    assert rec["appearance"]["bar"]["text_color"] == "#FF7A1A" and rec["appearance"]["audience"]["font_size"] == 64
    # the room's live audio chain follows the settings
    st = captions.rooms[rid]
    assert st.eq.gain_db == 6 and st.eq.band_gains_db()[2] == 12 and st.options["music_label"] is False
    # nonsense is refused
    for bad in ({"bar": {"text_color": "red;}"}}, {"bar": {"font_family": "x</style>"}}, {"nope": {}}, {"bar": {"position": "left"}}):
        assert tech.put(f"/api/captions/{rid}/settings", json={"appearance": bad}).status_code == 422
    assert tech.put(f"/api/captions/{rid}/settings", json={"reset_appearance": "bar"}).json()["appearance"]["bar"]["font_size"] == 56
    # vocabulary and boost strength: tech-editable now
    assert tech.put(f"/api/captions/{rid}/settings", json={"vocabulary": "Lovelace\nNHS", "hotwords_score": 3.5}).status_code == 200
    assert "Lovelace" in captions.all_vocabulary()
    assert tech.get(f"/api/captions/{rid}/settings").json()["hotwords_score"] == 3.5
    assert tech.put(f"/api/captions/rooms/{rid}", json={"enabled": True, "vocabulary": "Ada", "record": False}).status_code == 200


def test_corrections_history_and_transcripts(admin, client):
    rid = _rid(admin)
    tech = _tech(admin, client)
    tech.post(f"/api/captions/{rid}/test", json={"text": "welcome to love lace hall"})
    hist = tech.get(f"/api/captions/{rid}/history").json()
    assert hist[0]["text"] == "welcome to love lace hall" and hist[0]["words"][0]["text"] == "welcome"
    r = tech.post(f"/api/captions/{rid}/corrections", json={"original": "love lace", "corrected": "Lovelace"}).json()
    assert r["added_to_vocabulary"] is True
    assert tech.post(f"/api/captions/{rid}/corrections", json={"original": "lovelace", "corrected": "lovelace"}).json()["added_to_vocabulary"] is False
    assert tech.get(f"/api/captions/{rid}/settings").json()["vocabulary"] == "Lovelace"
    corr = tech.get(f"/api/captions/{rid}/corrections").json()
    assert corr[0]["corrected"] == "lovelace" and corr[1]["original"] == "love lace" and corr[1]["by"] == "Ben"
    assert tech.post(f"/api/captions/{rid}/history/clear").json()["ok"] and tech.get(f"/api/captions/{rid}/history").json() == []
    # Start/stop a transcript at any time; only finals are written.
    start = tech.post(f"/api/captions/{rid}/transcript/start").json()
    assert tech.get(f"/api/captions/{rid}/settings").json()["recording"] is True
    tech.post(f"/api/captions/{rid}/test", json={"text": "a live line", "final": False})
    tech.post(f"/api/captions/{rid}/test", json={"text": "first line"})
    tech.post(f"/api/captions/{rid}/test", json={"text": "second line"})
    assert tech.delete(f"/api/captions/transcripts/{start['id']}").status_code == 409   # still recording
    tech.post(f"/api/captions/{rid}/transcript/stop")
    tx = tech.get(f"/api/captions/transcripts?room_id={rid}").json()
    assert tx[0]["id"] == start["id"] and tx[0]["ended_at"] and tx[0]["size"] > 0
    body = tech.get(f"/api/captions/transcripts/{start['id']}").text
    assert "first line" in body and "second line" in body and "a live line" not in body
    srt = tech.get(f"/api/captions/transcripts/{start['id']}/export.srt")
    assert srt.status_code == 200 and "first line" in srt.text and "-->" in srt.text and "attachment" in srt.headers["content-disposition"]
    assert tech.get(f"/api/captions/transcripts/{start['id']}/export.vtt").text.startswith("WEBVTT")
    assert tech.get(f"/api/captions/transcripts/{start['id']}/export.exe").status_code == 404
    second = tech.post(f"/api/captions/{rid}/transcript/start").json()       # a new file every time
    assert second["filename"] != start["filename"]
    tech.post(f"/api/captions/{rid}/transcript/stop")
    assert tech.delete(f"/api/captions/transcripts/{start['id']}").status_code == 200
    assert tech.get(f"/api/captions/transcripts/{start['id']}").status_code == 404
