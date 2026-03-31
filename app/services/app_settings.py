from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AppSetting, ObjectSetting, Region, S3AccessKey
from app.services.access_keys import ensure_single_default_key, get_access_key_by_access_key_id, set_default_access_key


@dataclass(slots=True)
class SiteSettings:
    public_base_url: str
    object_database_url: str
    ftp_timeout: int
    postgres_db: str
    postgres_user: str
    postgres_password: str


@dataclass(slots=True)
class ObjectStorageSettings:
    s3_service_name: str
    s3_default_region: str
    s3_require_sigv4: bool
    s3_max_clock_skew_seconds: int
    s3_presign_expiry_seconds: int


@dataclass(slots=True)
class PendingObjectConfiguration:
    s3_service_name: str
    s3_default_region: str
    s3_access_key_id: str
    s3_secret_access_key: str
    s3_require_sigv4: bool
    s3_max_clock_skew_seconds: int
    s3_presign_expiry_seconds: int


@dataclass(slots=True)
class EffectiveS3Settings:
    public_base_url: str
    object_database_url: str
    ftp_timeout: int
    s3_service_name: str
    s3_default_region: str
    s3_require_sigv4: bool
    s3_max_clock_skew_seconds: int
    s3_presign_expiry_seconds: int


SITE_SETTING_KEYS = {
    "public_base_url",
    "object_database_url",
    "ftp_timeout",
    "postgres_db",
    "postgres_user",
    "postgres_password",
}

OBJECT_SETTING_KEYS = {
    "s3_service_name",
    "s3_default_region",
    "s3_require_sigv4",
    "s3_max_clock_skew_seconds",
    "s3_presign_expiry_seconds",
}

PENDING_OBJECT_SETTING_KEYS = {
    "pending_s3_service_name",
    "pending_s3_default_region",
    "pending_s3_access_key_id",
    "pending_s3_secret_access_key",
    "pending_s3_require_sigv4",
    "pending_s3_max_clock_skew_seconds",
    "pending_s3_presign_expiry_seconds",
}


def load_site_settings(db: Session, request_base_url: str | None = None) -> SiteSettings:
    rows = _read_rows(db, AppSetting, SITE_SETTING_KEYS)
    public_base_url = _normalize_base_url(
        rows.get("public_base_url") or settings.public_base_url or request_base_url or "http://localhost:8000"
    )
    return SiteSettings(
        public_base_url=public_base_url,
        object_database_url=(rows.get("object_database_url") or settings.object_database_url).strip(),
        ftp_timeout=_to_int(rows.get("ftp_timeout"), settings.ftp_timeout),
        postgres_db=(rows.get("postgres_db") or settings.postgres_db).strip(),
        postgres_user=(rows.get("postgres_user") or settings.postgres_user).strip(),
        postgres_password=(rows.get("postgres_password") or settings.postgres_password).strip(),
    )


def update_site_settings(db: Session, values: dict[str, object], *, commit: bool = True) -> SiteSettings:
    _update_key_value_settings(db, AppSetting, SITE_SETTING_KEYS, values)
    if commit:
        db.commit()
    else:
        db.flush()
    return load_site_settings(db)


def get_effective_ftp_timeout(db: Session | None = None) -> int:
    if db is not None:
        return load_site_settings(db).ftp_timeout

    from app.database import SiteSessionLocal

    with SiteSessionLocal() as site_db:
        return load_site_settings(site_db).ftp_timeout


def load_object_settings(db: Session) -> ObjectStorageSettings:
    rows = _read_rows(db, ObjectSetting, OBJECT_SETTING_KEYS)
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


def load_pending_object_configuration(db: Session) -> PendingObjectConfiguration | None:
    rows = _read_rows(db, AppSetting, PENDING_OBJECT_SETTING_KEYS)
    if not rows:
        return None

    access_key_id = (rows.get("pending_s3_access_key_id") or settings.s3_access_key_id).strip()
    secret_access_key = (rows.get("pending_s3_secret_access_key") or settings.s3_secret_access_key).strip()
    if not access_key_id or not secret_access_key:
        return None

    return PendingObjectConfiguration(
        s3_service_name=(rows.get("pending_s3_service_name") or settings.s3_service_name).strip(),
        s3_default_region=(rows.get("pending_s3_default_region") or settings.s3_default_region).strip(),
        s3_access_key_id=access_key_id,
        s3_secret_access_key=secret_access_key,
        s3_require_sigv4=_to_bool(rows.get("pending_s3_require_sigv4"), settings.s3_require_sigv4),
        s3_max_clock_skew_seconds=_to_int(
            rows.get("pending_s3_max_clock_skew_seconds"),
            settings.s3_max_clock_skew_seconds,
        ),
        s3_presign_expiry_seconds=_to_int(
            rows.get("pending_s3_presign_expiry_seconds"),
            settings.s3_presign_expiry_seconds,
        ),
    )


def update_pending_object_configuration(
    db: Session,
    values: dict[str, object],
    *,
    commit: bool = True,
) -> PendingObjectConfiguration:
    pending_values = {
        "pending_s3_service_name": values.get("s3_service_name"),
        "pending_s3_default_region": values.get("s3_default_region"),
        "pending_s3_access_key_id": values.get("s3_access_key_id"),
        "pending_s3_secret_access_key": values.get("s3_secret_access_key"),
        "pending_s3_require_sigv4": values.get("s3_require_sigv4"),
        "pending_s3_max_clock_skew_seconds": values.get("s3_max_clock_skew_seconds"),
        "pending_s3_presign_expiry_seconds": values.get("s3_presign_expiry_seconds"),
    }
    _update_key_value_settings(db, AppSetting, PENDING_OBJECT_SETTING_KEYS, pending_values)
    if commit:
        db.commit()
    else:
        db.flush()

    pending = load_pending_object_configuration(db)
    if pending is None:
        raise RuntimeError("Pending object configuration could not be stored.")
    return pending


def clear_pending_object_configuration(db: Session, *, commit: bool = True) -> None:
    for key in PENDING_OBJECT_SETTING_KEYS:
        row = db.get(AppSetting, key)
        if row is not None:
            db.delete(row)
    if commit:
        db.commit()
    else:
        db.flush()


def apply_pending_object_configuration(site_db: Session, object_db: Session) -> bool:
    pending = load_pending_object_configuration(site_db)
    if pending is None:
        return False

    update_object_settings(
        object_db,
        {
            "s3_service_name": pending.s3_service_name,
            "s3_default_region": pending.s3_default_region,
            "s3_require_sigv4": pending.s3_require_sigv4,
            "s3_max_clock_skew_seconds": pending.s3_max_clock_skew_seconds,
            "s3_presign_expiry_seconds": pending.s3_presign_expiry_seconds,
        },
        commit=False,
    )

    region = object_db.execute(select(Region).where(Region.code == pending.s3_default_region)).scalar_one_or_none()
    if region is None:
        object_db.add(Region(code=pending.s3_default_region, name=pending.s3_default_region))

    access_key = get_access_key_by_access_key_id(object_db, pending.s3_access_key_id)
    if access_key is None:
        access_key = S3AccessKey(
            name="Default Access Key",
            access_key_id=pending.s3_access_key_id,
            secret_access_key=pending.s3_secret_access_key,
            enabled=True,
            is_default=True,
        )
        object_db.add(access_key)
    else:
        access_key.secret_access_key = pending.s3_secret_access_key
        access_key.enabled = True
        access_key.is_default = True

    object_db.flush()
    set_default_access_key(object_db, access_key)
    ensure_single_default_key(object_db)
    clear_pending_object_configuration(site_db, commit=False)
    return True


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
        ftp_timeout=site_settings.ftp_timeout,
        s3_service_name=object_settings.s3_service_name,
        s3_default_region=object_settings.s3_default_region,
        s3_require_sigv4=object_settings.s3_require_sigv4,
        s3_max_clock_skew_seconds=object_settings.s3_max_clock_skew_seconds,
        s3_presign_expiry_seconds=object_settings.s3_presign_expiry_seconds,
    )


def _read_rows(db: Session, model, keys: set[str]) -> dict[str, str]:
    return {
        row.key: row.value
        for row in db.execute(select(model).where(model.key.in_(keys))).scalars()
    }


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
    if key in {"object_database_url", "postgres_db", "postgres_user", "postgres_password"}:
        return str(value).strip()
    if key in {"ftp_timeout", "pending_s3_max_clock_skew_seconds", "pending_s3_presign_expiry_seconds"}:
        return str(int(value))
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip()
