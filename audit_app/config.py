import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Config:
    env: str
    database_path: str
    host: str
    port: int
    username: str
    password: str
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
    wake_catchup: bool = False

    @property
    def test_mode(self):
        return self.env != "production"

    def test_window_open(self, now=None):
        if not self.test_mode or self.test_end_at is None:
            return True
        now = now or datetime.now(timezone.utc)
        return now < self.test_end_at


def load_config():
    env = os.getenv("APP_ENV", "development").lower()
    if env not in {"development", "test", "production"}:
        raise ValueError("APP_ENV must be development, test, or production")
    map_path = Path(os.getenv("BRIDGE_FIELD_MAP", "bridge_fields.json"))
    mapping = json.loads(map_path.read_text())
    zone = os.getenv("APP_TIMEZONE", "America/Toronto")
    ZoneInfo(zone)
    end_value = os.getenv("TEST_MODE_END_AT", "").strip()
    test_end_at = datetime.fromisoformat(end_value.replace("Z", "+00:00")) if end_value else None
    if test_end_at is not None and test_end_at.utcoffset() is None:
        raise ValueError("TEST_MODE_END_AT must include a timezone offset")
    config = Config(
        env=env,
        database_path=os.path.expanduser(os.getenv("DATABASE_PATH", "~/Library/Application Support/MLS Audit Desk/audit.sqlite3")),
        host=os.getenv("APP_HOST", "127.0.0.1"),
        port=int(os.getenv("APP_PORT", "8765")),
        username=os.getenv("APP_USERNAME", "staff"),
        password=os.getenv("APP_PASSWORD", ""),
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
        wake_catchup=os.getenv("WAKE_CATCHUP", "false").lower() in {"1", "true", "yes"},
    )
    if not 0 <= config.rate <= 1:
        raise ValueError("AUDIT_RATE must be between 0 and 1")
    if config.window_hours <= 0 or config.brokerage_cooldown_days < 0 or config.broker_cooldown_days < 0:
        raise ValueError("Window and cooldown settings are invalid")
    if config.bridge_auth_mode not in {"bearer", "query"}:
        raise ValueError("BRIDGE_AUTH_MODE must be bearer or query")
    if config.env == "production" and not all((config.bridge_key, config.sendgrid_key, config.from_address, config.password)):
        raise ValueError("Production configuration is incomplete")
    return config
