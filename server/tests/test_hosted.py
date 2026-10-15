"""Hosted mode (cloud/): setting up needs the code from the provider's setup link, the provider
updates the venue, and atcloud's commands inside the container."""
import json
import sqlite3

import pytest
from licensed_server import licence_key

from atsuit import config, db, hosted
from atsuit.modules import aihelper

SETUP = {"organisation": "Grand Hall", "site_name": "Main", "admin_username": "admin",
         "admin_password": "correct-horse", "rooms": ["CC"], "licence_key": ""}


@pytest.fixture()
def hosted_env(monkeypatch):
    monkeypatch.setenv("ATSUIT_HOSTED", "1")
    monkeypatch.setenv("ATSUIT_SETUP_CODE", "s3cret-code")


def test_a_hosted_server_is_set_up_only_from_the_setup_link(hosted_env, client):
    assert client.get("/api/public/hosting").json() == {"hosted": True, "setup_code_needed": True, "licensed": False}
    assert client.post("/api/setup", json=SETUP).status_code == 403
    assert client.post("/api/setup", json=SETUP, headers={"X-Setup-Code": "wrong"}).status_code == 403
    assert client.get("/api/setup/status").json()["setup_complete"] is False
    r = client.post("/api/setup", json=SETUP, headers={"X-Setup-Code": "s3cret-code"})
    assert r.status_code == 200, r.text
    assert client.get("/api/setup/status").json()["setup_complete"] is True


def test_a_hosted_server_with_no_code_cant_be_set_up_at_all(monkeypatch, client):
    monkeypatch.setenv("ATSUIT_HOSTED", "1")
    config.reload()
    assert client.post("/api/setup", json=SETUP, headers={"X-Setup-Code": ""}).status_code == 403


def test_a_venue_server_can_use_a_setup_code_too(monkeypatch, client):
    monkeypatch.setenv("ATSUIT_SETUP_CODE", "abc123")
    config.reload()
    assert client.get("/api/public/hosting").json()["setup_code_needed"] is True
    assert client.post("/api/setup", json=SETUP).status_code == 403
    assert client.post("/api/setup", json=SETUP, headers={"X-Setup-Code": "abc123"}).status_code == 200


def test_a_venue_server_without_a_code_sets_up_as_before(client):
    assert client.get("/api/public/hosting").json() == {"hosted": False, "setup_code_needed": False, "licensed": False}
    assert client.post("/api/setup", json=SETUP).status_code == 200


def test_the_provider_updates_a_hosted_venue(hosted_env, client):
    client.post("/api/setup", json={**SETUP, "licence_key": licence_key()}, headers={"X-Setup-Code": "s3cret-code"})
    u = client.get("/api/admin/updates").json()
    assert u["hosted"] is True and u["available"] is False and "provider" in u["detail"]
    assert client.post("/api/admin/updates/check").json()["hosted"] is True
    for method in ("post", "delete"):
        r = getattr(client, method)("/api/admin/updates/install")
        assert r.status_code == 400 and "provider" in r.json()["detail"]
    assert client.put("/api/admin/updates", json={"mode": "auto"}).status_code == 400


def test_the_licence_can_go_in_before_the_customer_sets_up(hosted_env, client, tmp_path, capsys):
    key = tmp_path / "key.txt"
    key.write_text(licence_key(licensee="Grand Hall"))
    assert hosted.main(["licence", str(key)]) == 0
    assert json.loads(capsys.readouterr().out)["licensee"] == "Grand Hall"
    assert client.get("/api/public/hosting").json()["licensed"] is True
    # the setup page leaves the licence step out, and setup keeps the key
    assert client.post("/api/setup", json=SETUP, headers={"X-Setup-Code": "s3cret-code"}).status_code == 200
    assert hosted.main(["info"]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["setup_complete"] and info["licence"]["valid"] and info["licence"]["licensee"] == "Grand Hall"
    assert info["rooms"] == 1 and info["accounts"] == 1 and not info["licence"]["locked"]
    key.write_text("not a key")
    assert hosted.main(["licence", str(key)]) == 1


def test_the_nightly_snapshot_is_a_whole_database(admin):
    with db.tx() as c:
        db.set_setting(c, "snapshot_probe", "here")
    out = hosted.snapshot()
    assert out == config.cfg.data / ".snapshot" / "atsuit.db"
    c = sqlite3.connect(out)
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert c.execute("SELECT value FROM settings WHERE key='snapshot_probe'").fetchone()[0] == '"here"'


def test_the_provider_publishes_the_windows_app(admin, tmp_path):
    rel = tmp_path / "release"
    rel.mkdir()
    (rel / "AT-SUIT-Node-Setup-9.9.9.exe").write_bytes(b"MZ installer")
    (rel / "AT-SUIT-Node-Setup-9.9.9.exe.blockmap").write_bytes(b"map")
    (rel / "latest.yml").write_text("version: 9.9.9\npath: AT-SUIT-Node-Setup-9.9.9.exe\n")
    (rel / "notes.txt").write_text("not for laptops")
    assert hosted.main(["app", str(rel)]) == 0
    assert admin.get("/api/admin/updates").json()["app"] == "9.9.9"
    assert not (config.cfg.data / "app" / "notes.txt").exists()


def test_a_providers_own_guide_articles_reach_every_venue(admin, tmp_path, monkeypatch):
    own = tmp_path / "kb"
    own.mkdir()
    (own / "dante.md").write_text("# Dante audio\n\n## No audio from the Dante desk\nKeywords: dante, desk, no audio, stage box\n"
                                  "1. Check the Dante Controller subscription to the stage box.\n2. Reboot the stage box.\n")
    (own / "safety.md").write_text("# Our safety rules\n\n## Fire alarm\nKeywords: fire alarm, evacuate\nStop the show and call the duty manager on channel 1.\n")
    monkeypatch.setattr(config.cfg, "kb_dirs", [own])
    arts = {a["slug"]: a for a in admin.get("/api/guide/help").json()["articles"]}
    assert arts["dante"]["title"] == "Dante audio" and "Keywords" not in arts["dante"]["markdown"]
    assert arts["safety"]["title"] == "Our safety rules"  # the provider's file replaces AT-SUIT's own
    assert "audio" in arts and "at-suit" in arts  # AT-SUIT's own guide is there too
    r = admin.post("/api/ai/ask", json={"question": "no audio from the dante desk"}).json()
    assert "Dante Controller subscription" in r["answer"]
    assert {"kind": "tech guide", "title": "No audio from the Dante desk", "url": "/guide/help#dante"} in r["sources"]
    assert admin.get("/guide/help").status_code == 200


def test_hosted_venues_use_the_providers_ai_model(admin, monkeypatch):
    monkeypatch.setattr(config.cfg, "ai_url", "http://ollama:11434")
    monkeypatch.setattr(config.cfg, "ai_model", "llama3.2:3b")
    with db.ro() as c:
        p = aihelper.prefs(c)
    assert (p["ai_url"], p["ai_model"]) == ("http://ollama:11434", "llama3.2:3b")
    admin.put("/api/admin/ai", json={"ai_url": "http://10.0.0.5:11434", "ai_model": "mistral"})
    with db.ro() as c:
        p = aihelper.prefs(c)
    assert (p["ai_url"], p["ai_model"]) == ("http://10.0.0.5:11434", "mistral")  # the venue's own wins
