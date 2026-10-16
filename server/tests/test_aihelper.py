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
        src = r["sources"][0]
        assert src == {"kind": "tech guide", "title": src["title"], "url": src["url"]} and title in src["title"], (question, r)
        assert src["url"].startswith("/guide/help#")  # the full entry, on the Help page
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


def _chat(tech, turns, state=None, **extra):
    msgs = [{"role": "tech" if i % 2 == 0 else "helper", "text": t} for i, t in enumerate(turns)]
    r = tech.post("/api/ai/chat", json={"messages": msgs, **({"state": state} if state else {}), **extra})
    assert r.status_code == 200, r.text
    return r.json()


class Talk:
    """A conversation the way the workspace's window keeps it."""

    def __init__(self, tech, **extra):
        self.tech, self.turns, self.state, self.extra = tech, [], None, extra

    def say(self, text):
        self.turns.append(text)
        r = _chat(self.tech, self.turns, self.state, **self.extra)
        self.turns.append(r["answer"])
        self.state = r["state"]
        return r


def test_a_conversation_without_a_model(admin, monkeypatch):
    monkeypatch.setattr(aihelper.httpx, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no web")))
    tech = _tech(admin)
    talk = Talk(tech)

    # It finds the guide entry, gives its steps and asks the first question, with buttons for the answers.
    r = talk.say("The lectern mic is dead")
    assert "No sound from a microphone" in r["answer"] and "meter" in r["answer"]
    assert r["choices"] == ["Yes", "No"] and r["state"]["ask"] == "meter" and r["sources"][0]["kind"] == "tech guide"

    # Typed answers follow the tree, in the tech's own words.
    r = talk.say("nope nothing")
    assert "radio" in r["answer"].lower() and r["state"]["ask"] == "wireless"
    r = talk.say("yes")
    assert "RF" in r["answer"] and r["state"]["ask"] == "rf"
    r = talk.say("No")
    assert "batteries" in r["answer"] and r["choices"] == ["Fixed", "Still not working"]

    # Still broken: something else, not the same entry again.
    r = talk.say("Still not working")
    assert "No sound from a microphone" not in r["answer"] and "Let's try something else" in r["answer"]

    # A follow-up question in the middle of it is read as a new question.
    r = talk.say("what if it's a condenser mic with no phantom power")
    assert "48V" in r["answer"] or "phantom" in r["answer"].lower()

    # Fixed: it says so and the window offers "This fixed it".
    r = talk.say("that's fixed it, thanks")
    assert r["done"] is True and "glad" in r["answer"].lower()


def test_chat_venue_first_and_running_out(admin, monkeypatch):
    monkeypatch.setattr(aihelper.httpx, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no web")))
    tech = _tech(admin)
    room = admin.get("/api/bootstrap").json()["rooms"][0]["id"]
    admin.put("/api/admin/ai", json={"venue_notes": "Hall A projector no signal\nUse input HDMI 2 on the wall plate; the remote is on the lectern shelf."})
    talk = Talk(tech, room_id=room)
    r = talk.say("Hall A projector no signal")
    assert r["answer"].index("HDMI 2") < r["answer"].index("From the tech guide")
    assert [s["kind"] for s in r["sources"]][:2] == ["venue notes", "tech guide"]

    # Something nobody knows: it asks for more and offers to call for help, in the right category.
    talk = Talk(tech, room_id=room)
    r = talk.say("the zorblatt is grommiting")
    assert r["choices"] == ["Call for help"] and r["help"] == {"category": "Other", "description": "the zorblatt is grommiting"}
    r = talk.say("still nothing")
    assert "run out of ideas" in r["answer"] and r["help"]

    # The help category comes from the topic it was on.
    talk = Talk(tech)
    talk.say("the lights are not responding to the desk")
    talk.say("every light")
    for _ in range(4):
        r = talk.say("tried that, still not working")
    assert r["help"]["category"] == "Lighting"

    # The last message has to be the tech's; nothing else is accepted.
    bad = tech.post("/api/ai/chat", json={"messages": [{"role": "helper", "text": "hi"}]})
    assert bad.status_code == 400
    assert tech.post("/api/ai/chat", json={"messages": [{"role": "boss", "text": "hi"}]}).status_code == 422


def test_chat_with_a_model(admin, monkeypatch):
    seen = {}

    def fake(url, model, messages):
        seen.update(url=url, model=model, messages=messages)
        return "Is the channel's meter moving?"
    monkeypatch.setattr(aihelper, "chat_model", fake)
    tech = _tech(admin)
    admin.put("/api/admin/ai", json={"ai_url": "http://10.0.0.5:11434", "venue_notes": "Lectern mic\nThe lectern mic is input 12; its phantom is on the stage box."})
    r = _chat(tech, ["the lectern mic is dead", "Check it's switched on. Is the meter moving?", "no, and the cable is fine"])
    assert r["model"] is True and r["answer"] == "Is the channel's meter moving?" and r["choices"] == []
    system, *rest = seen["messages"]
    assert system["role"] == "system" and "input 12" in system["content"] and "No sound from a microphone" in system["content"]
    assert "venue is right" in system["content"]
    assert [m["role"] for m in rest] == ["user", "assistant", "user"] and rest[-1]["content"] == "no, and the cable is fine"

    # No model reachable: it still talks, from the guide.
    monkeypatch.setattr(aihelper, "chat_model", lambda *a: (_ for _ in ()).throw(aihelper.httpx.ConnectError("down")))
    r = _chat(tech, ["the lectern mic is dead"])
    assert r["model"] is False and r["choices"] == ["Yes", "No"]


def test_admins_edit_the_guide(admin, monkeypatch, tmp_path):
    tech = _tech(admin)
    topics = {t["name"]: t for t in admin.get("/api/admin/ai/guide").json()["topics"]}
    assert topics["audio"]["source"] == "built-in" and topics["audio"]["questions"] > 5 and "staging" in topics
    assert tech.get("/api/admin/ai/guide").status_code == 403

    # Their copy replaces the built-in one, for the helper and on the help page.
    audio = admin.get("/api/admin/ai/guide/audio").json()
    assert audio["edited"] is False and audio["problems"] == []
    own = audio["markdown"].replace("## No sound from a microphone", "## No sound from a microphone\n1. In Hall A, the spare mics are in the blue case.", 1)
    assert admin.put("/api/admin/ai/guide/audio", json={"markdown": own}).json() == {"ok": True, "problems": []}
    assert admin.get("/api/admin/ai/guide").json()["topics"][1]["source"] == "edited"
    assert "blue case" in _chat(tech, ["the lectern mic is dead"])["answer"]
    assert "blue case" in admin.get("/api/guide/help").json()["articles"][1]["markdown"] or any(
        "blue case" in a["markdown"] for a in admin.get("/api/guide/help").json()["articles"])

    # Mistakes in the questions are pointed out.
    bad = "# Venue\n\n## Doors\nKeywords: doors\n1. Open them.\n\n### Ask a: Are they locked?\n- Yes: Get the key. → b\n"
    r = admin.put("/api/admin/ai/guide/venue", json={"markdown": bad}).json()
    assert r["problems"] == ["Doors: a → b: there's no question b"]
    assert {t["name"]: t["source"] for t in admin.get("/api/admin/ai/guide").json()["topics"]}["venue"] == "added"
    assert admin.put("/api/admin/ai/guide/..%2Fx", json={"markdown": "x"}).status_code in (400, 404)
    assert admin.put("/api/admin/ai/guide/Bad Name", json={"markdown": "x"}).status_code == 400

    # Reset brings the built-in one back; an added topic goes.
    admin.delete("/api/admin/ai/guide/audio")
    admin.delete("/api/admin/ai/guide/venue")
    assert "blue case" not in _chat(tech, ["the lectern mic is dead"])["answer"]
    assert "venue" not in {t["name"] for t in admin.get("/api/admin/ai/guide").json()["topics"]}


def test_the_help_page_reads_the_questions_out():
    md = aihelper.readable("# A\n\n## B\nKeywords: x\n1. Do it.\n\n### Ask q1: Is it on?\n- Yes: Good. → q2\n- No: Turn it on.\n\n### Ask q2: Is it loud?\n- Yes: Turn it down.\n")
    assert "Keywords" not in md and "#### Is it on?" in md and "- **Yes:** Good. (Next: Is it loud?)" in md and "- **No:** Turn it on." in md
