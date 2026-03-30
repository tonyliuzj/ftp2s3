from __future__ import annotations

import secrets
import string
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import S3AccessKey


ACCESS_KEY_ALPHABET = string.ascii_uppercase + string.digits


def generate_access_key_id(length: int = 20) -> str:
    prefix = "F2S"
    remaining = max(length - len(prefix), 8)
    return prefix + "".join(secrets.choice(ACCESS_KEY_ALPHABET) for _ in range(remaining))


def generate_secret_access_key() -> str:
    return secrets.token_urlsafe(32)


def get_access_key_by_access_key_id(db: Session, access_key_id: str) -> S3AccessKey | None:
    return db.execute(select(S3AccessKey).where(S3AccessKey.access_key_id == access_key_id)).scalar_one_or_none()


def get_default_access_key(db: Session) -> S3AccessKey | None:
    default_key = db.execute(
        select(S3AccessKey).where(S3AccessKey.enabled.is_(True)).where(S3AccessKey.is_default.is_(True))
    ).scalar_one_or_none()
    if default_key is not None:
        return default_key

    return db.execute(
        select(S3AccessKey).where(S3AccessKey.enabled.is_(True)).order_by(S3AccessKey.created_at, S3AccessKey.id)
    ).scalars().first()


def set_default_access_key(db: Session, target_key: S3AccessKey) -> None:
    keys = list(db.execute(select(S3AccessKey)).scalars())
    for key in keys:
        key.is_default = key.id == target_key.id


def ensure_single_default_key(db: Session) -> None:
    enabled_keys = list(
        db.execute(
            select(S3AccessKey).where(S3AccessKey.enabled.is_(True)).order_by(S3AccessKey.created_at, S3AccessKey.id)
        ).scalars()
    )
    if not enabled_keys:
        return

    default_keys = [key for key in enabled_keys if key.is_default]
    if len(default_keys) == 1:
        return

    chosen_key = default_keys[0] if default_keys else enabled_keys[0]
    set_default_access_key(db, chosen_key)


def touch_access_key(db: Session, access_key: S3AccessKey) -> None:
    access_key.last_used_at = datetime.now(timezone.utc)
    db.commit()


def mask_secret_access_key(secret_access_key: str) -> str:
    if len(secret_access_key) <= 8:
        return "*" * len(secret_access_key)
    return f"{secret_access_key[:4]}...{secret_access_key[-4:]}"
