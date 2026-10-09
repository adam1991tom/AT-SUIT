import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


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
        "admin_password": "correct-horse", "rooms": ["CC", "HD", "RH"],
    })
    assert r.status_code == 200, r.text
    return client
