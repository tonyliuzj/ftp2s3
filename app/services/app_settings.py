from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AppSetting


@dataclass(slots=True)
class EffectiveS3Settings:
    public_base_url: str
    s3_service_name: str
    s3_default_region: str
    s3_require_sigv4: bool
    s3_max_clock_skew_seconds: int
    s3_presign_expiry_seconds: int


SETTING_KEYS = {
    "public_base_url",
    "s3_service_name",
    "s3_default_region",
    "s3_require_sigv4",
    "s3_max_clock_skew_seconds",
    "s3_presign_expiry_seconds",
}


def load_effective_s3_settings(db: Session, request_base_url: str | None = None) -> EffectiveS3Settings:
    rows = {
        row.key: row.value
        for row in db.execute(select(AppSetting).where(AppSetting.key.in_(SETTING_KEYS))).scalars()
    }

    public_base_url = _normalize_base_url(
        rows.get("public_base_url") or settings.public_base_url or request_base_url or "http://localhost:8000"
    )
    return EffectiveS3Settings(
        public_base_url=public_base_url,
        s3_service_name=(rows.get("s3_service_name") or settings.s3_service_name).strip(),
        s3_default_region=(rows.get("s3_default_region") or settings.s3_default_region).strip(),
        s3_require_sigv4=_to_bool(rows.get("s3_require_sigv4"), settings.s3_require_sigv4),
        s3_max_clock_skew_seconds=_to_int(
            rows.get("s3_max_clock_skew_seconds"),
            settings.s3_max_clock_skew_seconds,
        ),
        s3_presign_expiry_seconds=_to_int(
            rows.get("s3_presign_expiry_seconds"),
            settings.s3_presign_expiry_seconds,
        ),
    )


def update_s3_settings(db: Session, values: dict[str, object]) -> EffectiveS3Settings:
    for key, raw_value in values.items():
        if key not in SETTING_KEYS or raw_value is None:
            continue

        value = _serialize_setting_value(key, raw_value)
        row = db.get(AppSetting, key)
        if row is None:
            row = AppSetting(key=key, value=value)
            db.add(row)
        else:
            row.value = value
    db.commit()

    return load_effective_s3_settings(db)


def _normalize_base_url(value: str) -> str:
    return value.strip().rstrip("/")


def _to_bool(value: str | None, fallback: bool) -> bool:
    if value is None:
        return fallback
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _to_int(value: str | None, fallback: int) -> int:
    if value is None:
        return fallback
    try:
        return int(value)
    except ValueError:
        return fallback


def _serialize_setting_value(key: str, value: object) -> str:
    if key == "public_base_url":
        return _normalize_base_url(str(value))
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip()
