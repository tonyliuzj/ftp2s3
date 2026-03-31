from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AppSetting, ObjectSetting


@dataclass(slots=True)
class SiteSettings:
    public_base_url: str
    object_database_url: str


@dataclass(slots=True)
class ObjectStorageSettings:
    s3_service_name: str
    s3_default_region: str
    s3_require_sigv4: bool
    s3_max_clock_skew_seconds: int
    s3_presign_expiry_seconds: int


@dataclass(slots=True)
class EffectiveS3Settings:
    public_base_url: str
    object_database_url: str
    s3_service_name: str
    s3_default_region: str
    s3_require_sigv4: bool
    s3_max_clock_skew_seconds: int
    s3_presign_expiry_seconds: int


SITE_SETTING_KEYS = {
    "public_base_url",
    "object_database_url",
}

OBJECT_SETTING_KEYS = {
    "s3_service_name",
    "s3_default_region",
    "s3_require_sigv4",
    "s3_max_clock_skew_seconds",
    "s3_presign_expiry_seconds",
}


def load_site_settings(db: Session, request_base_url: str | None = None) -> SiteSettings:
    rows = {
        row.key: row.value
        for row in db.execute(select(AppSetting).where(AppSetting.key.in_(SITE_SETTING_KEYS))).scalars()
    }
    public_base_url = _normalize_base_url(
        rows.get("public_base_url") or settings.public_base_url or request_base_url or "http://localhost:8000"
    )
    return SiteSettings(
        public_base_url=public_base_url,
        object_database_url=(rows.get("object_database_url") or settings.object_database_url).strip(),
    )


def update_site_settings(db: Session, values: dict[str, object], *, commit: bool = True) -> SiteSettings:
    _update_key_value_settings(db, AppSetting, SITE_SETTING_KEYS, values)
    if commit:
        db.commit()
    else:
        db.flush()
    return load_site_settings(db)


def load_object_settings(db: Session) -> ObjectStorageSettings:
    rows = {
        row.key: row.value
        for row in db.execute(select(ObjectSetting).where(ObjectSetting.key.in_(OBJECT_SETTING_KEYS))).scalars()
    }
    return ObjectStorageSettings(
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


def update_object_settings(db: Session, values: dict[str, object], *, commit: bool = True) -> ObjectStorageSettings:
    _update_key_value_settings(db, ObjectSetting, OBJECT_SETTING_KEYS, values)
    if commit:
        db.commit()
    else:
        db.flush()
    return load_object_settings(db)


def load_effective_s3_settings(
    site_db: Session,
    object_db: Session | None = None,
    request_base_url: str | None = None,
) -> EffectiveS3Settings:
    site_settings = load_site_settings(site_db, request_base_url=request_base_url)
    if object_db is not None:
        object_settings = load_object_settings(object_db)
    else:
        object_settings = ObjectStorageSettings(
            s3_service_name=settings.s3_service_name,
            s3_default_region=settings.s3_default_region,
            s3_require_sigv4=settings.s3_require_sigv4,
            s3_max_clock_skew_seconds=settings.s3_max_clock_skew_seconds,
            s3_presign_expiry_seconds=settings.s3_presign_expiry_seconds,
        )

    return EffectiveS3Settings(
        public_base_url=site_settings.public_base_url,
        object_database_url=site_settings.object_database_url,
        s3_service_name=object_settings.s3_service_name,
        s3_default_region=object_settings.s3_default_region,
        s3_require_sigv4=object_settings.s3_require_sigv4,
        s3_max_clock_skew_seconds=object_settings.s3_max_clock_skew_seconds,
        s3_presign_expiry_seconds=object_settings.s3_presign_expiry_seconds,
    )


def _update_key_value_settings(
    db: Session,
    model,
    allowed_keys: set[str],
    values: dict[str, object],
) -> None:
    for key, raw_value in values.items():
        if key not in allowed_keys or raw_value is None:
            continue

        value = _serialize_setting_value(key, raw_value)
        row = db.get(model, key)
        if row is None:
            row = model(key=key, value=value)
            db.add(row)
        else:
            row.value = value


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
    if key == "object_database_url":
        return str(value).strip()
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip()
