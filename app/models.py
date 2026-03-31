from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import CoreBase, ObjectBase


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )


class Zone(TimestampMixin, ObjectBase):
    __tablename__ = "zones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    ftp_host: Mapped[str] = mapped_column(String(255), nullable=False)
    ftp_port: Mapped[int] = mapped_column(Integer, default=21, nullable=False)
    ftp_username: Mapped[str] = mapped_column(String(255), nullable=False)
    ftp_password: Mapped[str] = mapped_column(String(255), nullable=False)
    pool_strategy: Mapped[str] = mapped_column(String(20), default="fill_first", nullable=False)
    pool_cursor: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    buckets: Mapped[list["Bucket"]] = relationship(back_populates="zone", cascade="all, delete-orphan")
    objects: Mapped[list["Object"]] = relationship(back_populates="zone")
    servers: Mapped[list["ZoneServer"]] = relationship(
        back_populates="zone",
        cascade="all, delete-orphan",
        order_by="ZoneServer.sort_order",
    )


class ZoneServer(TimestampMixin, ObjectBase):
    __tablename__ = "zone_servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    zone_id: Mapped[int] = mapped_column(ForeignKey("zones.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    ftp_host: Mapped[str] = mapped_column(String(255), nullable=False)
    ftp_port: Mapped[int] = mapped_column(Integer, default=21, nullable=False)
    ftp_username: Mapped[str] = mapped_column(String(255), nullable=False)
    ftp_password: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    capacity_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    zone: Mapped["Zone"] = relationship(back_populates="servers")
    replicas: Mapped[list["ObjectReplica"]] = relationship(back_populates="zone_server")


class Region(TimestampMixin, ObjectBase):
    __tablename__ = "regions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)


class AppSetting(CoreBase):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(String(2000), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class ObjectSetting(ObjectBase):
    __tablename__ = "object_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(String(2000), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class S3AccessKey(TimestampMixin, ObjectBase):
    __tablename__ = "s3_access_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    access_key_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    secret_access_key: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Bucket(TimestampMixin, ObjectBase):
    __tablename__ = "buckets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    zone_id: Mapped[int] = mapped_column(ForeignKey("zones.id"), nullable=False, index=True)
    base_dir: Mapped[str] = mapped_column(String(500), nullable=False)
    region: Mapped[str] = mapped_column(String(100), default="us-east-1", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    zone: Mapped[Zone] = relationship(back_populates="buckets")
    objects: Mapped[list["Object"]] = relationship(back_populates="bucket", cascade="all, delete-orphan")


class Object(TimestampMixin, ObjectBase):
    __tablename__ = "objects"
    __table_args__ = (
        UniqueConstraint("bucket_id", "object_key", name="uq_objects_bucket_key"),
        UniqueConstraint("bucket_id", "ftp_path", name="uq_objects_bucket_ftp_path"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    bucket_id: Mapped[int] = mapped_column(ForeignKey("buckets.id"), nullable=False, index=True)
    zone_id: Mapped[int] = mapped_column(ForeignKey("zones.id"), nullable=False, index=True)
    object_key: Mapped[str] = mapped_column(String(1000), nullable=False, index=True)
    ftp_path: Mapped[str] = mapped_column(String(1000), nullable=False, index=True)
    size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_modified: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    bucket: Mapped[Bucket] = relationship(back_populates="objects")
    zone: Mapped[Zone] = relationship(back_populates="objects")
    replicas: Mapped[list["ObjectReplica"]] = relationship(back_populates="object", cascade="all, delete-orphan")


class ObjectReplica(TimestampMixin, ObjectBase):
    __tablename__ = "object_replicas"
    __table_args__ = (
        UniqueConstraint("object_id", "zone_server_id", name="uq_object_replicas_object_server"),
        UniqueConstraint("zone_server_id", "ftp_path", name="uq_object_replicas_server_path"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    object_id: Mapped[int] = mapped_column(ForeignKey("objects.id"), nullable=False, index=True)
    zone_id: Mapped[int] = mapped_column(ForeignKey("zones.id"), nullable=False, index=True)
    zone_server_id: Mapped[int] = mapped_column(ForeignKey("zone_servers.id"), nullable=False, index=True)
    ftp_path: Mapped[str] = mapped_column(String(1000), nullable=False, index=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    object: Mapped["Object"] = relationship(back_populates="replicas")
    zone: Mapped["Zone"] = relationship()
    zone_server: Mapped["ZoneServer"] = relationship(back_populates="replicas")


class AdminUser(CoreBase):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
