from functools import lru_cache
from hashlib import sha256

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./kip_calendar.db"
    secret_key: str = "change-me"
    admin_username: str = "admin"
    admin_password: str = "admin"
    admin_calendar_token: str | None = None
    base_url: str = "http://localhost:8000"
    upload_dir: str = "uploads"
    max_upload_bytes: int = 10 * 1024 * 1024
    admin_session_ttl_seconds: int = 12 * 60 * 60
    calendar_timezone: str = "Asia/Yekaterinburg"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_admin_calendar_token() -> str:
    settings = get_settings()
    if settings.admin_calendar_token:
        return settings.admin_calendar_token
    return sha256(f"{settings.secret_key}:admin-calendar".encode()).hexdigest()[:40]


def validate_production_settings() -> None:
    settings = get_settings()
    is_local = settings.base_url.startswith("http://localhost") or settings.base_url.startswith("http://127.0.0.1")
    if is_local:
        return
    if settings.secret_key == "change-me":
        raise RuntimeError("SECRET_KEY must be changed in production")
    if settings.admin_password == "admin":
        raise RuntimeError("ADMIN_PASSWORD must be changed in production")
