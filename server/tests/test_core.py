def test_health_and_setup(client):
    assert client.get("/api/health").json()["setup_complete"] is False
    assert client.get("/", follow_redirects=False).headers["location"] == "/setup"
    r = client.post("/api/setup", json={"organisation": "O", "site_name": "S", "admin_username": "admin",
                                        "admin_password": "correct-horse", "rooms": ["A"]})
    assert r.status_code == 200
    assert client.post("/api/setup", json={"organisation": "O", "site_name": "S", "admin_username": "xy",
                                           "admin_password": "correct-horse"}).status_code == 409


def test_login_roles_and_bootstrap(admin):
    b = admin.get("/api/bootstrap").json()
    assert [r["name"] for r in b["rooms"]] == ["CC", "HD", "RH"]
    assert b["licence"]["edition"] == "evaluation"
    r = admin.post("/api/admin/accounts", json={"username": "tech1", "password": "password1", "role": "tech"})
    assert r.status_code == 200
    admin.post("/api/auth/logout")
    assert admin.get("/api/bootstrap").status_code == 401
    assert admin.post("/api/auth/login", json={"username": "tech1", "password": "nope"}).status_code == 401
    assert admin.post("/api/auth/login", json={"username": "TECH1", "password": "password1"}).status_code == 200
    assert admin.get("/api/admin/accounts").status_code == 403
    assert admin.get("/api/bootstrap").json()["me"]["role"] == "tech"


def test_roomcomms_password_hash_compatible():
    import hashlib
    from atsuit.security import verify_password
    salt = "ab" * 16
    stored = f"{salt}${hashlib.pbkdf2_hmac('sha256', b'secret', salt.encode(), 200000).hex()}"
    assert verify_password("secret", stored)
    assert not verify_password("wrong", stored)


def test_sites_rooms_and_licence_limits(admin):
    site = admin.get("/api/admin/sites").json()[0]
    assert admin.post("/api/admin/sites", json={"name": "Second"}).status_code == 402
    r = admin.post("/api/admin/rooms", json={"site_id": site["id"], "name": "Q1"})
    assert r.status_code == 200
    assert admin.post("/api/admin/rooms", json={"site_id": site["id"], "name": "Q1"}).status_code == 409
    assert admin.put("/api/admin/licence", json={"key": "garbage.key"}).status_code == 400


def test_signed_licence(admin, monkeypatch):
    import base64, json, time
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    monkeypatch.setenv("ATSUIT_VENDOR_PUBKEY", base64.urlsafe_b64encode(pub).decode().rstrip("="))
    body = json.dumps({"licensee": "Venue Ltd", "max_nodes": 0, "max_sites": 3, "expires": int(time.time()) + 3600,
                       "modules": ["comms", "timers", "fleet", "dashboard"]}).encode()
    b64 = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")
    key = f"{b64(body)}.{b64(priv.sign(body))}"
    r = admin.put("/api/admin/licence", json={"key": key})
    assert r.status_code == 200 and r.json()["licensee"] == "Venue Ltd"
    mods = admin.get("/api/bootstrap").json()["modules"]
    assert mods["captions"] is False and mods["comms"] is True
    assert admin.get("/api/captions/status").status_code == 404
    assert admin.post("/api/admin/sites", json={"name": "Second"}).status_code == 200


def test_backup_zip(admin):
    r = admin.get("/api/admin/backup")
    assert r.status_code == 200 and r.content[:2] == b"PK"
