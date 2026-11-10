from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="LUMELIA_", extra="ignore")
    database_url: str = "postgresql+psycopg://localhost:55439/lumelia"
    encryption_key: str = ""
    environment: str = "development"
    allowed_origins: str = "http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:8000"
    public_url: str = "http://127.0.0.1:8000"
    access_password_hash: str = ""
    cookie_secure: bool = False
    session_hours: int = 12
    worker_interval_seconds: int = 10
    live_actions_enabled: bool = False
    # Demo switch: the worker opens no network connection at all (mailbox probes, IMAP collection,
    # warm-up, sourcing and call reconciliation are skipped). Simulated sends keep running.
    offline: bool = False
    # Shown on the public recipient notice (/privacy).
    company_name: str = "Lumelia"
    privacy_contact: str = "privacy@example.com"
    # Mailboxes and domains that must never be used for cold outreach (comma-separated),
    # typically the company's primary domain. Subdomains are covered.
    protected_sender_domains: str = ""
    protected_sender_addresses: str = ""

    @field_validator("database_url", mode="before")
    @classmethod
    def database_driver(cls, value):
        if isinstance(value, str) and value.startswith(("postgres://", "postgresql://")):
            return "postgresql+psycopg://" + value.split("://", 1)[1]
        return value

    @property
    def origins(self):
        return [x.strip().rstrip("/") for x in self.allowed_origins.split(",") if x.strip()]

    @property
    def protected_domains(self):
        return {x.strip().lower() for x in self.protected_sender_domains.split(",") if x.strip()}

    @property
    def protected_addresses(self):
        return {x.strip().lower() for x in self.protected_sender_addresses.split(",") if x.strip()}


@lru_cache
def settings() -> Settings:
    return Settings()
