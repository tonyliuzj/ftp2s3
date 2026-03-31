from __future__ import annotations

import logging
import re

from sqlalchemy import select

from .config import settings
from .database import ObjectDatabaseUnavailableError, SessionLocal, SiteSessionLocal, configure_object_database
from .models import AdminUser, Bucket, Region, S3AccessKey
from .security import hash_password
from .services.app_settings import load_object_settings


logger = logging.getLogger(__name__)


REGION_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,30}[a-z0-9])$")


def _is_valid_region_code(value: str) -> bool:
    normalized = value.strip()
    return 3 <= len(normalized) <= 32 and bool(REGION_PATTERN.fullmatch(normalized))


def ensure_default_admin() -> None:
    with SiteSessionLocal() as db:
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
    try:
        with SiteSessionLocal() as site_db:
            configure_object_database(core_db=site_db)
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
    except ObjectDatabaseUnavailableError as exc:
        logger.warning("Skipping default access key bootstrap: %s", exc)


def ensure_default_regions() -> None:
    try:
        with SiteSessionLocal() as site_db:
            configure_object_database(core_db=site_db)
        with SessionLocal() as db:
            default_region = load_object_settings(db).s3_default_region
            required_codes = {
                code.strip()
                for code in {default_region, *db.execute(select(Bucket.region)).scalars()}
                if code and _is_valid_region_code(code)
            }
            existing_codes = {code for code in db.execute(select(Region.code)).scalars()}
            missing_codes = sorted(required_codes - existing_codes)
            if not missing_codes:
                return

            for code in missing_codes:
                db.add(Region(code=code, name=code))
            db.commit()
    except ObjectDatabaseUnavailableError as exc:
        logger.warning("Skipping default region bootstrap: %s", exc)
