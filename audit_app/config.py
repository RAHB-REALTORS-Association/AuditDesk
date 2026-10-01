import json
import os
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urlparse


@dataclass(frozen=True)
class Config:
    env: str
    database_path: str
    host: str
    port: int
    bridge_base_url: str
    bridge_key: str
    bridge_auth_mode: str
    field_map: dict
    sendgrid_key: str
    from_address: str
    from_name: str
    admin_email: str
    rate: float
    window_hours: int
    brokerage_cooldown_days: int
    broker_cooldown_days: int
    subject_template: str
    body_template: str
    timezone: str
    test_end_at: datetime | None = None
    auth_mode: str = "cloudflare"
    access_issuer: str = ""
    access_audience: str = ""
    secret_key: str = ""
    public_url: str = "http://127.0.0.1:8765"
    bootstrap_admins: tuple = ()
    scheduler_enabled: bool = False
    email_enabled: bool = False

    @property
    def test_mode(self):
        return self.env != "production"

    def test_window_open(self, now=None):
        if not self.test_mode or self.test_end_at is None:
            return True
        now = now or datetime.now(timezone.utc)
        return now < self.test_end_at


def development_config(config):
    """Ignore live integrations and identity configuration in the open sandbox."""
    if config.env != "development":
        return config
    return replace(config, database_path="", bridge_base_url="", bridge_key="", sendgrid_key="",
                   from_address="sandbox@example.invalid", admin_email="developer@example.invalid",
                   bootstrap_admins=("developer@example.invalid",), auth_mode="open",
                   access_issuer="", access_audience="", secret_key="",
                   scheduler_enabled=False, email_enabled=False, test_end_at=None)


def load_config():
    env = os.getenv("APP_ENV", "development").lower()
    if env not in {"development", "test", "production"}:
        raise ValueError("APP_ENV must be development, test, or production")
    public_url = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8765").rstrip("/")
    if env == "development" and public_url == "auto":
        # Coolify supplies each PR's generated URL. TLS terminates at Cloudflare;
        # its HTTP origin route must not become the browser's CSRF origin.
        preview = urlparse(os.getenv("COOLIFY_URL", "").split(",")[0].strip())
        if not preview.hostname or preview.username or preview.path not in {"", "/"}:
            raise ValueError("Development PUBLIC_BASE_URL=auto requires a valid COOLIFY_URL")
        public_url = "https://" + preview.netloc
    map_path = Path(os.getenv("BRIDGE_FIELD_MAP", "bridge_fields.json"))
    mapping = json.loads(map_path.read_text())
    zone = os.getenv("APP_TIMEZONE", "America/Toronto")
    ZoneInfo(zone)
    end_value = os.getenv("TEST_MODE_END_AT", "").strip() if env != "development" else ""
    test_end_at = datetime.fromisoformat(end_value.replace("Z", "+00:00")) if end_value else None
    if test_end_at is not None and test_end_at.utcoffset() is None:
        raise ValueError("TEST_MODE_END_AT must include a timezone offset")
    config = Config(
        env=env,
        database_path=os.path.expanduser(os.getenv("DATABASE_PATH", "./data/audit.sqlite3")),
        host=os.getenv("APP_HOST", "127.0.0.1"),
        port=int(os.getenv("APP_PORT", "8765")),
        bridge_base_url=os.getenv("BRIDGE_BASE_URL", ""),
        bridge_key=os.getenv("BRIDGE_API_KEY", ""),
        bridge_auth_mode=os.getenv("BRIDGE_AUTH_MODE", "bearer"),
        field_map=mapping,
        sendgrid_key=os.getenv("SENDGRID_API_KEY", ""),
        from_address=os.getenv("EMAIL_FROM_ADDRESS", ""),
        from_name=os.getenv("EMAIL_FROM_NAME", "MLS Audit Team"),
        admin_email=os.getenv("ADMIN_EMAIL", ""),
        rate=float(os.getenv("AUDIT_RATE", "0.05")),
        window_hours=int(os.getenv("LISTING_WINDOW_HOURS", "24")),
        brokerage_cooldown_days=int(os.getenv("BROKERAGE_COOLDOWN_DAYS", "14")),
        broker_cooldown_days=int(os.getenv("BROKER_COOLDOWN_DAYS", "14")),
        subject_template=os.getenv("EMAIL_SUBJECT_TEMPLATE", "Listing Audit Request - MLS {mls_number}"),
        body_template=os.getenv("EMAIL_BODY_TEMPLATE", "Please provide the listing paperwork for MLS {mls_number}, {address}.\\n\\nListing agent: {agent_name}\\nBrokerage: {brokerage_name}\\n\\nPlease reply with the required documentation."),
        timezone=zone,
        test_end_at=test_end_at,
        auth_mode=os.getenv("AUTH_MODE", "cloudflare"),
        access_issuer=os.getenv("CF_ACCESS_ISSUER", "").rstrip("/"),
        access_audience=os.getenv("CF_ACCESS_AUDIENCE", ""),
        secret_key=os.getenv("APP_SECRET_KEY", ""),
        public_url=public_url,
        bootstrap_admins=tuple(x.strip().lower() for x in os.getenv("BOOTSTRAP_ADMIN_EMAILS", "").split(",") if x.strip()),
        scheduler_enabled=os.getenv("SCHEDULER_ENABLED", "false").lower() in {"1", "true", "yes"},
        email_enabled=os.getenv("EMAIL_ENABLED", "false").lower() in {"1", "true", "yes"},
    )
    if not 0 <= config.rate <= 1:
        raise ValueError("AUDIT_RATE must be between 0 and 1")
    if config.window_hours <= 0 or config.brokerage_cooldown_days < 0 or config.broker_cooldown_days < 0:
        raise ValueError("Window and cooldown settings are invalid")
    if config.bridge_auth_mode not in {"bearer", "query"}:
        raise ValueError("BRIDGE_AUTH_MODE must be bearer or query")
    if config.env == "production" and config.auth_mode != "cloudflare":
        raise ValueError("Production requires AUTH_MODE=cloudflare")
    if config.env == "production" and config.email_enabled and not all((config.bridge_key, config.sendgrid_key, config.from_address)):
        raise ValueError("Production configuration is incomplete")
    return development_config(config)


def validate_web_config(config):
    if config.env not in {"development", "test", "production"}:
        raise ValueError("Unknown application environment")
    if config.env != "development" and config.auth_mode != "cloudflare":
        raise ValueError("Only AUTH_MODE=cloudflare is supported")
    public = urlparse(config.public_url)
    if public.path or public.query or public.fragment or public.username or not public.hostname:
        raise ValueError("PUBLIC_BASE_URL must be an origin without a path")
    if config.env == "development":
        if public.scheme not in {"http", "https"}:
            raise ValueError("Development PUBLIC_BASE_URL must use HTTP or HTTPS")
        return
    issuer = urlparse(config.access_issuer)
    if (issuer.scheme != "https" or not issuer.hostname or not issuer.hostname.endswith(".cloudflareaccess.com")
            or issuer.path or issuer.query or issuer.fragment or issuer.username or issuer.port):
        raise ValueError("CF_ACCESS_ISSUER must be your HTTPS Cloudflare Access team origin")
    if not config.access_audience or public.scheme != "https":
        raise ValueError("Cloudflare authentication requires an audience and HTTPS PUBLIC_BASE_URL")
    if config.secret_key and len(config.secret_key) < 32:
        raise ValueError("APP_SECRET_KEY must contain at least 32 random characters")
