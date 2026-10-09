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

    with TestClient(create_app()) as c:
        yield c


@pytest.fixture()
def admin(client):
    r = client.post("/api/setup", json={
        "organisation": "Test Org", "site_name": "Main Venue", "admin_username": "admin",
        "admin_password": "correct-horse", "rooms": ["CC", "HD", "RH"], "licence_key": licence_key(),
    })
    assert r.status_code == 200, r.text
    return client
