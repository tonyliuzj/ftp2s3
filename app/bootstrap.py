from __future__ import annotations

import logging

from app.database import ObjectDatabaseUnavailableError, SessionLocal, SiteSessionLocal, configure_object_database
from app.services.app_settings import apply_pending_object_configuration


logger = logging.getLogger(__name__)


def apply_pending_setup_if_possible() -> None:
    try:
        with SiteSessionLocal() as site_db:
            configure_object_database(core_db=site_db)
            with SessionLocal() as object_db:
                changed = apply_pending_object_configuration(site_db, object_db)
                if not changed:
                    return
                object_db.commit()
                site_db.commit()
    except ObjectDatabaseUnavailableError as exc:
        logger.warning("Pending object configuration is waiting for PostgreSQL: %s", exc)
    except Exception:
        logger.exception("Failed to apply pending object configuration.")
