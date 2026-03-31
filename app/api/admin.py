from __future__ import annotations

import mimetypes
from ftplib import error_perm

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal, configure_object_database, get_db, get_object_database_status, get_site_db
from app.dependencies import get_current_admin
from app.http_helpers import iter_file_chunks
from app.models import AdminUser, Bucket, Object, ObjectReplica, ObjectSetting, Region, S3AccessKey, Zone, ZoneServer
from app.pathing import InvalidPathError, basename, normalize_ftp_dir, normalize_object_key
from app.s3_auth import generate_presigned_get_url
from app.schemas import (
    AccessKeyCreate,
    AccessKeyCreateResponse,
    AccessKeyRead,
    AccessKeyUpdate,
    AdminUserResponse,
    BucketCreate,
    BucketObjectsResponse,
    BucketRead,
    BucketUpdate,
    LoginRequest,
    LoginResponse,
    MessageResponse,
    ObjectRead,
    ObjectSettingsRead,
    ObjectSettingsUpdate,
    PresignRequest,
    PresignResponse,
    RegionCreate,
    RegionRead,
    RegionUpdate,
    SearchResponse,
    SyncPreviewResponse,
    SyncRepairResponse,
    SyncStatusResponse,
    SystemStatusResponse,
    SetupRequest,
    SetupResponse,
    SetupStatusResponse,
    SiteSettingsRead,
    SiteSettingsUpdate,
    ZoneServerRead,
    ZoneCreate,
    ZoneSyncPreviewResponse,
    ZoneSyncRepairResponse,
    ZoneRead,
    ZoneUpdate,
)
from app.security import hash_password, verify_password
from app.services.access_keys import (
    ensure_single_default_key,
    generate_access_key_id,
    generate_secret_access_key,
    get_access_key_by_access_key_id,
    get_default_access_key,
    mask_secret_access_key,
    set_default_access_key,
)
from app.services.app_settings import (
    apply_pending_object_configuration,
    clear_pending_object_configuration,
    get_effective_ftp_timeout,
    load_effective_s3_settings,
    load_object_settings,
    load_pending_object_configuration,
    load_site_settings,
    update_pending_object_configuration,
    update_object_settings,
    update_site_settings,
)
from app.services.storage_manager import InsufficientZoneStorageError, StorageManager, StorageOperationError
from app.services.sync_service import SyncService
from app.services.zone_sync_service import ZoneSyncService


router = APIRouter(prefix="/admin", tags=["admin"])


def _site_settings_to_schema(site_settings) -> SiteSettingsRead:
    return SiteSettingsRead(
        public_base_url=site_settings.public_base_url,
        object_database_url=site_settings.object_database_url,
        ftp_timeout=site_settings.ftp_timeout,
    )


def _object_settings_to_schema(object_settings) -> ObjectSettingsRead:
    return ObjectSettingsRead(
        s3_service_name=object_settings.s3_service_name,
        s3_default_region=object_settings.s3_default_region,
        s3_require_sigv4=object_settings.s3_require_sigv4,
        s3_max_clock_skew_seconds=object_settings.s3_max_clock_skew_seconds,
        s3_presign_expiry_seconds=object_settings.s3_presign_expiry_seconds,
    )


def _object_defaults_to_setup_status(site_settings, pending_object_settings, object_settings) -> SetupStatusResponse:
    active_object_settings = object_settings or pending_object_settings or type(
        "SetupObjectDefaults",
        (),
        {
            "s3_service_name": settings.s3_service_name,
            "s3_default_region": settings.s3_default_region,
            "s3_require_sigv4": settings.s3_require_sigv4,
            "s3_max_clock_skew_seconds": settings.s3_max_clock_skew_seconds,
            "s3_presign_expiry_seconds": settings.s3_presign_expiry_seconds,
        },
    )()
    return SetupStatusResponse(
        needs_setup=True,
        app_name=settings.app_name,
        object_database_url=site_settings.object_database_url,
        postgres_host=settings.postgres_host,
        postgres_db=site_settings.postgres_db,
        postgres_user=site_settings.postgres_user,
        postgres_password=site_settings.postgres_password,
        default_admin_username=settings.default_admin_username,
        default_admin_password=settings.default_admin_password,
        public_base_url=site_settings.public_base_url,
        s3_service_name=active_object_settings.s3_service_name,
        s3_default_region=active_object_settings.s3_default_region,
        s3_access_key_id=(
            pending_object_settings.s3_access_key_id
            if pending_object_settings is not None
            else settings.s3_access_key_id
        ),
        s3_secret_access_key=(
            pending_object_settings.s3_secret_access_key
            if pending_object_settings is not None
            else settings.s3_secret_access_key
        ),
        s3_require_sigv4=active_object_settings.s3_require_sigv4,
        s3_max_clock_skew_seconds=active_object_settings.s3_max_clock_skew_seconds,
        s3_presign_expiry_seconds=active_object_settings.s3_presign_expiry_seconds,
    )


def _zone_server_to_schema(zone_server: ZoneServer) -> ZoneServerRead:
    return ZoneServerRead(
        id=zone_server.id,
        name=zone_server.name,
        ftp_host=zone_server.ftp_host,
        ftp_port=zone_server.ftp_port,
        ftp_username=zone_server.ftp_username,
        ftp_password_set=bool(zone_server.ftp_password),
        enabled=zone_server.enabled,
        sort_order=zone_server.sort_order,
        capacity_bytes=zone_server.capacity_bytes,
        created_at=zone_server.created_at,
        updated_at=zone_server.updated_at,
    )


def _zone_to_schema(db: Session, zone: Zone) -> ZoneRead:
    bucket_count = int(db.execute(select(func.count(Bucket.id)).where(Bucket.zone_id == zone.id)).scalar_one() or 0)
    servers = list(db.execute(select(ZoneServer).where(ZoneServer.zone_id == zone.id).order_by(ZoneServer.sort_order, ZoneServer.id)).scalars())
    primary_server = servers[0] if servers else None
    return ZoneRead(
        id=zone.id,
        name=zone.name,
        ftp_host=primary_server.ftp_host if primary_server is not None else zone.ftp_host,
        ftp_port=primary_server.ftp_port if primary_server is not None else zone.ftp_port,
        ftp_username=primary_server.ftp_username if primary_server is not None else zone.ftp_username,
        ftp_password_set=bool(primary_server.ftp_password if primary_server is not None else zone.ftp_password),
        pool_strategy=zone.pool_strategy,
        enabled=zone.enabled,
        created_at=zone.created_at,
        updated_at=zone.updated_at,
        bucket_count=bucket_count,
        server_count=len(servers),
        servers=[_zone_server_to_schema(server) for server in servers],
    )


def _bucket_to_schema(db: Session, bucket: Bucket) -> BucketRead:
    object_count = int(db.execute(select(func.count(Object.id)).where(Object.bucket_id == bucket.id)).scalar_one() or 0)
    zone = db.get(Zone, bucket.zone_id)
    return BucketRead(
        id=bucket.id,
        name=bucket.name,
        zone_id=bucket.zone_id,
        zone_name=zone.name if zone is not None else "Unknown",
        base_dir=bucket.base_dir,
        region=bucket.region,
        enabled=bucket.enabled,
        created_at=bucket.created_at,
        updated_at=bucket.updated_at,
        object_count=object_count,
    )


def _access_key_to_schema(access_key: S3AccessKey) -> AccessKeyRead:
    return AccessKeyRead(
        id=access_key.id,
        name=access_key.name,
        access_key_id=access_key.access_key_id,
        enabled=access_key.enabled,
        is_default=access_key.is_default,
        created_at=access_key.created_at,
        updated_at=access_key.updated_at,
        last_used_at=access_key.last_used_at,
        masked_secret_access_key=mask_secret_access_key(access_key.secret_access_key),
    )


def _object_to_schema(db: Session, row: Object) -> ObjectRead:
    primary_replica = db.execute(
        select(ObjectReplica)
        .where(ObjectReplica.object_id == row.id)
        .where(ObjectReplica.zone_id == row.zone_id)
        .where(ObjectReplica.is_primary.is_(True))
    ).scalar_one_or_none()
    primary_server = db.get(ZoneServer, primary_replica.zone_server_id) if primary_replica is not None else None
    primary_zone = db.get(Zone, primary_replica.zone_id) if primary_replica is not None else db.get(Zone, row.zone_id)
    replica_count = int(db.execute(select(func.count(ObjectReplica.id)).where(ObjectReplica.object_id == row.id)).scalar_one() or 0)
    return ObjectRead(
        id=row.id,
        bucket_id=row.bucket_id,
        zone_id=row.zone_id,
        object_key=row.object_key,
        ftp_path=row.ftp_path,
        size=row.size,
        last_modified=row.last_modified,
        primary_server_name=primary_server.name if primary_server is not None else None,
        primary_zone_name=primary_zone.name if primary_zone is not None else None,
        replica_count=replica_count,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _zone_servers_from_payload(payload) -> list:
    if payload.servers:
        return payload.servers
    if payload.ftp_host and payload.ftp_username and payload.ftp_password:
        return [
            type("LegacyZoneServer", (), {
                "id": None,
                "name": "Primary",
                "ftp_host": payload.ftp_host.strip(),
                "ftp_port": payload.ftp_port,
                "ftp_username": payload.ftp_username.strip(),
                "ftp_password": payload.ftp_password.strip(),
                "enabled": True,
                "sort_order": 0,
                "capacity_bytes": None,
            })()
        ]
    return []


def _sync_zone_servers(db: Session, zone: Zone, payload_servers: list) -> None:
    existing_servers = {
        server.id: server
        for server in db.execute(select(ZoneServer).where(ZoneServer.zone_id == zone.id)).scalars()
    }
    seen_ids: set[int] = set()
    normalized_servers = sorted(payload_servers, key=lambda server: (server.sort_order, server.id or 0, server.name))

    if not normalized_servers:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="At least one FTP server is required for a zone.")

    for index, server_payload in enumerate(normalized_servers):
        if server_payload.id is not None:
            zone_server = existing_servers.get(server_payload.id)
            if zone_server is None or zone_server.zone_id != zone.id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Zone server does not belong to this zone.")
            seen_ids.add(zone_server.id)
        else:
            if not server_payload.ftp_password:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"FTP password is required for new server '{server_payload.name}'.")
            zone_server = ZoneServer(zone_id=zone.id, name=server_payload.name)
            db.add(zone_server)

        zone_server.name = server_payload.name.strip()
        zone_server.ftp_host = server_payload.ftp_host.strip()
        zone_server.ftp_port = server_payload.ftp_port
        zone_server.ftp_username = server_payload.ftp_username.strip()
        if server_payload.ftp_password not in (None, ""):
            zone_server.ftp_password = server_payload.ftp_password
        elif zone_server.id is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"FTP password is required for new server '{server_payload.name}'.")
        zone_server.enabled = server_payload.enabled
        zone_server.sort_order = index
        zone_server.capacity_bytes = server_payload.capacity_bytes

    for zone_server_id, zone_server in existing_servers.items():
        if zone_server_id not in seen_ids:
            replica_count = int(
                db.execute(select(func.count(ObjectReplica.id)).where(ObjectReplica.zone_server_id == zone_server.id)).scalar_one()
                or 0
            )
            if replica_count:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Server '{zone_server.name}' cannot be removed while objects are still assigned to it. Run Zone Sync first.",
                )
            db.delete(zone_server)

    db.flush()
    current_servers = list(db.execute(select(ZoneServer).where(ZoneServer.zone_id == zone.id).order_by(ZoneServer.sort_order, ZoneServer.id)).scalars())
    if not current_servers:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="At least one FTP server is required for a zone.")

    primary_server = current_servers[0]
    zone.ftp_host = primary_server.ftp_host
    zone.ftp_port = primary_server.ftp_port
    zone.ftp_username = primary_server.ftp_username
    zone.ftp_password = primary_server.ftp_password


def _region_to_schema(db: Session, region: Region) -> RegionRead:
    bucket_count = int(db.execute(select(func.count(Bucket.id)).where(Bucket.region == region.code)).scalar_one() or 0)
    default_region = load_object_settings(db).s3_default_region
    return RegionRead(
        id=region.id,
        code=region.code,
        name=region.name,
        created_at=region.created_at,
        updated_at=region.updated_at,
        bucket_count=bucket_count,
        is_default=region.code == default_region,
    )


@router.get("/setup/status", response_model=SetupStatusResponse)
def setup_status(
    request: Request,
    site_db: Session = Depends(get_site_db),
) -> SetupStatusResponse:
    request_base_url = str(request.base_url).rstrip("/")
    site_settings = load_site_settings(site_db, request_base_url=request_base_url)
    pending_object_settings = load_pending_object_configuration(site_db)
    admin_user_total = int(site_db.execute(select(func.count(AdminUser.id))).scalar_one() or 0)
    if admin_user_total > 0:
        return SetupStatusResponse(
            needs_setup=False,
            app_name=settings.app_name,
            object_database_url="",
            postgres_host=settings.postgres_host,
            postgres_db="",
            postgres_user="",
            postgres_password="",
            default_admin_username="",
            default_admin_password="",
            public_base_url="",
            s3_service_name=settings.s3_service_name,
            s3_default_region=settings.s3_default_region,
            s3_access_key_id="",
            s3_secret_access_key="",
            s3_require_sigv4=settings.s3_require_sigv4,
            s3_max_clock_skew_seconds=settings.s3_max_clock_skew_seconds,
            s3_presign_expiry_seconds=settings.s3_presign_expiry_seconds,
        )

    object_settings = None

    object_database_status = get_object_database_status(core_db=site_db)
    if object_database_status.available:
        try:
            configure_object_database(database_url=object_database_status.database_url)
            with SessionLocal() as object_db:
                if apply_pending_object_configuration(site_db, object_db):
                    object_db.commit()
                    site_db.commit()
                object_settings = load_object_settings(object_db)
        except Exception:
            site_db.rollback()
            object_database_status = get_object_database_status(core_db=site_db)

    status_payload = _object_defaults_to_setup_status(site_settings, pending_object_settings, object_settings)
    return status_payload


@router.post("/setup", response_model=SetupResponse)
def complete_initial_setup(
    payload: SetupRequest,
    request: Request,
    site_db: Session = Depends(get_site_db),
) -> SetupResponse:
    existing_admin = site_db.execute(select(AdminUser).limit(1)).scalar_one_or_none()
    if existing_admin is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Initial setup has already been completed.")

    existing_username = site_db.execute(select(AdminUser).where(AdminUser.username == payload.admin_username)).scalar_one_or_none()
    if existing_username is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Admin username already exists.")

    site_values = {
        "public_base_url": payload.public_base_url,
        "object_database_url": payload.object_database_url,
        "ftp_timeout": settings.ftp_timeout,
        "postgres_db": payload.postgres_db,
        "postgres_user": payload.postgres_user,
        "postgres_password": payload.postgres_password,
    }
    object_values = {
        "s3_service_name": payload.s3_service_name,
        "s3_default_region": payload.s3_default_region,
        "s3_access_key_id": payload.s3_access_key_id,
        "s3_secret_access_key": payload.s3_secret_access_key,
        "s3_require_sigv4": payload.s3_require_sigv4,
        "s3_max_clock_skew_seconds": payload.s3_max_clock_skew_seconds,
        "s3_presign_expiry_seconds": payload.s3_presign_expiry_seconds,
    }

    object_database_available = False
    object_database_error: str | None = None
    try:
        update_site_settings(site_db, site_values, commit=False)

        try:
            configure_object_database(core_db=site_db)
            with SessionLocal() as object_db:
                update_object_settings(object_db, object_values, commit=False)
                region = object_db.execute(select(Region).where(Region.code == payload.s3_default_region)).scalar_one_or_none()
                if region is None:
                    object_db.add(Region(code=payload.s3_default_region, name=payload.s3_default_region))

                access_key = get_access_key_by_access_key_id(object_db, payload.s3_access_key_id)
                if access_key is None:
                    access_key = S3AccessKey(
                        name="Default Access Key",
                        access_key_id=payload.s3_access_key_id,
                        secret_access_key=payload.s3_secret_access_key,
                        enabled=True,
                        is_default=True,
                    )
                    object_db.add(access_key)
                else:
                    access_key.secret_access_key = payload.s3_secret_access_key
                    access_key.enabled = True
                    access_key.is_default = True

                object_db.flush()
                set_default_access_key(object_db, access_key)
                ensure_single_default_key(object_db)
                object_db.commit()
            clear_pending_object_configuration(site_db, commit=False)
            object_database_available = True
        except Exception as exc:
            update_pending_object_configuration(site_db, object_values, commit=False)
            object_database_error = str(exc)

        admin_user = AdminUser(
            username=payload.admin_username,
            password_hash=hash_password(payload.admin_password),
        )
        site_db.add(admin_user)
        site_db.commit()
        site_db.refresh(admin_user)

        request.session.clear()
        request.session["user_id"] = admin_user.id
    except HTTPException:
        site_db.rollback()
        raise
    except Exception as exc:
        site_db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if object_database_available:
        return SetupResponse(
            message="Initial setup complete.",
            object_database_available=True,
        )

    return SetupResponse(
        message="Initial setup complete. PostgreSQL settings were saved and will be applied when the object database becomes reachable.",
        object_database_available=False,
        object_database_error=object_database_error,
    )


@router.post("/login", response_model=LoginResponse)
def admin_login(payload: LoginRequest, request: Request, db: Session = Depends(get_site_db)) -> LoginResponse:
    admin_count = int(db.execute(select(func.count(AdminUser.id))).scalar_one() or 0)
    if admin_count == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Initial setup is required before anyone can log in.",
        )

    user = db.execute(select(AdminUser).where(AdminUser.username == payload.username)).scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password.")

    request.session.clear()
    request.session["user_id"] = user.id
    return LoginResponse(user=AdminUserResponse.model_validate(user))


@router.post("/logout", response_model=MessageResponse)
def admin_logout(request: Request, _admin_user: AdminUser = Depends(get_current_admin)) -> MessageResponse:
    request.session.clear()
    return MessageResponse(message="Logged out.")


@router.get("/me", response_model=AdminUserResponse)
def admin_me(admin_user: AdminUser = Depends(get_current_admin)) -> AdminUserResponse:
    return AdminUserResponse.model_validate(admin_user)


@router.get("/settings/site", response_model=SiteSettingsRead)
def get_site_settings(
    request: Request,
    site_db: Session = Depends(get_site_db),
    _admin_user: AdminUser = Depends(get_current_admin),
) -> SiteSettingsRead:
    site_settings = load_site_settings(site_db, request_base_url=str(request.base_url).rstrip("/"))
    return _site_settings_to_schema(site_settings)


@router.put("/settings/site", response_model=SiteSettingsRead)
def save_site_settings(
    payload: SiteSettingsUpdate,
    site_db: Session = Depends(get_site_db),
    _admin_user: AdminUser = Depends(get_current_admin),
) -> SiteSettingsRead:
    try:
        site_settings = update_site_settings(site_db, payload.model_dump(), commit=False)
        site_db.commit()
        return _site_settings_to_schema(site_settings)
    except Exception as exc:
        site_db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/settings/object", response_model=ObjectSettingsRead)
def get_object_settings(
    site_db: Session = Depends(get_site_db),
    db: Session = Depends(get_db),
    _admin_user: AdminUser = Depends(get_current_admin),
) -> ObjectSettingsRead:
    if apply_pending_object_configuration(site_db, db):
        db.commit()
        site_db.commit()
    return _object_settings_to_schema(load_object_settings(db))


@router.put("/settings/object", response_model=ObjectSettingsRead)
def save_object_settings(
    payload: ObjectSettingsUpdate,
    site_db: Session = Depends(get_site_db),
    db: Session = Depends(get_db),
    _admin_user: AdminUser = Depends(get_current_admin),
) -> ObjectSettingsRead:
    try:
        object_settings = update_object_settings(db, payload.model_dump(), commit=False)
        region = db.execute(select(Region).where(Region.code == payload.s3_default_region)).scalar_one_or_none()
        if region is None:
            db.add(Region(code=payload.s3_default_region, name=payload.s3_default_region))
        clear_pending_object_configuration(site_db, commit=False)
        db.commit()
        site_db.commit()
        return _object_settings_to_schema(object_settings)
    except Exception as exc:
        db.rollback()
        site_db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/regions", response_model=list[RegionRead])
def list_regions(
    db: Session = Depends(get_db),
    _admin_user: AdminUser = Depends(get_current_admin),
) -> list[RegionRead]:
    regions = list(db.execute(select(Region).order_by(Region.code)).scalars())
    return [_region_to_schema(db, region) for region in regions]


@router.post("/regions", response_model=RegionRead, status_code=status.HTTP_201_CREATED)
def create_region(
    payload: RegionCreate,
    db: Session = Depends(get_db),
    _admin_user: AdminUser = Depends(get_current_admin),
) -> RegionRead:
    existing_region = db.execute(select(Region).where(Region.code == payload.code)).scalar_one_or_none()
    if existing_region is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Region code already exists.")

    region = Region(code=payload.code, name=payload.name)
    db.add(region)
    db.commit()
    db.refresh(region)
    return _region_to_schema(db, region)


@router.put("/regions/{region_id}", response_model=RegionRead)
def update_region(
    region_id: int,
    payload: RegionUpdate,
    db: Session = Depends(get_db),
    _admin_user: AdminUser = Depends(get_current_admin),
) -> RegionRead:
    region = db.get(Region, region_id)
    if region is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Region not found.")

    data = payload.model_dump(exclude_unset=True)
    old_code = region.code
    new_code = data.get("code", old_code)
    if new_code != old_code:
        existing_region = db.execute(select(Region).where(Region.code == new_code)).scalar_one_or_none()
        if existing_region is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Region code already exists.")

    if "code" in data:
        region.code = new_code
    if "name" in data and data["name"] is not None:
        region.name = data["name"]

    if new_code != old_code:
        effective_default_region = load_object_settings(db).s3_default_region
        for bucket in db.execute(select(Bucket).where(Bucket.region == old_code)).scalars():
            bucket.region = new_code

        if effective_default_region == old_code:
            default_region_setting = db.get(ObjectSetting, "s3_default_region")
            if default_region_setting is None:
                db.add(ObjectSetting(key="s3_default_region", value=new_code))
            else:
                default_region_setting.value = new_code

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(region)
    return _region_to_schema(db, region)


@router.delete("/regions/{region_id}", response_model=MessageResponse)
def delete_region(
    region_id: int,
    db: Session = Depends(get_db),
    _admin_user: AdminUser = Depends(get_current_admin),
) -> MessageResponse:
    region = db.get(Region, region_id)
    if region is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Region not found.")

    bucket_count = int(db.execute(select(func.count(Bucket.id)).where(Bucket.region == region.code)).scalar_one() or 0)
    if bucket_count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Region cannot be deleted while buckets still use it.",
        )

    default_region = load_object_settings(db).s3_default_region
    if region.code == default_region:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Region cannot be deleted while it is the default S3 region.",
        )

    db.delete(region)
    db.commit()
    return MessageResponse(message="Region deleted.")


@router.get("/keys", response_model=list[AccessKeyRead])
def list_access_keys(
    db: Session = Depends(get_db),
    _admin_user: AdminUser = Depends(get_current_admin),
) -> list[AccessKeyRead]:
    keys = list(db.execute(select(S3AccessKey).order_by(S3AccessKey.created_at.desc(), S3AccessKey.id.desc())).scalars())
    return [_access_key_to_schema(key) for key in keys]


@router.post("/keys", response_model=AccessKeyCreateResponse, status_code=status.HTTP_201_CREATED)
def create_access_key(
    payload: AccessKeyCreate,
    db: Session = Depends(get_db),
    _admin_user: AdminUser = Depends(get_current_admin),
) -> AccessKeyCreateResponse:
    if payload.is_default and not payload.enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A default key must be enabled.")

    access_key_id = payload.access_key_id or generate_access_key_id()
    while db.execute(select(S3AccessKey).where(S3AccessKey.access_key_id == access_key_id)).scalar_one_or_none():
        if payload.access_key_id:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Access key ID already exists.")
        access_key_id = generate_access_key_id()

    secret_access_key = payload.secret_access_key or generate_secret_access_key()
    existing_enabled_key = db.execute(
        select(S3AccessKey).where(S3AccessKey.enabled.is_(True)).limit(1)
    ).scalar_one_or_none()
    key = S3AccessKey(
        name=payload.name.strip(),
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        enabled=payload.enabled,
        is_default=False,
    )
    db.add(key)
    db.flush()

    if payload.enabled and (payload.is_default or existing_enabled_key is None):
        set_default_access_key(db, key)

    ensure_single_default_key(db)
    db.commit()
    db.refresh(key)
    return AccessKeyCreateResponse(key=_access_key_to_schema(key), secret_access_key=secret_access_key)


@router.put("/keys/{key_id}", response_model=AccessKeyRead)
def update_access_key(
    key_id: int,
    payload: AccessKeyUpdate,
    db: Session = Depends(get_db),
    _admin_user: AdminUser = Depends(get_current_admin),
) -> AccessKeyRead:
    access_key = db.get(S3AccessKey, key_id)
    if access_key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Access key not found.")

    data = payload.model_dump(exclude_unset=True)
    if data.get("is_default") and data.get("enabled") is False:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A default key must be enabled.")

    for field, value in data.items():
        if field == "name" and value is not None:
            setattr(access_key, field, value.strip())
        elif field != "is_default":
            setattr(access_key, field, value)

    if payload.is_default:
        access_key.enabled = True
        set_default_access_key(db, access_key)
    elif access_key.is_default and payload.enabled is False:
        access_key.is_default = False

    ensure_single_default_key(db)
    db.commit()
    db.refresh(access_key)
    return _access_key_to_schema(access_key)


@router.post("/keys/{key_id}/rotate", response_model=AccessKeyCreateResponse)
def rotate_access_key_secret(
    key_id: int,
    db: Session = Depends(get_db),
    _admin_user: AdminUser = Depends(get_current_admin),
) -> AccessKeyCreateResponse:
    access_key = db.get(S3AccessKey, key_id)
    if access_key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Access key not found.")

    secret_access_key = generate_secret_access_key()
    access_key.secret_access_key = secret_access_key
    db.commit()
    db.refresh(access_key)
    return AccessKeyCreateResponse(key=_access_key_to_schema(access_key), secret_access_key=secret_access_key)


@router.delete("/keys/{key_id}", response_model=MessageResponse)
def delete_access_key(
    key_id: int,
    db: Session = Depends(get_db),
    _admin_user: AdminUser = Depends(get_current_admin),
) -> MessageResponse:
    access_key = db.get(S3AccessKey, key_id)
    if access_key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Access key not found.")

    was_default = access_key.is_default
    db.delete(access_key)
    db.flush()
    if was_default:
        ensure_single_default_key(db)
    db.commit()
    return MessageResponse(message="Access key deleted.")


@router.get("/zones", response_model=list[ZoneRead])
def list_zones(db: Session = Depends(get_db), _admin_user: AdminUser = Depends(get_current_admin)) -> list[ZoneRead]:
    zones = list(db.execute(select(Zone).order_by(Zone.name)).scalars())
    return [_zone_to_schema(db, zone) for zone in zones]


@router.post("/zones", response_model=ZoneRead, status_code=status.HTTP_201_CREATED)
def create_zone(
    payload: ZoneCreate,
    db: Session = Depends(get_db),
    _admin_user: AdminUser = Depends(get_current_admin),
) -> ZoneRead:
    existing_zone = db.execute(select(Zone).where(Zone.name == payload.name)).scalar_one_or_none()
    if existing_zone is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Zone name already exists.")

    zone = Zone(
        name=payload.name,
        ftp_host=payload.ftp_host or "pending",
        ftp_port=payload.ftp_port,
        ftp_username=payload.ftp_username or "pending",
        ftp_password=payload.ftp_password or "pending",
        pool_strategy=payload.pool_strategy,
        enabled=payload.enabled,
    )
    db.add(zone)
    db.flush()
    _sync_zone_servers(db, zone, _zone_servers_from_payload(payload))
    db.commit()
    db.refresh(zone)
    return _zone_to_schema(db, zone)


@router.put("/zones/{zone_id}", response_model=ZoneRead)
def update_zone(
    zone_id: int,
    payload: ZoneUpdate,
    db: Session = Depends(get_db),
    _admin_user: AdminUser = Depends(get_current_admin),
) -> ZoneRead:
    zone = db.get(Zone, zone_id)
    if zone is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Zone not found.")

    data = payload.model_dump(exclude_unset=True)
    if "name" in data:
        existing_zone = db.execute(select(Zone).where(Zone.name == data["name"]).where(Zone.id != zone_id)).scalar_one_or_none()
        if existing_zone is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Zone name already exists.")
        zone.name = data["name"]

    if "pool_strategy" in data and data["pool_strategy"] is not None:
        zone.pool_strategy = data["pool_strategy"]
    if "enabled" in data and data["enabled"] is not None:
        zone.enabled = data["enabled"]

    legacy_server_update = None
    if payload.servers is not None:
        legacy_server_update = payload.servers
    elif any(key in data for key in {"ftp_host", "ftp_port", "ftp_username", "ftp_password"}):
        existing_servers = list(
            db.execute(select(ZoneServer).where(ZoneServer.zone_id == zone.id).order_by(ZoneServer.sort_order, ZoneServer.id)).scalars()
        )
        primary_server = existing_servers[0] if existing_servers else None
        legacy_server_update = [
            type("LegacyZoneServer", (), {
                "id": primary_server.id if primary_server is not None else None,
                "name": primary_server.name if primary_server is not None else "Primary",
                "ftp_host": (payload.ftp_host or (primary_server.ftp_host if primary_server is not None else "")).strip(),
                "ftp_port": payload.ftp_port or (primary_server.ftp_port if primary_server is not None else 21),
                "ftp_username": (payload.ftp_username or (primary_server.ftp_username if primary_server is not None else "")).strip(),
                "ftp_password": payload.ftp_password if payload.ftp_password not in (None, "") else (primary_server.ftp_password if primary_server is not None else None),
                "enabled": primary_server.enabled if primary_server is not None else True,
                "sort_order": 0,
                "capacity_bytes": primary_server.capacity_bytes if primary_server is not None else None,
            })()
        ]

    if legacy_server_update is not None:
        _sync_zone_servers(db, zone, legacy_server_update)

    db.commit()
    db.refresh(zone)
    return _zone_to_schema(db, zone)


@router.delete("/zones/{zone_id}", response_model=MessageResponse)
def delete_zone(
    zone_id: int,
    db: Session = Depends(get_db),
    _admin_user: AdminUser = Depends(get_current_admin),
) -> MessageResponse:
    zone = db.get(Zone, zone_id)
    if zone is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Zone not found.")

    bucket_count = int(db.execute(select(func.count(Bucket.id)).where(Bucket.zone_id == zone.id)).scalar_one() or 0)
    if bucket_count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Zone cannot be deleted while buckets are still assigned to it.",
        )

    db.delete(zone)
    db.commit()
    return MessageResponse(message="Zone deleted.")


@router.get("/buckets", response_model=list[BucketRead])
def list_buckets(
    db: Session = Depends(get_db),
    _admin_user: AdminUser = Depends(get_current_admin),
) -> list[BucketRead]:
    buckets = list(db.execute(select(Bucket).order_by(Bucket.name)).scalars())
    return [_bucket_to_schema(db, bucket) for bucket in buckets]


@router.post("/buckets", response_model=BucketRead, status_code=status.HTTP_201_CREATED)
def create_bucket(
    payload: BucketCreate,
    db: Session = Depends(get_db),
    _admin_user: AdminUser = Depends(get_current_admin),
) -> BucketRead:
    existing_bucket = db.execute(select(Bucket).where(Bucket.name == payload.name)).scalar_one_or_none()
    if existing_bucket is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Bucket name already exists.")

    zone = db.get(Zone, payload.zone_id)
    if zone is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Zone not found.")
    region = db.execute(select(Region).where(Region.code == payload.region.strip())).scalar_one_or_none()
    if region is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Region not found.")

    try:
        base_dir = normalize_ftp_dir(payload.base_dir)
    except InvalidPathError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    bucket = Bucket(name=payload.name, zone_id=payload.zone_id, base_dir=base_dir, enabled=payload.enabled)
    bucket.region = region.code
    db.add(bucket)
    db.commit()
    db.refresh(bucket)
    return _bucket_to_schema(db, bucket)


@router.put("/buckets/{bucket_id}", response_model=BucketRead)
def update_bucket(
    bucket_id: int,
    payload: BucketUpdate,
    db: Session = Depends(get_db),
    _admin_user: AdminUser = Depends(get_current_admin),
) -> BucketRead:
    bucket = db.get(Bucket, bucket_id)
    if bucket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bucket not found.")

    data = payload.model_dump(exclude_unset=True)
    if "name" in data:
        existing_bucket = db.execute(
            select(Bucket).where(Bucket.name == data["name"]).where(Bucket.id != bucket_id)
        ).scalar_one_or_none()
        if existing_bucket is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Bucket name already exists.")

    if "zone_id" in data and db.get(Zone, data["zone_id"]) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Zone not found.")

    if "base_dir" in data:
        try:
            data["base_dir"] = normalize_ftp_dir(data["base_dir"])
        except InvalidPathError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if "region" in data and data["region"] is not None:
        data["region"] = data["region"].strip()
        region = db.execute(select(Region).where(Region.code == data["region"])).scalar_one_or_none()
        if region is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Region not found.")
        data["region"] = region.code

    for field, value in data.items():
        setattr(bucket, field, value)

    db.commit()
    db.refresh(bucket)
    return _bucket_to_schema(db, bucket)


@router.delete("/buckets/{bucket_id}", response_model=MessageResponse)
def delete_bucket(
    bucket_id: int,
    db: Session = Depends(get_db),
    _admin_user: AdminUser = Depends(get_current_admin),
) -> MessageResponse:
    bucket = db.get(Bucket, bucket_id)
    if bucket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bucket not found.")

    object_count = int(db.execute(select(func.count(Object.id)).where(Object.bucket_id == bucket.id)).scalar_one() or 0)
    if object_count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Bucket cannot be deleted while indexed objects still exist.",
        )

    db.delete(bucket)
    db.commit()
    return MessageResponse(message="Bucket deleted.")


@router.get("/buckets/{bucket_name}/objects", response_model=BucketObjectsResponse)
def list_bucket_objects(
    bucket_name: str,
    prefix: str | None = None,
    db: Session = Depends(get_db),
    _admin_user: AdminUser = Depends(get_current_admin),
) -> BucketObjectsResponse:
    manager = StorageManager(db=db, ftp_timeout=get_effective_ftp_timeout())
    try:
        data = manager.list_objects_for_bucket(bucket_name, prefix=prefix)
        data["objects"] = [_object_to_schema(db, row) for row in data["objects"]]
        return BucketObjectsResponse.model_validate(data)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidPathError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except error_perm as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except InsufficientZoneStorageError as exc:
        raise HTTPException(status_code=507, detail=str(exc)) from exc
    except StorageOperationError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/buckets/{bucket_name}/search", response_model=SearchResponse)
def search_bucket_objects(
    bucket_name: str,
    q: str,
    db: Session = Depends(get_db),
    _admin_user: AdminUser = Depends(get_current_admin),
) -> SearchResponse:
    manager = StorageManager(db=db, ftp_timeout=get_effective_ftp_timeout())
    try:
        results = manager.search_objects(bucket_name, q)
        return SearchResponse(bucket=bucket_name, query=q, objects=[_object_to_schema(db, row) for row in results], count=len(results))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/buckets/{bucket_name}/upload")
def upload_object_via_admin(
    bucket_name: str,
    file: UploadFile = File(...),
    object_key: str | None = Form(default=None),
    db: Session = Depends(get_db),
    _admin_user: AdminUser = Depends(get_current_admin),
):
    effective_object_key = object_key or file.filename
    if not effective_object_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Object key is required.")

    manager = StorageManager(db=db, ftp_timeout=get_effective_ftp_timeout())
    try:
        row = manager.put_object(bucket_name, effective_object_key, file.file)
        return {"message": "Upload complete.", "object": _object_to_schema(db, row)}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidPathError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except error_perm as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InsufficientZoneStorageError as exc:
        raise HTTPException(status_code=507, detail=str(exc)) from exc
    except StorageOperationError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Could not connect to the FTP server: {exc}") from exc


@router.delete("/buckets/{bucket_name}/objects/{object_path:path}", response_model=MessageResponse)
def delete_object_via_admin(
    bucket_name: str,
    object_path: str,
    db: Session = Depends(get_db),
    _admin_user: AdminUser = Depends(get_current_admin),
) -> MessageResponse:
    manager = StorageManager(db=db, ftp_timeout=get_effective_ftp_timeout())
    try:
        manager.delete_object(bucket_name, object_path)
        return MessageResponse(message="Object deleted.")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidPathError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except error_perm as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except StorageOperationError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Could not connect to the FTP server: {exc}") from exc


@router.get("/buckets/{bucket_name}/download/{object_path:path}")
def download_object_via_admin(
    bucket_name: str,
    object_path: str,
    db: Session = Depends(get_db),
    _admin_user: AdminUser = Depends(get_current_admin),
) -> StreamingResponse:
    manager = StorageManager(db=db, ftp_timeout=get_effective_ftp_timeout())
    try:
        file_obj, _row = manager.download_object(bucket_name, object_path)
        headers = {"Content-Disposition": f'attachment; filename="{basename(object_path)}"'}
        return StreamingResponse(iter_file_chunks(file_obj), media_type="application/octet-stream", headers=headers)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidPathError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Could not connect to the FTP server: {exc}") from exc
    except StorageOperationError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/buckets/{bucket_name}/view/{object_path:path}")
def view_object_via_admin(
    bucket_name: str,
    object_path: str,
    db: Session = Depends(get_db),
    _admin_user: AdminUser = Depends(get_current_admin),
) -> StreamingResponse:
    manager = StorageManager(db=db, ftp_timeout=get_effective_ftp_timeout())
    try:
        file_obj, _row = manager.download_object(bucket_name, object_path)
        media_type = mimetypes.guess_type(object_path)[0] or "application/octet-stream"
        return StreamingResponse(iter_file_chunks(file_obj), media_type=media_type)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidPathError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except error_perm as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except StorageOperationError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Could not connect to the FTP server: {exc}") from exc


@router.post("/buckets/{bucket_name}/presign", response_model=PresignResponse)
def presign_object_via_admin(
    bucket_name: str,
    payload: PresignRequest,
    request: Request,
    db: Session = Depends(get_db),
    site_db: Session = Depends(get_site_db),
    _admin_user: AdminUser = Depends(get_current_admin),
) -> PresignResponse:
    manager = StorageManager(db=db, ftp_timeout=get_effective_ftp_timeout())
    try:
        bucket, _zone = manager.get_bucket_and_zone(bucket_name)
        normalized_object_key = normalize_object_key(payload.object_key)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidPathError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    effective_settings = load_effective_s3_settings(site_db, db, request_base_url=str(request.base_url).rstrip("/"))
    access_key = (
        get_access_key_by_access_key_id(db, payload.access_key_id)
        if payload.access_key_id
        else get_default_access_key(db)
    )
    if access_key is None or not access_key.enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No enabled access key is available for presigned links.")

    expires_in = payload.expires_in or effective_settings.s3_presign_expiry_seconds
    url, expires_at = generate_presigned_get_url(
        effective_settings=effective_settings,
        bucket_name=bucket.name,
        object_key=normalized_object_key,
        access_key_id=access_key.access_key_id,
        secret_access_key=access_key.secret_access_key,
        region=bucket.region,
        expires_in=expires_in,
    )
    return PresignResponse(
        bucket=bucket.name,
        object_key=normalized_object_key,
        region=bucket.region,
        access_key_id=access_key.access_key_id,
        expires_at=expires_at,
        url=url,
    )


@router.get("/buckets/{bucket_name}/sync/preview", response_model=SyncPreviewResponse)
def preview_bucket_sync(
    bucket_name: str,
    db: Session = Depends(get_db),
    _admin_user: AdminUser = Depends(get_current_admin),
) -> SyncPreviewResponse:
    service = SyncService(db=db, ftp_timeout=get_effective_ftp_timeout())
    try:
        return SyncPreviewResponse.model_validate(service.compare_ftp_to_db(bucket_name))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidPathError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Could not connect to the FTP server: {exc}") from exc


@router.post("/buckets/{bucket_name}/sync/repair", response_model=SyncRepairResponse)
def repair_bucket_sync(
    bucket_name: str,
    db: Session = Depends(get_db),
    _admin_user: AdminUser = Depends(get_current_admin),
) -> SyncRepairResponse:
    service = SyncService(db=db, ftp_timeout=get_effective_ftp_timeout())
    try:
        return SyncRepairResponse.model_validate(service.repair_bucket(bucket_name))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidPathError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Could not connect to the FTP server: {exc}") from exc


@router.get("/buckets/{bucket_name}/sync/status", response_model=SyncStatusResponse)
def bucket_sync_status(
    bucket_name: str,
    db: Session = Depends(get_db),
    _admin_user: AdminUser = Depends(get_current_admin),
) -> SyncStatusResponse:
    service = SyncService(db=db, ftp_timeout=get_effective_ftp_timeout())
    return SyncStatusResponse.model_validate(service.get_status(bucket_name))


@router.get("/buckets/{bucket_name}/zone-sync/preview", response_model=ZoneSyncPreviewResponse)
def preview_bucket_zone_sync(
    bucket_name: str,
    db: Session = Depends(get_db),
    _admin_user: AdminUser = Depends(get_current_admin),
) -> ZoneSyncPreviewResponse:
    service = ZoneSyncService(db=db, ftp_timeout=get_effective_ftp_timeout())
    try:
        return ZoneSyncPreviewResponse.model_validate(service.compare_bucket(bucket_name))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidPathError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except InsufficientZoneStorageError as exc:
        raise HTTPException(status_code=507, detail=str(exc)) from exc
    except StorageOperationError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Could not connect to the FTP server: {exc}") from exc


@router.post("/buckets/{bucket_name}/zone-sync/repair", response_model=ZoneSyncRepairResponse)
def repair_bucket_zone_sync(
    bucket_name: str,
    db: Session = Depends(get_db),
    _admin_user: AdminUser = Depends(get_current_admin),
) -> ZoneSyncRepairResponse:
    service = ZoneSyncService(db=db, ftp_timeout=get_effective_ftp_timeout())
    try:
        return ZoneSyncRepairResponse.model_validate(service.repair_bucket(bucket_name))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidPathError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except InsufficientZoneStorageError as exc:
        raise HTTPException(status_code=507, detail=str(exc)) from exc
    except StorageOperationError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Could not connect to the FTP server: {exc}") from exc


@router.post("/sync/rescan-all")
def rescan_all_buckets(
    db: Session = Depends(get_db),
    _admin_user: AdminUser = Depends(get_current_admin),
):
    service = SyncService(db=db, ftp_timeout=get_effective_ftp_timeout())
    return {"results": service.rescan_all_buckets()}


@router.get("/system/status", response_model=SystemStatusResponse)
def system_status(
    request: Request,
    site_db: Session = Depends(get_site_db),
    _admin_user: AdminUser = Depends(get_current_admin),
) -> SystemStatusResponse:
    request_base_url = str(request.base_url).rstrip("/")
    site_settings = load_site_settings(site_db, request_base_url=request_base_url)
    object_database_status = get_object_database_status(core_db=site_db)
    admin_user_total = int(site_db.execute(select(func.count(AdminUser.id))).scalar_one() or 0)

    object_counts: dict[str, object] = {
        "s3_service_name": None,
        "s3_default_region": None,
        "s3_default_access_key_id": None,
        "s3_access_key_count": None,
        "s3_require_sigv4": None,
        "s3_presign_expiry_seconds": None,
        "zone_total": None,
        "zone_enabled": None,
        "bucket_total": None,
        "bucket_enabled": None,
        "object_total": None,
        "zone_server_total": None,
        "mirror_all_zone_total": None,
        "sync_statuses": [],
    }

    if object_database_status.available:
        try:
            configure_object_database(database_url=object_database_status.database_url)
            with SessionLocal() as db:
                if apply_pending_object_configuration(site_db, db):
                    db.commit()
                    site_db.commit()
                effective_settings = load_effective_s3_settings(site_db, db, request_base_url=request_base_url)
                default_access_key = get_default_access_key(db)
                sync_service = SyncService(db=db, ftp_timeout=site_settings.ftp_timeout)
                object_counts = {
                    "s3_service_name": effective_settings.s3_service_name,
                    "s3_default_region": effective_settings.s3_default_region,
                    "s3_default_access_key_id": default_access_key.access_key_id if default_access_key else None,
                    "s3_access_key_count": int(db.execute(select(func.count(S3AccessKey.id))).scalar_one() or 0),
                    "s3_require_sigv4": effective_settings.s3_require_sigv4,
                    "s3_presign_expiry_seconds": effective_settings.s3_presign_expiry_seconds,
                    "zone_total": int(db.execute(select(func.count(Zone.id))).scalar_one() or 0),
                    "zone_enabled": int(db.execute(select(func.count(Zone.id)).where(Zone.enabled.is_(True))).scalar_one() or 0),
                    "bucket_total": int(db.execute(select(func.count(Bucket.id))).scalar_one() or 0),
                    "bucket_enabled": int(db.execute(select(func.count(Bucket.id)).where(Bucket.enabled.is_(True))).scalar_one() or 0),
                    "object_total": int(db.execute(select(func.count(Object.id))).scalar_one() or 0),
                    "zone_server_total": int(db.execute(select(func.count(ZoneServer.id))).scalar_one() or 0),
                    "mirror_all_zone_total": int(
                        db.execute(select(func.count(Zone.id)).where(Zone.pool_strategy == "mirror_all")).scalar_one() or 0
                    ),
                    "sync_statuses": [SyncStatusResponse.model_validate(item) for item in sync_service.list_statuses()],
                }
        except Exception as exc:
            site_db.rollback()
            object_database_status = get_object_database_status(core_db=site_db)
            if object_database_status.available:
                object_database_status.available = False
                object_database_status.error = f"Object metadata database is unavailable: {exc}"

    return SystemStatusResponse(
        app_name=settings.app_name,
        site_database_url=settings.database_url,
        object_database_url=site_settings.object_database_url,
        object_database_available=object_database_status.available,
        object_database_error=object_database_status.error,
        s3_endpoint_url=site_settings.public_base_url,
        s3_service_name=object_counts["s3_service_name"],
        s3_default_region=object_counts["s3_default_region"],
        s3_default_access_key_id=object_counts["s3_default_access_key_id"],
        s3_access_key_count=object_counts["s3_access_key_count"],
        s3_require_sigv4=object_counts["s3_require_sigv4"],
        s3_path_style_only=True,
        s3_presign_expiry_seconds=object_counts["s3_presign_expiry_seconds"],
        zone_total=object_counts["zone_total"],
        zone_enabled=object_counts["zone_enabled"],
        bucket_total=object_counts["bucket_total"],
        bucket_enabled=object_counts["bucket_enabled"],
        object_total=object_counts["object_total"],
        zone_server_total=object_counts["zone_server_total"],
        mirror_all_zone_total=object_counts["mirror_all_zone_total"],
        admin_user_total=admin_user_total,
        sync_statuses=object_counts["sync_statuses"],
    )
