from __future__ import annotations

from datetime import datetime
from typing import Any

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator


BUCKET_NAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{1,61}[a-z0-9])$")
BUCKET_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
IPV4_LIKE_BUCKET_PATTERN = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
REGION_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,30}[a-z0-9])$")
ZONE_POOL_STRATEGIES = {"fill_first", "round_robin", "mirror_all"}


class MessageResponse(BaseModel):
    message: str


class LoginRequest(BaseModel):
    username: str
    password: str


class AdminUserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    created_at: datetime


class LoginResponse(BaseModel):
    user: AdminUserResponse


class ZoneServerWrite(BaseModel):
    id: int | None = None
    name: str = Field(min_length=1, max_length=100)
    ftp_host: str = Field(min_length=1, max_length=255)
    ftp_port: int = Field(default=21, ge=1, le=65535)
    ftp_username: str = Field(min_length=1, max_length=255)
    ftp_password: str | None = Field(default=None, max_length=255)
    enabled: bool = True
    sort_order: int = Field(default=0, ge=0, le=1000000)
    capacity_bytes: int | None = Field(default=None, ge=0)

    @field_validator("name", "ftp_host", "ftp_username")
    @classmethod
    def strip_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("This field is required.")
        return normalized

    @field_validator("ftp_password")
    @classmethod
    def strip_password(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class ZoneServerRead(BaseModel):
    id: int
    name: str
    ftp_host: str
    ftp_port: int
    ftp_username: str
    ftp_password_set: bool = True
    enabled: bool
    sort_order: int
    capacity_bytes: int | None = None
    created_at: datetime
    updated_at: datetime


class ZoneCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    ftp_host: str | None = Field(default=None, min_length=1, max_length=255)
    ftp_port: int = Field(default=21, ge=1, le=65535)
    ftp_username: str | None = Field(default=None, min_length=1, max_length=255)
    ftp_password: str | None = Field(default=None, min_length=1, max_length=255)
    pool_strategy: str = Field(default="fill_first", min_length=1, max_length=20)
    servers: list[ZoneServerWrite] = Field(default_factory=list)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def validate_zone_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Zone name is required.")
        return normalized

    @field_validator("pool_strategy")
    @classmethod
    def validate_pool_strategy(cls, value: str) -> str:
        normalized = value.strip()
        if normalized not in ZONE_POOL_STRATEGIES:
            raise ValueError("Pool strategy must be fill_first, round_robin, or mirror_all.")
        return normalized


class ZoneUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    ftp_host: str | None = Field(default=None, min_length=1, max_length=255)
    ftp_port: int | None = Field(default=None, ge=1, le=65535)
    ftp_username: str | None = Field(default=None, min_length=1, max_length=255)
    ftp_password: str | None = Field(default=None, min_length=1, max_length=255)
    pool_strategy: str | None = Field(default=None, min_length=1, max_length=20)
    servers: list[ZoneServerWrite] | None = None
    enabled: bool | None = None

    @field_validator("name")
    @classmethod
    def validate_zone_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Zone name is required.")
        return normalized

    @field_validator("pool_strategy")
    @classmethod
    def validate_pool_strategy(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if normalized not in ZONE_POOL_STRATEGIES:
            raise ValueError("Pool strategy must be fill_first, round_robin, or mirror_all.")
        return normalized


class ZoneRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    ftp_host: str
    ftp_port: int
    ftp_username: str
    ftp_password_set: bool = True
    pool_strategy: str
    enabled: bool
    created_at: datetime
    updated_at: datetime
    bucket_count: int = 0
    server_count: int = 0
    servers: list[ZoneServerRead] = Field(default_factory=list)


class RegionCreate(BaseModel):
    code: str = Field(min_length=3, max_length=32)
    name: str = Field(min_length=1, max_length=100)

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        return _validate_region(value)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Region name is required.")
        return normalized


class RegionUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=3, max_length=32)
    name: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_region(value)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Region name is required.")
        return normalized


class RegionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    created_at: datetime
    updated_at: datetime
    bucket_count: int = 0
    is_default: bool = False


class BucketCreate(BaseModel):
    name: str = Field(min_length=3, max_length=63)
    zone_id: int
    base_dir: str = Field(min_length=1, max_length=500)
    region: str = Field(default="us-east-1", min_length=3, max_length=32)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def validate_bucket_name(cls, value: str) -> str:
        return _validate_bucket_name(value)

    @field_validator("region")
    @classmethod
    def validate_region(cls, value: str) -> str:
        return _validate_region(value)


class BucketUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=3, max_length=63)
    zone_id: int | None = None
    base_dir: str | None = Field(default=None, min_length=1, max_length=500)
    region: str | None = Field(default=None, min_length=3, max_length=32)
    enabled: bool | None = None

    @field_validator("name")
    @classmethod
    def validate_bucket_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_bucket_name(value)

    @field_validator("region")
    @classmethod
    def validate_region(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_region(value)


class BucketRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    zone_id: int
    zone_name: str
    base_dir: str
    region: str
    enabled: bool
    created_at: datetime
    updated_at: datetime
    object_count: int = 0


class ObjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    bucket_id: int
    zone_id: int
    object_key: str
    ftp_path: str
    size: int | None
    last_modified: datetime | None
    primary_server_name: str | None = None
    primary_zone_name: str | None = None
    replica_count: int = 0
    created_at: datetime
    updated_at: datetime


class DirectoryEntry(BaseModel):
    name: str
    prefix: str


class BucketObjectsResponse(BaseModel):
    bucket: str
    prefix: str
    directories: list[DirectoryEntry]
    objects: list[ObjectRead]
    count: int


class SearchResponse(BaseModel):
    bucket: str
    query: str
    objects: list[ObjectRead]
    count: int


class SyncDiffItem(BaseModel):
    object_key: str
    ftp_path: str
    size: int | None = None
    last_modified: datetime | None = None
    db_object_key: str | None = None
    db_ftp_path: str | None = None
    db_size: int | None = None
    db_last_modified: datetime | None = None


class SyncSummary(BaseModel):
    ftp_total: int
    db_total: int
    ftp_only: int
    db_only: int
    path_mismatches: int
    size_mismatches: int
    repaired_rows: int = 0


class SyncPreviewResponse(BaseModel):
    bucket: str
    summary: SyncSummary
    ftp_only_files: list[SyncDiffItem]
    db_only_files: list[SyncDiffItem]
    path_mismatches: list[SyncDiffItem]
    size_mismatches: list[SyncDiffItem]


class SyncRepairResponse(BaseModel):
    bucket: str
    inserted: int
    updated: int
    deleted: int
    repaired_rows: int
    summary: SyncSummary


class SyncStatusResponse(BaseModel):
    bucket: str
    status: str
    action: str | None = None
    updated_at: datetime | None = None
    summary: dict[str, Any] | None = None
    error: str | None = None


class ZoneSyncDiffItem(BaseModel):
    object_key: str
    ftp_path: str
    zone_name: str
    expected_server_name: str | None = None
    actual_server_names: list[str] = Field(default_factory=list)
    detail: str | None = None


class ZoneSyncSummary(BaseModel):
    object_total: int
    expected_replicas: int
    actual_replicas: int
    missing_objects: int
    missing_expected_copies: int
    unexpected_replicas: int
    db_replica_mismatches: int
    repaired_replicas: int = 0


class ZoneSyncPreviewResponse(BaseModel):
    bucket: str
    summary: ZoneSyncSummary
    missing_objects: list[ZoneSyncDiffItem]
    missing_expected_copies: list[ZoneSyncDiffItem]
    unexpected_replicas: list[ZoneSyncDiffItem]
    db_replica_mismatches: list[ZoneSyncDiffItem]


class ZoneSyncRepairResponse(BaseModel):
    bucket: str
    inserted_replicas: int
    updated_db_rows: int
    repaired_replicas: int
    summary: ZoneSyncSummary


class SystemStatusResponse(BaseModel):
    app_name: str
    site_database_url: str
    object_database_url: str
    object_database_available: bool
    object_database_error: str | None = None
    s3_endpoint_url: str
    s3_service_name: str | None = None
    s3_default_region: str | None = None
    s3_default_access_key_id: str | None = None
    s3_access_key_count: int | None = None
    s3_require_sigv4: bool | None = None
    s3_path_style_only: bool
    s3_presign_expiry_seconds: int | None = None
    zone_total: int | None = None
    zone_enabled: int | None = None
    bucket_total: int | None = None
    bucket_enabled: int | None = None
    object_total: int | None = None
    zone_server_total: int | None = None
    mirror_all_zone_total: int | None = None
    admin_user_total: int
    sync_statuses: list[SyncStatusResponse] = Field(default_factory=list)


class SiteSettingsRead(BaseModel):
    public_base_url: str
    object_database_url: str


class SiteSettingsUpdate(BaseModel):
    public_base_url: str = Field(min_length=1, max_length=500)
    object_database_url: str = Field(min_length=1, max_length=2000)

    @field_validator("public_base_url")
    @classmethod
    def validate_public_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("Public base URL must start with http:// or https://.")
        return normalized

    @field_validator("object_database_url")
    @classmethod
    def validate_object_database_url(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized.startswith(("postgresql://", "postgresql+psycopg://", "postgresql+psycopg2://")):
            raise ValueError("Object database URL must be a PostgreSQL SQLAlchemy URL.")
        return normalized


class ObjectSettingsRead(BaseModel):
    s3_service_name: str
    s3_default_region: str
    s3_require_sigv4: bool
    s3_max_clock_skew_seconds: int
    s3_presign_expiry_seconds: int

    @field_validator("s3_default_region")
    @classmethod
    def validate_region(cls, value: str) -> str:
        return _validate_region(value)


class ObjectSettingsUpdate(BaseModel):
    s3_service_name: str = Field(min_length=1, max_length=50)
    s3_default_region: str = Field(min_length=3, max_length=32)
    s3_require_sigv4: bool
    s3_max_clock_skew_seconds: int = Field(ge=0, le=3600)
    s3_presign_expiry_seconds: int = Field(ge=60, le=604800)

    @field_validator("s3_default_region")
    @classmethod
    def validate_region(cls, value: str) -> str:
        return _validate_region(value)


class AccessKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    access_key_id: str | None = Field(default=None, min_length=6, max_length=100)
    secret_access_key: str | None = Field(default=None, min_length=16, max_length=255)
    enabled: bool = True
    is_default: bool = False

    @field_validator("access_key_id")
    @classmethod
    def validate_access_key_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not re.fullmatch(r"^[A-Z0-9][A-Z0-9_-]{5,99}$", normalized):
            raise ValueError(
                "Access key ID must use uppercase letters, numbers, underscores, or hyphens."
            )
        return normalized

    @field_validator("secret_access_key")
    @classmethod
    def validate_secret_access_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if len(normalized) < 16:
            raise ValueError("Secret access key must be at least 16 characters long.")
        return normalized


class AccessKeyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    enabled: bool | None = None
    is_default: bool | None = None


class AccessKeyRead(BaseModel):
    id: int
    name: str
    access_key_id: str
    enabled: bool
    is_default: bool
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None = None
    masked_secret_access_key: str


class AccessKeyCreateResponse(BaseModel):
    key: AccessKeyRead
    secret_access_key: str


class PresignRequest(BaseModel):
    object_key: str = Field(min_length=1, max_length=1000)
    expires_in: int | None = Field(default=None, ge=60, le=604800)
    access_key_id: str | None = Field(default=None, min_length=6, max_length=100)


class PresignResponse(BaseModel):
    bucket: str
    object_key: str
    region: str
    access_key_id: str
    expires_at: datetime
    url: str


def _validate_bucket_name(value: str) -> str:
    normalized = value.strip()
    if len(normalized) < 3 or len(normalized) > 63:
        raise ValueError("Bucket name must be between 3 and 63 characters long.")
    if not BUCKET_NAME_PATTERN.fullmatch(normalized):
        raise ValueError(
            "Bucket name must contain only lowercase letters, numbers, dots, and hyphens, "
            "and must start and end with a letter or number."
        )
    if ".." in normalized:
        raise ValueError("Bucket name cannot contain consecutive dots.")
    if ".-" in normalized or "-." in normalized:
        raise ValueError("Bucket name cannot place dots next to hyphens.")
    if IPV4_LIKE_BUCKET_PATTERN.fullmatch(normalized):
        raise ValueError("Bucket name cannot be formatted like an IPv4 address.")
    labels = normalized.split(".")
    if any(not BUCKET_LABEL_PATTERN.fullmatch(label) for label in labels):
        raise ValueError("Each dot-separated bucket label must start and end with a letter or number.")
    return normalized


def _validate_region(value: str) -> str:
    normalized = value.strip()
    if len(normalized) < 3 or len(normalized) > 32:
        raise ValueError("Region must be between 3 and 32 characters long.")
    if not REGION_PATTERN.fullmatch(normalized):
        raise ValueError(
            "Region must contain only lowercase letters, numbers, and hyphens, "
            "and must start and end with a letter or number."
        )
    return normalized
