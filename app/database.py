from __future__ import annotations

import logging
from dataclasses import dataclass
from threading import Lock
from typing import Generator

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


logger = logging.getLogger(__name__)


class CoreBase(DeclarativeBase):
    pass


class ObjectBase(DeclarativeBase):
    pass


Base = ObjectBase


class ObjectDatabaseUnavailableError(RuntimeError):
    pass


@dataclass(slots=True)
class ObjectDatabaseStatus:
    available: bool
    database_url: str
    error: str | None = None


def _validate_core_database_url(database_url: str) -> str:
    normalized = database_url.strip()
    if not normalized.startswith("sqlite:///"):
        raise RuntimeError(
            "APP_DATABASE_URL must be a SQLite SQLAlchemy URL such as "
            "'sqlite:///./data/app.db'."
        )
    return normalized


def _validate_object_database_url(database_url: str) -> str:
    normalized = database_url.strip()
    if not (
        normalized.startswith("postgresql://")
        or normalized.startswith("postgresql+psycopg://")
        or normalized.startswith("postgresql+psycopg2://")
    ):
        raise RuntimeError(
            "The object metadata database must be a PostgreSQL SQLAlchemy URL such as "
            "'postgresql+psycopg://user:password@localhost:5432/ftp2s3'."
        )
    return normalized


def _engine_kwargs(database_url: str) -> dict[str, object]:
    kwargs: dict[str, object] = {"pool_pre_ping": True}
    if database_url.startswith("sqlite:///"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return kwargs


site_engine = create_engine(
    _validate_core_database_url(settings.database_url),
    **_engine_kwargs(settings.database_url),
)
SiteSessionLocal = sessionmaker(bind=site_engine, autoflush=False, autocommit=False, expire_on_commit=False)

ObjectSessionLocal = sessionmaker(autoflush=False, autocommit=False, expire_on_commit=False)
SessionLocal = ObjectSessionLocal
_object_engine_lock = Lock()
_object_engine = None
_object_database_url = ""


def get_effective_object_database_url(core_db: Session | None = None) -> str:
    configured_value = ""
    if core_db is not None:
        from .models import AppSetting

        row = core_db.execute(select(AppSetting).where(AppSetting.key == "object_database_url")).scalar_one_or_none()
        if row is not None and row.value.strip():
            configured_value = row.value.strip()

    return _validate_object_database_url(configured_value or settings.object_database_url)


def configure_object_database(*, database_url: str | None = None, core_db: Session | None = None) -> str:
    global _object_engine
    global _object_database_url

    resolved_url = _validate_object_database_url(database_url or get_effective_object_database_url(core_db))

    with _object_engine_lock:
        if _object_engine is not None and _object_database_url == resolved_url:
            return resolved_url

        next_engine = create_engine(resolved_url, **_engine_kwargs(resolved_url))
        try:
            with next_engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            ObjectBase.metadata.create_all(bind=next_engine)
        except Exception as exc:
            next_engine.dispose()
            raise ObjectDatabaseUnavailableError(f"Object metadata database is unavailable: {exc}") from exc

        ObjectSessionLocal.configure(bind=next_engine)

        previous_engine = _object_engine
        _object_engine = next_engine
        _object_database_url = resolved_url

        if previous_engine is not None:
            previous_engine.dispose()

    return resolved_url


def get_current_object_database_url() -> str:
    if _object_database_url:
        return _object_database_url

    with SiteSessionLocal() as site_db:
        return configure_object_database(core_db=site_db)


def get_object_database_status(core_db: Session | None = None) -> ObjectDatabaseStatus:
    database_url = get_effective_object_database_url(core_db)
    try:
        configure_object_database(database_url=database_url)
        with ObjectSessionLocal() as object_db:
            object_db.execute(text("SELECT 1"))
        return ObjectDatabaseStatus(available=True, database_url=database_url)
    except Exception as exc:
        message = str(exc)
        if not isinstance(exc, ObjectDatabaseUnavailableError):
            message = f"Object metadata database is unavailable: {message}"
        return ObjectDatabaseStatus(available=False, database_url=database_url, error=message)


def init_db() -> None:
    CoreBase.metadata.create_all(bind=site_engine)

    with SiteSessionLocal() as site_db:
        try:
            configure_object_database(core_db=site_db)
            _seed_zone_servers_and_object_replicas()
        except Exception as exc:
            logger.warning("Object metadata database is unavailable during startup: %s", exc)


def _seed_zone_servers_and_object_replicas() -> None:
    from .models import Object, ObjectReplica, Zone, ZoneServer

    with ObjectSessionLocal() as object_db:
        zones = list(object_db.execute(select(Zone).order_by(Zone.id)).scalars())
        changed = False

        for zone in zones:
            existing_servers = list(
                object_db.execute(
                    select(ZoneServer).where(ZoneServer.zone_id == zone.id).order_by(ZoneServer.sort_order, ZoneServer.id)
                ).scalars()
            )
            if existing_servers:
                continue

            object_db.add(
                ZoneServer(
                    zone_id=zone.id,
                    name=f"{zone.name} Primary",
                    ftp_host=zone.ftp_host,
                    ftp_port=zone.ftp_port,
                    ftp_username=zone.ftp_username,
                    ftp_password=zone.ftp_password,
                    enabled=zone.enabled,
                    sort_order=0,
                )
            )
            changed = True

        if changed:
            object_db.commit()

        first_server_by_zone: dict[int, ZoneServer] = {}
        for server in object_db.execute(
            select(ZoneServer).order_by(ZoneServer.zone_id, ZoneServer.sort_order, ZoneServer.id)
        ).scalars():
            first_server_by_zone.setdefault(server.zone_id, server)

        inserted_replicas = False
        for row in object_db.execute(select(Object).order_by(Object.id)).scalars():
            existing_replica = object_db.execute(
                select(ObjectReplica)
                .where(ObjectReplica.object_id == row.id)
                .where(ObjectReplica.zone_id == row.zone_id)
            ).scalar_one_or_none()
            if existing_replica is not None:
                continue

            server = first_server_by_zone.get(row.zone_id)
            if server is None:
                continue

            object_db.add(
                ObjectReplica(
                    object_id=row.id,
                    zone_id=row.zone_id,
                    zone_server_id=server.id,
                    ftp_path=row.ftp_path,
                    is_primary=True,
                )
            )
            inserted_replicas = True

        if inserted_replicas:
            object_db.commit()


def get_db() -> Generator[Session, None, None]:
    db = None
    try:
        with SiteSessionLocal() as site_db:
            effective_url = get_effective_object_database_url(site_db)

        if _object_engine is None or _object_database_url != effective_url:
            configure_object_database(database_url=effective_url)

        db = ObjectSessionLocal()
        db.execute(text("SELECT 1"))
        yield db
    except ObjectDatabaseUnavailableError:
        raise
    except Exception as exc:
        raise ObjectDatabaseUnavailableError(f"Object metadata database is unavailable: {exc}") from exc
    finally:
        if db is not None:
            db.close()


def get_site_db() -> Generator[Session, None, None]:
    db = SiteSessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_object_db() -> Generator[Session, None, None]:
    yield from get_db()
