"""AI helper: answers from the venue's notes and past fixes, learns from techs,
and only sends the bare question out when web search is on."""
from atsuit.modules import aihelper


def _tech(admin):
    admin.post("/api/admin/accounts", json={"username": "ben", "password": "ben-password-1", "display_name": "Ben", "role": "tech"})
    t = admin.__class__(admin.app)
    t.post("/api/auth/login", json={"username": "ben", "password": "ben-password-1"})
    return t


def test_answers_from_what_the_venue_knows(admin, monkeypatch):
    calls = []
    monkeypatch.setattr(aihelper.httpx, "get", lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(AssertionError("no web")))
    tech = _tech(admin)
    room = admin.get("/api/bootstrap").json()["rooms"][0]["id"]
    assert tech.get("/api/ai").json() == {"enabled": True, "name": "AI helper", "model": False, "web_search": False}

    # nothing known yet (and the built-in guide off): it says so, and nothing went anywhere
    admin.put("/api/admin/ai", json={"builtin_guide": False})
    r = tech.post("/api/ai/ask", json={"question": "The lectern mic is dead", "room_id": room}).json()
    assert "don't know" in r["answer"] and r["sources"] == [] and r["model"] is False and calls == []

    # a help call fixed and written up is what it offers next time
    h = tech.post("/api/comms/help", json={"room_id": room, "category": "Audio", "description": "lectern mic dead"}).json()
    tech.put(f"/api/comms/help/{h['id']}", json={"status": "resolved", "resolution": "Swap the AA batteries in the lectern beltpack (spares in the tech desk drawer)"})
    admin.put("/api/admin/ai", json={"venue_notes": "Hall projector\nUse input HDMI 2. The remote is on the lectern shelf.\n\nWifi\nCrew network is ATCREW."})
    r = tech.post("/api/ai/ask", json={"question": "lectern mic not working", "room_id": room}).json()
    assert "beltpack" in r["answer"] and r["sources"][0]["kind"] == "past help call"
    r = tech.post("/api/ai/ask", json={"question": "projector shows no picture"}).json()
    assert "HDMI 2" in r["answer"] and r["sources"][0]["kind"] == "venue notes"

    # "This fixed it" teaches it
    tech.post("/api/ai/learn", json={"question": "Confidence monitor flickers", "answer": "Reseat the SDI cable at the stage box", "room_id": room})
    assert "SDI" in tech.post("/api/ai/ask", json={"question": "confidence monitor flickering"}).json()["answer"]
    learned = admin.get("/api/admin/ai").json()["learned"]
    assert learned[0]["added_by"] == "Ben"
    admin.delete(f"/api/admin/ai/learned/{learned[0]['id']}")
    assert "SDI" not in tech.post("/api/ai/ask", json={"question": "confidence monitor flickering"}).json()["answer"]

    # with a local model it writes the answer from those notes
    seen = {}

    def fake_model(url, model, prompt):
        seen.update(url=url, model=model, prompt=prompt)
        return "1. Check the beltpack batteries."
    monkeypatch.setattr(aihelper, "generate", fake_model)
    admin.put("/api/admin/ai", json={"ai_url": "http://10.0.0.5:11434", "ai_model": "llama3.1:8b"})
    r = tech.post("/api/ai/ask", json={"question": "lectern mic not working", "room_id": room}).json()
    assert r == {**r, "answer": "1. Check the beltpack batteries.", "model": True}
    assert "AA batteries" in seen["prompt"] and seen["url"] == "http://10.0.0.5:11434"

    # turned off: nothing answers
    admin.put("/api/admin/ai", json={"enabled": False})
    assert tech.post("/api/ai/ask", json={"question": "anything"}).status_code == 404


def test_web_search_sends_only_the_question(admin, monkeypatch):
    sent = []

    class R:
        def raise_for_status(self): pass
        def json(self): return {"Heading": "XLR connector", "AbstractText": "Pin 1 is ground.", "AbstractURL": "https://example.org/xlr"}

    monkeypatch.setattr(aihelper.httpx, "get", lambda url, params, timeout: sent.append((url, params)) or R())
    tech = _tech(admin)
    admin.put("/api/admin/ai", json={"venue_notes": "Secret client: ACME launch", "web_search": True})
    r = tech.post("/api/ai/ask", json={"question": "xlr pin out"}).json()
    assert "Pin 1 is ground" in r["answer"] and r["sources"][-1]["url"] == "https://example.org/xlr"
    assert sent[0][0] == aihelper.SEARCH and sent[0][1]["q"] == "xlr pin out" and "ACME" not in str(sent)


def test_the_built_in_tech_guide(admin, monkeypatch):
    monkeypatch.setattr(aihelper.httpx, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no web")))
    tech = _tech(admin)
    assert len(aihelper.guide()) > 50 and all(g["title"] and g["text"] and "Keywords" not in g["text"] for g in aihelper.guide())
    for question, title in [("the radio mic keeps cutting out", "Radio mic"), ("projector says no signal", "No signal"),
                            ("howling from the speakers", "Feedback"), ("remote people hear an echo on teams", "echo"),
                            ("speaker is overrunning", "overrunning"), ("hum from the laptop", "Hum")]:
        r = tech.post("/api/ai/ask", json={"question": question}).json()
        assert r["sources"][0] == {"kind": "tech guide", "title": r["sources"][0]["title"]} and title in r["sources"][0]["title"], (question, r)
        assert r["answer"].startswith("From the tech guide") and "1." in r["answer"]

    # The venue's own knowledge comes first, the guide after it.
    admin.put("/api/admin/ai", json={"venue_notes": "Hall A projector no signal\nUse input HDMI 2 on the wall plate."})
    r = tech.post("/api/ai/ask", json={"question": "Hall A projector no signal"}).json()
    assert [s["kind"] for s in r["sources"]][:2] == ["venue notes", "tech guide"]
    assert r["answer"].index("HDMI 2") < r["answer"].index("From the tech guide")

    # A model gets both.
    seen = {}
    monkeypatch.setattr(aihelper, "generate", lambda url, model, prompt: seen.update(prompt=prompt) or "Use HDMI 2.")
    admin.put("/api/admin/ai", json={"venue_notes": "Hall A projector no signal\nUse input HDMI 2.", "ai_url": "http://10.0.0.5:11434"})
    tech.post("/api/ai/ask", json={"question": "Hall A projector no signal"})
    assert "[venue notes]" in seen["prompt"] and "[tech guide]" in seen["prompt"]

    # Turned off, it isn't used.
    admin.put("/api/admin/ai", json={"builtin_guide": False})
    assert admin.get("/api/admin/ai").json()["settings"]["builtin_guide"] is False
    assert tech.post("/api/ai/ask", json={"question": "radio mic keeps cutting out"}).json()["sources"] == []
