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


def test_version_matches_release_file():
    from pathlib import Path

    from atsuit import VERSION

    assert VERSION == (Path(__file__).resolve().parents[2] / "VERSION").read_text().strip()


def test_full_suite_licences_cover_new_modules():
    from atsuit import licence

    assert licence._modules(["comms", "timers", "fleet", "captions", "overlays", "dashboard"]) == licence.ALL_MODULES
    assert licence._modules(["*"]) == licence.ALL_MODULES
    assert licence._modules(["timers", "comms"]) == ["comms", "timers"]


def _tech(admin):
    admin.post("/api/admin/accounts", json={"username": "tech1", "password": "password1", "role": "tech"})
    admin.post("/api/auth/logout")
    assert admin.post("/api/auth/login", json={"username": "tech1", "password": "password1"}).status_code == 200


def test_licence_and_info_are_admin_only(admin):
    assert admin.get("/api/bootstrap").json()["licence"]["edition"] == "evaluation"
    _tech(admin)
    b = admin.get("/api/bootstrap").json()
    assert b["licence"] == {} and b["modules"]["timers"] is True
    for path in ("/api/admin/licence", "/api/admin/info", "/api/admin/info?download=1", "/api/admin/settings",
                 "/api/admin/downloads/atsuit_node.py"):
        assert admin.get(path).status_code == 403, path


def test_licence_details(admin, monkeypatch):
    import base64, json, time
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    monkeypatch.setenv("ATSUIT_VENDOR_PUBKEY", base64.urlsafe_b64encode(pub).decode().rstrip("="))
    body = json.dumps({"licensee": "Venue Ltd", "edition": "pro", "max_nodes": 40, "max_sites": 2, "serial": "AT-0042",
                       "issued": int(time.time()), "expires": int(time.time()) + 10 * 86400, "modules": ["*"]}).encode()
    b64 = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")
    key = f"{b64(body)}.{b64(priv.sign(body))}"
    assert admin.put("/api/admin/licence", json={"key": key}).status_code == 200
    l = admin.get("/api/admin/licence").json()
    assert l["valid"] and l["signature_valid"] and l["serial"] == "AT-0042" and l["edition"] == "pro"
    assert l["days_left"] in (9, 10) and l["issued"] > 0 and l["raw"] == key
    assert l["usage"] == {"sites": 1, "rooms": 3, "nodes": 0} and l["limits"]["nodes"] == 40
    assert len(l["vendor_key_id"]) == 16 and l["vendor_key_source"] == "ATSUIT_VENDOR_PUBKEY"


def test_admin_info_and_diagnostics_have_no_secrets(admin):
    from atsuit import config
    key = admin.post("/api/admin/api-keys", json={"name": "Companion"}).json()["key"]
    code = admin.get("/api/fleet/enrolment").json()[0]["enrol_code"]
    r = admin.post("/api/nodes/enrol", json={"code": code, "name": "ATLAP1", "kind": "tech"})
    node_token = r.json()["token"]
    session = admin.cookies.get("atsuit_session") or ""
    admin.get("/api/admin/backup")
    info = admin.get("/api/admin/info").json()
    for k in ("product", "version", "build", "server", "storage", "apps", "nodes", "counts", "modules", "licence",
              "captions", "websockets", "last_backup"):
        assert k in info, k
    assert info["server"]["count"] == 1 and info["nodes"]["by_kind"]["tech"]["total"] == 1
    assert info["build"]["number"] and info["last_backup"]["actor"] == "admin"
    r = admin.get("/api/admin/info?download=1")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    text = r.text
    secret_key = (config.cfg.data / "secret.key")
    secrets = [key, node_token, code] + ([session] if session else [])
    if secret_key.exists():
        secrets.append(secret_key.read_bytes().hex()[:16])
    for s in secrets:
        assert s not in text
    for word in ("password_hash", "token_hash", "key_hash", "licence_key\"", "\"raw\""):
        assert word not in text, word


def test_built_in_logo_and_brand_colour(admin):
    assert admin.get("/favicon.ico").status_code == 200
    assert admin.get("/static/brand/icon-96.png").status_code == 200
    assert admin.get("/api/public/branding").json()["accent"] == "#FF7A1A"
    # a server set up before the logo stored the old blue default: it now follows the brand
    from atsuit import db
    with db.connect() as c:
        db.set_setting(c, "branding", {**db.get_setting(c, "branding", {}), "accent": "#4f7cff"})
    assert admin.get("/api/public/branding").json()["accent"] == "#FF7A1A"
    for page in ("/", "/node", "/setup"):
        r = admin.get(page)
        if r.status_code == 200 and "text/html" in r.headers.get("content-type", ""):
            assert "/static/brand/favicon.ico" in r.text
