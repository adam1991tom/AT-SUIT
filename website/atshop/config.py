"""Settings, all from environment variables (see .env.example). Nothing secret lives in the repo."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _bool(v: str | None, default: bool = False) -> bool:
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "yes", "true", "on")


def _secret(name: str) -> str:
    """NAME, or the contents of the file in NAME_FILE (Docker secrets)."""
    f = os.environ.get(f"{name}_FILE")
    if f:
        return Path(f).read_text().strip()
    return os.environ.get(name, "").strip()


class ConfigError(RuntimeError):
    pass


@dataclass
class Settings:
    site_url: str = "http://localhost:8090"
    data_dir: Path = Path("./data")
    secret: str = ""
    dev: bool = False
    # Paddle Billing. "sandbox" until adam says otherwise; "production" also needs allow_live.
    paddle_env: str = "sandbox"
    paddle_api_key: str = ""
    paddle_client_token: str = ""
    paddle_webhook_secret: str = ""
    paddle_api_url: str = ""
    allow_live: bool = False
    # The vendor private key that signs AT SUITE licence keys (a file, mode 600, never in git).
    signing_key_file: str = ""
    admin_emails: set[str] = field(default_factory=set)
    # Email. With no SMTP_HOST, mail is kept in the outbox (admin → Emails) and logged, not sent.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_tls: bool = True
    mail_from: str = "AT SUITE <hello@example.com>"
    support_email: str = "support@example.com"
    sales_email: str = "sales@example.com"
    company_name: str = "[COMPANY NAME] Ltd"
    # Managed hosting: "manual" (jobs wait in admin → Hosting for you to run) or "ssh" (runs atcloud).
    hosting_mode: str = "manual"
    hosting_domain: str = ""
    hosting_ssh: str = ""  # user@host for ssh mode
    hosting_ssh_key: str = ""
    hosting_atcloud: str = "atcloud"
    releases_repo: str = "adam1991tom/AT-SUIT-releases"
    catalogue_file: Path = HERE.parent / "catalogue.toml"

    @property
    def secure_cookies(self) -> bool:
        return self.site_url.startswith("https://")

    @property
    def paddle_base(self) -> str:
        if self.paddle_api_url:
            return self.paddle_api_url.rstrip("/")
        return "https://api.paddle.com" if self.paddle_env == "production" else "https://sandbox-api.paddle.com"

    @property
    def billing_ready(self) -> bool:
        return bool(self.paddle_api_key and self.paddle_client_token)

    @property
    def prices_file(self) -> Path:
        return self.data_dir / f"paddle-prices.{self.paddle_env}.json"

    def check(self) -> None:
        if self.paddle_env not in ("sandbox", "production"):
            raise ConfigError("PADDLE_ENV must be sandbox or production")
        if self.paddle_env == "production" and not self.allow_live:
            raise ConfigError("PADDLE_ENV=production takes real money. Set SHOP_ALLOW_LIVE=yes only once "
                              "live billing has been approved.")
        if self.paddle_env == "production" and self.paddle_client_token.startswith("test_"):
            raise ConfigError("PADDLE_CLIENT_TOKEN is a sandbox token but PADDLE_ENV=production")
        if self.paddle_env == "sandbox" and self.paddle_client_token.startswith("live_"):
            raise ConfigError("PADDLE_CLIENT_TOKEN is a live token but PADDLE_ENV=sandbox")
        if not self.dev and len(self.secret) < 32:
            raise ConfigError("SHOP_SECRET must be at least 32 random characters (python -c \"import secrets; "
                              "print(secrets.token_urlsafe(48))\")")
        if self.hosting_mode not in ("manual", "ssh"):
            raise ConfigError("HOSTING_MODE must be manual or ssh")
        if self.hosting_mode == "ssh" and not self.hosting_ssh:
            raise ConfigError("HOSTING_MODE=ssh needs HOSTING_SSH (user@host)")


def load() -> Settings:
    e = os.environ.get
    s = Settings(
        site_url=e("SITE_URL", "http://localhost:8090").rstrip("/"),
        data_dir=Path(e("SHOP_DATA", "./data")),
        secret=_secret("SHOP_SECRET"),
        dev=_bool(e("SHOP_DEV")),
        paddle_env=e("PADDLE_ENV", "sandbox").strip().lower(),
        paddle_api_key=_secret("PADDLE_API_KEY"),
        paddle_client_token=e("PADDLE_CLIENT_TOKEN", "").strip(),
        paddle_webhook_secret=_secret("PADDLE_WEBHOOK_SECRET"),
        paddle_api_url=e("PADDLE_API_URL", ""),
        allow_live=_bool(e("SHOP_ALLOW_LIVE")),
        signing_key_file=e("LICENCE_SIGNING_KEY_FILE", ""),
        admin_emails={a.strip().lower() for a in e("ADMIN_EMAILS", "").split(",") if a.strip()},
        smtp_host=e("SMTP_HOST", ""),
        smtp_port=int(e("SMTP_PORT", "587") or 587),
        smtp_user=e("SMTP_USER", ""),
        smtp_password=_secret("SMTP_PASSWORD"),
        smtp_tls=_bool(e("SMTP_TLS"), True),
        mail_from=e("MAIL_FROM", "AT SUITE <hello@example.com>"),
        support_email=e("SUPPORT_EMAIL", "support@example.com"),
        sales_email=e("SALES_EMAIL", "sales@example.com"),
        company_name=e("COMPANY_NAME", "[COMPANY NAME] Ltd"),
        hosting_mode=e("HOSTING_MODE", "manual").strip().lower(),
        hosting_domain=e("HOSTING_DOMAIN", "").strip().lower(),
        hosting_ssh=e("HOSTING_SSH", ""),
        hosting_ssh_key=e("HOSTING_SSH_KEY", ""),
        hosting_atcloud=e("HOSTING_ATCLOUD", "atcloud"),
        releases_repo=e("RELEASES_REPO", "adam1991tom/AT-SUIT-releases"),
    )
    if e("CATALOGUE_FILE"):
        s.catalogue_file = Path(e("CATALOGUE_FILE"))
    return s
