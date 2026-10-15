import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from licensed_server import licence_key, trust_test_key  # noqa: E402


@pytest.fixture(autouse=True)
def test_vendor_key(monkeypatch):
    """Every test trusts the throwaway vendor key in licensed_server.py, never the real one."""
    from atsuit import licence

    monkeypatch.setattr(licence, "_vendor_raw", licence._vendor_raw)  # put the real one back afterwards
    trust_test_key()
    yield
    licence.forget()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ATSUIT_DATA", str(tmp_path))
    monkeypatch.setenv("ATSUIT_ASR", os.getenv("TEST_ASR", "0"))
    from atsuit import config, security

    config.reload()
    security.reset_keys()
    from fastapi.testclient import TestClient

    from atsuit.main import create_app
    from atsuit.modules import core

    core._forgot_asks.clear()  # every test starts with no recent "forgot" requests

    with TestClient(create_app()) as c:
        yield c


def _setup(client):
    r = client.post("/api/setup", json={
        "organisation": "Test Org", "site_name": "Main Venue", "admin_username": "admin",
        "admin_password": "correct-horse", "rooms": ["CC", "HD", "RH"], "licence_key": licence_key(),
    })
    assert r.status_code == 200, r.text
    return client


@pytest.fixture()
def admin(client):
    """A site as it was before tech passwords: techs type their name on a tech laptop,
    and accounts the tests add don't have to choose a new password at their first
    sign-in (a test-only trigger clears the flag)."""
    from atsuit import db

    _setup(client)
    with db.tx() as c:
        db.set_setting(c, "tech_passwords", False)
        c.execute("CREATE TRIGGER IF NOT EXISTS test_no_first_change AFTER UPDATE OF must_change ON accounts "
                  "WHEN NEW.must_change=1 BEGIN UPDATE accounts SET must_change=0 WHERE id=NEW.id; END")
        c.execute("CREATE TRIGGER IF NOT EXISTS test_no_first_change_new AFTER INSERT ON accounts "
                  "WHEN NEW.must_change=1 BEGIN UPDATE accounts SET must_change=0 WHERE id=NEW.id; END")
    return client


@pytest.fixture()
def strict(client):
    """A new site as sold: tech passwords on, first sign-in changes the password."""
    return _setup(client)
