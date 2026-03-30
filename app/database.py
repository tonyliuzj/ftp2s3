from __future__ import annotations

from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


def _validate_database_url() -> str:
    database_url = settings.database_url.strip()
    if not (
        database_url.startswith("postgresql://")
        or database_url.startswith("postgresql+psycopg://")
        or database_url.startswith("postgresql+psycopg2://")
    ):
        raise RuntimeError(
            "DATABASE_URL must be a PostgreSQL SQLAlchemy URL such as "
            "'postgresql+psycopg://user:password@localhost:5432/ftp2s3'."
        )
    return database_url


engine = create_engine(_validate_database_url(), pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _seed_zone_servers_and_object_replicas()


def _seed_zone_servers_and_object_replicas() -> None:
    from .models import Object, ObjectReplica, Zone, ZoneServer

    with SessionLocal() as db:
        zones = list(db.query(Zone).all())
        changed = False

        for zone in zones:
            existing_servers = list(
                db.query(ZoneServer).filter(ZoneServer.zone_id == zone.id).order_by(ZoneServer.sort_order).all()
            )
            if existing_servers:
                continue

            db.add(
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
            db.commit()

        first_server_by_zone: dict[int, ZoneServer] = {}
        for server in db.query(ZoneServer).order_by(ZoneServer.zone_id, ZoneServer.sort_order, ZoneServer.id).all():
            first_server_by_zone.setdefault(server.zone_id, server)

        inserted_replicas = False
        for row in db.query(Object).all():
            existing_replica = (
                db.query(ObjectReplica)
                .filter(ObjectReplica.object_id == row.id, ObjectReplica.zone_id == row.zone_id)
                .first()
            )
            if existing_replica is not None:
                continue

            server = first_server_by_zone.get(row.zone_id)
            if server is None:
                continue

            db.add(
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
            db.commit()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
