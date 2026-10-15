"""The public site, settings guards, the catalogue, the signing key, reminders and the Paddle setup tool."""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from atshop import catalogue, config, jobs
from atshop.licensing import Signer, SigningError

from conftest import FakePaddle, make_settings

ROOT = Path(__file__).resolve().parents[2]
PAGES = ["/", "/features", "/how-it-works", "/pricing", "/hosting", "/faq", "/support", "/downloads", "/contact",
         "/login", "/register", "/docs", "/sitemap.xml", "/robots.txt"]
DOCS = ["getting-started", "self-hosted-install", "managed-hosting", "licence-keys", "laptop-app", "billing"]
LEGAL = ["terms", "privacy", "dpa", "billing", "acceptable-use"]


def test_every_public_page_opens(h):
    c = h.client()
    for p in PAGES + [f"/docs/{d}" for d in DOCS] + [f"/legal/{k}" for k in LEGAL]:
        r = c.get(p)
        assert r.status_code == 200, p
    assert c.get("/docs/../../config").status_code == 404
    assert c.get("/legal/nope").status_code == 404
    assert c.get("/nope").status_code == 404


def test_legal_pages_are_marked_as_drafts(h):
    c = h.client()
    for k in LEGAL:
        assert "Draft" in c.get(f"/legal/{k}").text, k


def test_pricing_shows_the_catalogue(h):
    page = h.client().get("/pricing").text
    for amount in ("£99", "£229", "£429", "£990", "£2,290", "£4,290", "£39", "£149"):
        assert amount in page, amount
    assert "Prices exclude VAT" in page


def test_price_change_in_the_catalogue_shows_on_the_site(tmp_path, vendor):
    toml = (ROOT / "website" / "catalogue.toml").read_text().replace("monthly = 229", "monthly = 249")
    f = tmp_path / "catalogue.toml"
    f.write_text(toml)
    from conftest import Harness
    h = Harness(tmp_path, vendor, catalogue_file=f)
    assert "£249" in h.client().get("/pricing").text
    # A Paddle price at the old amount no longer sells; paddle_setup makes the new one.
    h.shop.cat.prices[h.price[("plan", "venue", "month")]]["amount"] = 22900
    assert h.shop.cat.price_id("plan", "venue", "month") is None
    assert "plan:venue/month" in h.shop.cat.missing_prices()


def test_security_headers(h):
    r = h.client().get("/")
    csp = r.headers["content-security-policy"]
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp and "'unsafe-eval'" not in csp
    assert "script-src 'self' https://cdn.paddle.com;" in csp  # no inline scripts
    assert r.headers["x-frame-options"] == "DENY"
    assert h.client().get("/login").headers["cache-control"] == "no-store"
    # No inline <script> anywhere (they'd be blocked by the CSP anyway).
    for p in PAGES[:-2]:
        assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", h.client().get(p).text), p


def test_contact_form(h):
    c = h.client()
    r = c.post("/contact", data={"email": "lead@example.com", "name": "Lee", "kind": "sales", "subject": "Quote",
                                 "message": "We have 120 laptops across 3 sites."})
    assert r.status_code == 303
    t = h.shop.db.one("SELECT * FROM tickets")
    assert t["kind"] == "sales" and t["email"] == "lead@example.com"
    assert c.post("/contact", data={"email": "x@example.com", "subject": "", "message": "short"}).status_code == 400
    # A bot that fills the hidden field is thanked and ignored.
    c.post("/contact", data={"website": "spam", "email": "b@example.com", "subject": "Hi", "message": "Buy my stuff now"})
    assert h.shop.db.one("SELECT COUNT(*) n FROM tickets")["n"] == 1


def test_live_billing_needs_approval(tmp_path, vendor):
    s = make_settings(tmp_path, vendor, paddle_env="production", paddle_client_token="live_abc")
    with pytest.raises(config.ConfigError, match="SHOP_ALLOW_LIVE"):
        s.check()
    s.allow_live = True
    s.check()
    assert s.paddle_base == "https://api.paddle.com"
    s2 = make_settings(tmp_path, vendor, paddle_client_token="live_abc")
    with pytest.raises(config.ConfigError, match="live token"):
        s2.check()
    assert make_settings(tmp_path, vendor).paddle_base == "https://sandbox-api.paddle.com"
    with pytest.raises(config.ConfigError, match="SHOP_SECRET"):
        make_settings(tmp_path, vendor, secret="short").check()


def test_settings_from_environment(monkeypatch, tmp_path):
    (tmp_path / "api").write_text("test_from_file\n")
    monkeypatch.setenv("PADDLE_API_KEY_FILE", str(tmp_path / "api"))
    monkeypatch.setenv("ADMIN_EMAILS", "A@Example.com, b@example.com")
    monkeypatch.setenv("SITE_URL", "https://www.example.com/")
    s = config.load()
    assert s.paddle_api_key == "test_from_file"
    assert s.admin_emails == {"a@example.com", "b@example.com"}
    assert s.site_url == "https://www.example.com" and s.secure_cookies and s.paddle_env == "sandbox"


def test_signing_key_must_be_private(tmp_path, vendor):
    open_key = tmp_path / "open.pem"
    open_key.write_bytes(vendor["file"].read_bytes())
    os.chmod(open_key, 0o644)
    with pytest.raises(SigningError, match="chmod 600"):
        Signer(str(open_key)).sign(licensee="X", edition="venue", max_nodes=1, expires=0)
    with pytest.raises(SigningError, match="not set"):
        Signer("").sign(licensee="X", edition="venue", max_nodes=1, expires=0)
    assert not Signer("").ready and Signer(str(vendor["file"])).ready


def test_no_private_keys_in_the_website_folder():
    for f in (ROOT / "website").rglob("*"):
        if f.is_file() and f.suffix not in (".png", ".jpg", ".ico", ".ttf", ".pyc"):
            assert "PRIVATE KEY-----" not in f.read_text(errors="ignore") or f.name.startswith("test_"), f


def test_catalogue_matches_the_licence_tool():
    """Plans sold here sign keys with the same laptop limits tools/licence.py uses."""
    spec = importlib.util.spec_from_file_location("licence_tool", ROOT / "tools" / "licence.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    cat = catalogue.load(ROOT / "website" / "catalogue.toml")
    for p in cat.plan_list:
        assert tool.PLANS[p.licence_plan]["nodes"] == p.laptops, p.key
        assert p.yearly < p.monthly * 12, f"{p.key}: yearly should cost less than 12 months"
    assert tool.PLANS[cat.event["licence_plan"]] == {"nodes": cat.event["laptops"], "days": cat.event["days"]}
    assert tool.PLANS[cat.trial["licence_plan"]] == {"nodes": cat.trial["laptops"], "days": cat.trial["days"]}
    assert cat.spare_days == tool.SPARE_DAYS


def test_reminders_for_keys_that_will_not_renew(h):
    c = h.sign_in("try@example.com")
    c.post("/account/trial", data={"csrf": h.csrf(c)})
    lic = h.shop.db.one("SELECT * FROM licences")
    h.shop.db.run("UPDATE licences SET expires=? WHERE id=?", int(time.time()) + 5 * 86400, lic["id"])
    assert jobs.reminders(h.shop) == 1
    jobs.reminders(h.shop)  # once only
    subjects = [r["subject"] for r in h.shop.db.all("SELECT subject FROM emails WHERE to_addr='try@example.com'")]
    assert subjects.count("AT SUITE: your licence ends in 7 days") == 1


def test_paddle_setup_creates_prices_once_and_keeps_old_ones(tmp_path, vendor, capsys):
    spec = importlib.util.spec_from_file_location("paddle_setup", ROOT / "website" / "tools" / "paddle_setup.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    s = make_settings(tmp_path, vendor)
    fake = FakePaddle()
    t = httpx.MockTransport(fake)
    assert tool.main([], settings=s, transport=t) == 0  # a dry run changes nothing
    assert not fake.prices and not s.prices_file.exists()
    assert tool.main(["--apply"], settings=s, transport=t) == 0
    assert len(fake.products) == 5 and len(fake.prices) == 9
    venue_year = next(b for b in fake.prices.values() if b["description"] == "AT SUITE Venue (yearly)")
    assert venue_year["unit_price"] == {"amount": "229000", "currency_code": "GBP"}
    assert venue_year["billing_cycle"] == {"interval": "year", "frequency": 1} and venue_year["tax_mode"] == "external"
    event = next(b for b in fake.prices.values() if "Event" in b["description"])
    assert "billing_cycle" not in event
    cat = catalogue.load(s.catalogue_file, s.prices_file)
    assert cat.missing_prices() == []
    assert tool.main(["--apply"], settings=s, transport=t) == 0 and len(fake.prices) == 9  # nothing new

    # A price rise makes one new price and keeps the old one mapped (existing subscribers renew on it).
    f = tmp_path / "catalogue.toml"
    f.write_text((ROOT / "website" / "catalogue.toml").read_text().replace("yearly = 2290", "yearly = 2490"))
    s.catalogue_file = f
    old_id = cat.price_id("plan", "venue", "year")
    assert tool.main(["--apply", "--archive-old"], settings=s, transport=t) == 0
    cat = catalogue.load(s.catalogue_file, s.prices_file)
    assert len(fake.prices) == 10 and fake.prices[old_id]["status"] == "archived"
    assert cat.lookup(old_id)["key"] == "venue" and cat.lookup(old_id)["active"] is False
    assert cat.price_id("plan", "venue", "year") != old_id


def test_paddle_setup_refuses_live_without_approval(tmp_path, vendor):
    spec = importlib.util.spec_from_file_location("paddle_setup", ROOT / "website" / "tools" / "paddle_setup.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    s = make_settings(tmp_path, vendor, paddle_env="production", paddle_client_token="live_x")
    with pytest.raises(config.ConfigError):
        tool.main(["--apply"], settings=s, transport=httpx.MockTransport(FakePaddle()))


def test_the_app_starts_from_the_environment(tmp_path, vendor):
    """`uvicorn atshop.app:app` with the variables from .env.example."""
    env = {**os.environ, "SHOP_SECRET": "x" * 40, "SHOP_DATA": str(tmp_path / "d"),
           "LICENCE_SIGNING_KEY_FILE": str(vendor["file"]), "PYTHONPATH": str(ROOT / "website")}
    code = ("from fastapi.testclient import TestClient; from atshop.app import app; "
            "r = TestClient(app).get('/api/health'); print(r.json())")
    out = subprocess.run(["python3", "-c", code], env=env, cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert "'signing': True" in out.stdout and "'billing': 'off'" in out.stdout
