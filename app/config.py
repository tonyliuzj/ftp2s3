from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


def load_dotenv_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


load_dotenv_file(BASE_DIR / ".env")


@dataclass(slots=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "ftp2s3")
    database_url: str = os.getenv("APP_DATABASE_URL", f"sqlite:///{(BASE_DIR / 'data' / 'app.db').as_posix()}")
    object_database_url: str = os.getenv("OBJECT_DATABASE_URL") or os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://postgres:postgres@localhost:5432/ftp2s3",
    )
    secret_key: str = os.getenv("SECRET_KEY", "dev-secret-key-change-me")
    session_cookie_name: str = os.getenv("SESSION_COOKIE_NAME", "ftp2s3_session")
    default_admin_username: str = os.getenv("DEFAULT_ADMIN_USERNAME", "admin")
    default_admin_password: str = os.getenv("DEFAULT_ADMIN_PASSWORD", "admin123")
    ftp_timeout: int = int(os.getenv("FTP_TIMEOUT", "30"))
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8000"))
    panel_mount_path: str = os.getenv("PANEL_MOUNT_PATH", "/panel")
    public_base_url: str | None = os.getenv("PUBLIC_BASE_URL") or None
    s3_service_name: str = os.getenv("S3_SERVICE_NAME", "s3")
    s3_default_region: str = os.getenv("S3_DEFAULT_REGION", "us-east-1")
    s3_access_key_id: str = os.getenv("S3_ACCESS_KEY_ID", "ftp2s3-access-key")
    s3_secret_access_key: str = os.getenv("S3_SECRET_ACCESS_KEY", "ftp2s3-secret-key-change-me")
    s3_require_sigv4: bool = os.getenv("S3_REQUIRE_SIGV4", "true").lower() in {"1", "true", "yes", "on"}
    s3_max_clock_skew_seconds: int = int(os.getenv("S3_MAX_CLOCK_SKEW_SECONDS", "900"))
    s3_presign_expiry_seconds: int = int(os.getenv("S3_PRESIGN_EXPIRY_SECONDS", "3600"))


settings = Settings()
