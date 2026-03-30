from __future__ import annotations

import re

from sqlalchemy import select

from .config import settings
from .database import SessionLocal
from .models import AdminUser, Bucket, Region, S3AccessKey
from .security import hash_password


REGION_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,30}[a-z0-9])$")


def _is_valid_region_code(value: str) -> bool:
    normalized = value.strip()
    return 3 <= len(normalized) <= 32 and bool(REGION_PATTERN.fullmatch(normalized))


def ensure_default_admin() -> None:
    with SessionLocal() as db:
        existing_user = db.execute(
            select(AdminUser).where(AdminUser.username == settings.default_admin_username)
        ).scalar_one_or_none()

        if existing_user is not None:
            return

        db.add(
            AdminUser(
                username=settings.default_admin_username,
                password_hash=hash_password(settings.default_admin_password),
            )
        )
        db.commit()


def ensure_default_access_key() -> None:
    with SessionLocal() as db:
        existing_key = db.execute(select(S3AccessKey).limit(1)).scalar_one_or_none()
        if existing_key is not None:
            return

        db.add(
            S3AccessKey(
                name="Default Access Key",
                access_key_id=settings.s3_access_key_id,
                secret_access_key=settings.s3_secret_access_key,
                enabled=True,
                is_default=True,
            )
        )
        db.commit()


def ensure_default_regions() -> None:
    with SessionLocal() as db:
        required_codes = {
            code.strip()
            for code in {settings.s3_default_region, *db.execute(select(Bucket.region)).scalars()}
            if code and _is_valid_region_code(code)
        }
        existing_codes = {code for code in db.execute(select(Region.code)).scalars()}
        missing_codes = sorted(required_codes - existing_codes)
        if not missing_codes:
            return

        for code in missing_codes:
            db.add(Region(code=code, name=code))
        db.commit()
