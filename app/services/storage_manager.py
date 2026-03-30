from __future__ import annotations

import logging
from dataclasses import dataclass
from ftplib import error_perm
from typing import BinaryIO

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Bucket, Object, ObjectReplica, Zone, ZoneServer
from app.pathing import ftp_path_to_object_key, join_ftp_path, normalize_object_key, normalize_prefix
from app.services.ftp_storage import FTPFileInfo, FTPStorage


logger = logging.getLogger(__name__)


class StorageOperationError(RuntimeError):
    """Raised when a zone topology operation fails."""


class InsufficientZoneStorageError(StorageOperationError):
    """Raised when no enabled server can accept a new upload."""


@dataclass(slots=True)
class WriteReplicaPlan:
    zone: Zone
    ftp_path: str
    is_primary: bool
    target_server_id: int | None = None


@dataclass(slots=True)
class StoredReplica:
    zone: Zone
    zone_server: ZoneServer
    ftp_path: str
    ftp_info: FTPFileInfo
    is_primary: bool


class StorageManager:
    def __init__(self, db: Session, ftp_timeout: int = 30) -> None:
        self.db = db
        self.ftp_timeout = ftp_timeout

    def list_buckets(self) -> list[Bucket]:
        return list(self.db.execute(select(Bucket).where(Bucket.enabled.is_(True)).order_by(Bucket.name)).scalars())

    def get_bucket_and_zone(self, bucket_name: str) -> tuple[Bucket, Zone]:
        bucket = self.db.execute(select(Bucket).where(Bucket.name == bucket_name)).scalar_one_or_none()
        if bucket is None:
            raise ValueError(f"Bucket '{bucket_name}' was not found.")
        if not bucket.enabled:
            raise ValueError(f"Bucket '{bucket_name}' is disabled.")

        zone = self.db.get(Zone, bucket.zone_id)
        if zone is None:
            raise ValueError(f"Zone for bucket '{bucket_name}' was not found.")
        if not zone.enabled:
            raise ValueError(f"Zone '{zone.name}' is disabled.")

        return bucket, zone

    def build_ftp_path(self, bucket: Bucket, object_key: str) -> str:
        normalized_key = normalize_object_key(object_key)
        return join_ftp_path(bucket.base_dir, normalized_key)

    def get_zone_servers(self, zone: Zone, *, enabled_only: bool = True) -> list[ZoneServer]:
        query = select(ZoneServer).where(ZoneServer.zone_id == zone.id)
        if enabled_only:
            query = query.where(ZoneServer.enabled.is_(True))
        query = query.order_by(ZoneServer.sort_order, ZoneServer.id)
        return list(self.db.execute(query).scalars())

    def get_storage_for_server(self, zone_server: ZoneServer) -> FTPStorage:
        return FTPStorage(
            host=zone_server.ftp_host,
            port=zone_server.ftp_port,
            username=zone_server.ftp_username,
            password=zone_server.ftp_password,
            timeout=self.ftp_timeout,
        )

    def get_storage_for_zone(self, zone: Zone) -> FTPStorage:
        servers = self.get_zone_servers(zone)
        if not servers:
            raise StorageOperationError(f"Zone '{zone.name}' has no enabled FTP servers.")
        return self.get_storage_for_server(servers[0])

    def expected_replica_count_for_zone(self, zone: Zone) -> int:
        if zone.pool_strategy == "mirror_all":
            return len(self.get_zone_servers(zone))
        return 1

    def list_objects_for_bucket(self, bucket_name: str, prefix: str | None = None) -> dict[str, object]:
        bucket, _zone = self.get_bucket_and_zone(bucket_name)
        normalized_prefix = normalize_prefix(prefix)
        prefix_filter = f"{normalized_prefix}/" if normalized_prefix else ""

        query = select(Object).where(Object.bucket_id == bucket.id)
        if prefix_filter:
            query = query.where(Object.object_key.startswith(prefix_filter))
        query = query.order_by(Object.object_key)
        rows = list(self.db.execute(query).scalars())

        directories: dict[str, dict[str, str]] = {}
        objects: list[Object] = []

        for row in rows:
            relative_key = row.object_key
            if prefix_filter:
                relative_key = row.object_key.removeprefix(prefix_filter)

            if "/" in relative_key:
                directory_name = relative_key.split("/", 1)[0]
                directory_prefix = f"{prefix_filter}{directory_name}" if prefix_filter else directory_name
                directories[directory_prefix] = {"name": directory_name, "prefix": directory_prefix}
                continue

            objects.append(row)

        return {
            "bucket": bucket.name,
            "prefix": normalized_prefix,
            "directories": [directories[key] for key in sorted(directories)],
            "objects": objects,
            "count": len(objects),
        }

    def search_objects(self, bucket_name: str, query_text: str) -> list[Object]:
        bucket, _zone = self.get_bucket_and_zone(bucket_name)
        pattern = f"%{query_text.strip()}%"
        query = (
            select(Object)
            .where(Object.bucket_id == bucket.id)
            .where(or_(Object.object_key.ilike(pattern), Object.ftp_path.ilike(pattern)))
            .order_by(Object.object_key)
        )
        return list(self.db.execute(query).scalars())

    def put_object(
        self,
        bucket_name: str,
        object_key: str,
        file_stream: BinaryIO,
        size: int | None = None,
    ) -> Object:
        bucket, zone = self.get_bucket_and_zone(bucket_name)
        normalized_key = normalize_object_key(object_key)
        ftp_path = self.build_ftp_path(bucket, normalized_key)
        existing_row = self.db.execute(
            select(Object).where(Object.bucket_id == bucket.id).where(Object.object_key == normalized_key)
        ).scalar_one_or_none()
        existing_replicas = self._existing_replicas_by_server(existing_row)
        plans = self._build_write_replica_plans(zone=zone, ftp_path=ftp_path, existing_replicas=existing_replicas)

        uploaded: list[StoredReplica] = []
        try:
            for plan in plans:
                uploaded.append(
                    self._upload_replica(
                        zone=plan.zone,
                        ftp_path=plan.ftp_path,
                        file_stream=file_stream,
                        size=size,
                        is_primary=plan.is_primary,
                        target_server_id=plan.target_server_id,
                    )
                )
        except Exception as exc:
            self.db.rollback()
            if existing_row is None:
                self._cleanup_uploaded_replicas(uploaded)
            else:
                logger.warning(
                    "Upload for %s/%s partially updated zone copies; Zone Sync may be required: %s",
                    bucket_name,
                    normalized_key,
                    exc,
                )
                raise StorageOperationError(
                    f"Upload updated some FTP copies before failing. Run Zone Sync to repair the zone. {exc}"
                ) from exc
            raise

        primary_replica = next((item for item in uploaded if item.is_primary), None)
        if primary_replica is None:
            raise StorageOperationError("Primary replica upload did not complete.")

        try:
            row, _created, _changed = self.upsert_object_metadata(
                bucket=bucket,
                zone=zone,
                object_key=normalized_key,
                ftp_path=primary_replica.ftp_path,
                size=size if size is not None else primary_replica.ftp_info.size,
                last_modified=primary_replica.ftp_info.last_modified,
            )
            self.db.flush()

            keep_server_ids: set[int] = set()
            for stored in uploaded:
                self.upsert_object_replica(
                    object_row=row,
                    zone=stored.zone,
                    zone_server=stored.zone_server,
                    ftp_path=stored.ftp_path,
                    is_primary=stored.is_primary,
                )
                keep_server_ids.add(stored.zone_server.id)

            self._remove_stale_replica_rows(row, keep_server_ids=keep_server_ids)
            self.db.commit()
            self.db.refresh(row)
            return row
        except Exception:
            self.db.rollback()
            logger.exception("Database write failed after FTP upload for %s", ftp_path)
            raise

    def download_object(self, bucket_name: str, object_key: str) -> tuple[BinaryIO, Object | None]:
        bucket, zone = self.get_bucket_and_zone(bucket_name)
        normalized_key = normalize_object_key(object_key)
        row = self.db.execute(
            select(Object).where(Object.bucket_id == bucket.id).where(Object.object_key == normalized_key)
        ).scalar_one_or_none()

        errors: list[Exception] = []
        for replica in self._candidate_replicas_for_download(bucket, zone, normalized_key, row):
            try:
                storage = self.get_storage_for_server(replica.zone_server)
                return storage.download_file(replica.ftp_path), row
            except (error_perm, OSError) as exc:
                errors.append(exc)

        if errors:
            raise errors[-1]
        raise ValueError(f"Object '{normalized_key}' was not found in bucket '{bucket_name}'.")

    def get_object_file_info(self, bucket_name: str, object_key: str) -> FTPFileInfo:
        bucket, zone = self.get_bucket_and_zone(bucket_name)
        normalized_key = normalize_object_key(object_key)
        row = self.db.execute(
            select(Object).where(Object.bucket_id == bucket.id).where(Object.object_key == normalized_key)
        ).scalar_one_or_none()

        errors: list[Exception] = []
        for replica in self._candidate_replicas_for_download(bucket, zone, normalized_key, row):
            try:
                storage = self.get_storage_for_server(replica.zone_server)
                return storage.get_file_info(replica.ftp_path)
            except (error_perm, OSError) as exc:
                errors.append(exc)

        if errors:
            raise errors[-1]
        raise ValueError(f"Object '{normalized_key}' was not found in bucket '{bucket_name}'.")

    def delete_object(self, bucket_name: str, object_key: str) -> None:
        bucket, zone = self.get_bucket_and_zone(bucket_name)
        normalized_key = normalize_object_key(object_key)
        row = self.db.execute(
            select(Object).where(Object.bucket_id == bucket.id).where(Object.object_key == normalized_key)
        ).scalar_one_or_none()

        replicas = self._candidate_replicas_for_delete(bucket, zone, normalized_key, row)
        fatal_errors: list[Exception] = []
        for replica in replicas:
            try:
                storage = self.get_storage_for_server(replica.zone_server)
                storage.delete_file(replica.ftp_path)
            except error_perm as exc:
                if str(exc).startswith("550"):
                    continue
                fatal_errors.append(exc)
            except OSError as exc:
                fatal_errors.append(exc)

        if fatal_errors:
            raise fatal_errors[-1]

        if row is not None:
            try:
                self.db.delete(row)
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise

    def upsert_object_metadata(
        self,
        *,
        bucket: Bucket,
        zone: Zone,
        object_key: str,
        ftp_path: str,
        size: int | None,
        last_modified,
    ) -> tuple[Object, bool, bool]:
        normalized_key = normalize_object_key(object_key)
        existing_row = self.db.execute(
            select(Object)
            .where(Object.bucket_id == bucket.id)
            .where(or_(Object.object_key == normalized_key, Object.ftp_path == ftp_path))
        ).scalar_one_or_none()

        created = existing_row is None
        row = existing_row or Object(bucket_id=bucket.id, zone_id=zone.id, object_key=normalized_key, ftp_path=ftp_path)
        if created:
            self.db.add(row)

        changed = any(
            [
                created,
                row.bucket_id != bucket.id,
                row.zone_id != zone.id,
                row.object_key != normalized_key,
                row.ftp_path != ftp_path,
                row.size != size,
                row.last_modified != last_modified,
            ]
        )
        row.bucket_id = bucket.id
        row.zone_id = zone.id
        row.object_key = normalized_key
        row.ftp_path = ftp_path
        row.size = size
        row.last_modified = last_modified
        return row, created, changed

    def upsert_object_replica(
        self,
        *,
        object_row: Object,
        zone: Zone,
        zone_server: ZoneServer,
        ftp_path: str,
        is_primary: bool,
    ) -> tuple[ObjectReplica, bool, bool]:
        existing_row = self.db.execute(
            select(ObjectReplica)
            .where(ObjectReplica.object_id == object_row.id)
            .where(ObjectReplica.zone_server_id == zone_server.id)
        ).scalar_one_or_none()

        created = existing_row is None
        row = existing_row or ObjectReplica(
            object_id=object_row.id,
            zone_id=zone.id,
            zone_server_id=zone_server.id,
            ftp_path=ftp_path,
            is_primary=is_primary,
        )
        if created:
            self.db.add(row)

        if is_primary:
            for other_row in self.db.execute(
                select(ObjectReplica).where(ObjectReplica.object_id == object_row.id).where(ObjectReplica.id != row.id)
            ).scalars():
                other_row.is_primary = False

        changed = any(
            [
                created,
                row.zone_id != zone.id,
                row.zone_server_id != zone_server.id,
                row.ftp_path != ftp_path,
                row.is_primary != is_primary,
            ]
        )
        row.zone_id = zone.id
        row.zone_server_id = zone_server.id
        row.ftp_path = ftp_path
        row.is_primary = is_primary
        return row, created, changed

    def count_objects_for_bucket(self, bucket_id: int) -> int:
        return int(
            self.db.execute(select(func.count(Object.id)).where(Object.bucket_id == bucket_id)).scalar_one() or 0
        )

    def scan_bucket_source_zone(self, bucket_name: str) -> dict[str, object]:
        bucket, zone = self.get_bucket_and_zone(bucket_name)
        files_by_key: dict[str, dict[str, object]] = {}

        for zone_server in self.get_zone_servers(zone):
            storage = self.get_storage_for_server(zone_server)
            for item in storage.list_objects(bucket.base_dir):
                normalized_key = ftp_path_to_object_key(bucket.base_dir, item.ftp_path)
                files_by_key.setdefault(
                    normalized_key,
                    {
                        "object_key": normalized_key,
                        "ftp_path": item.ftp_path,
                        "size": item.size,
                        "last_modified": item.last_modified,
                        "zone_server_id": zone_server.id,
                        "zone_server_name": zone_server.name,
                    },
                )

        return {
            "bucket": bucket,
            "zone": zone,
            "files": sorted(files_by_key.values(), key=lambda row: row["object_key"]),
        }

    def scan_bucket_zone_topology(self, bucket_name: str) -> dict[str, object]:
        bucket, zone = self.get_bucket_and_zone(bucket_name)
        servers = self.get_zone_servers(zone)
        files: list[dict[str, object]] = []
        for zone_server in servers:
            storage = self.get_storage_for_server(zone_server)
            for item in storage.list_objects(bucket.base_dir):
                normalized_key = ftp_path_to_object_key(bucket.base_dir, item.ftp_path)
                files.append(
                    {
                        "object_key": normalized_key,
                        "ftp_path": item.ftp_path,
                        "size": item.size,
                        "last_modified": item.last_modified,
                        "zone_id": zone.id,
                        "zone_name": zone.name,
                        "zone_server_id": zone_server.id,
                        "zone_server_name": zone_server.name,
                    }
                )

        return {
            "bucket": bucket,
            "zone": zone,
            "servers": servers,
            "files": sorted(files, key=lambda row: (row["object_key"], row["zone_server_name"])),
        }

    def copy_replica_to_server(
        self,
        *,
        source_server: ZoneServer,
        source_path: str,
        target_zone: Zone,
        target_server: ZoneServer,
        target_path: str,
        is_primary: bool = False,
    ) -> StoredReplica:
        source_storage = self.get_storage_for_server(source_server)
        temp_file = source_storage.download_file(source_path)
        size = None
        try:
            size = source_storage.get_file_info(source_path).size
        except Exception:
            size = None
        return self._upload_replica(
            zone=target_zone,
            ftp_path=target_path,
            file_stream=temp_file,
            size=size,
            is_primary=is_primary,
            target_server_id=target_server.id,
        )

    def _build_write_replica_plans(
        self,
        *,
        zone: Zone,
        ftp_path: str,
        existing_replicas: dict[int, ObjectReplica],
    ) -> list[WriteReplicaPlan]:
        servers = self.get_zone_servers(zone)
        if not servers:
            raise StorageOperationError(f"Zone '{zone.name}' has no enabled FTP servers.")

        primary_server = self._choose_primary_server(zone=zone, servers=servers, existing_replicas=existing_replicas)
        if primary_server is None:
            raise StorageOperationError(f"Zone '{zone.name}' has no enabled FTP servers.")

        if zone.pool_strategy == "mirror_all":
            ordered_servers = [primary_server, *[server for server in servers if server.id != primary_server.id]]
            return [
                WriteReplicaPlan(
                    zone=zone,
                    ftp_path=ftp_path,
                    is_primary=server.id == primary_server.id,
                    target_server_id=server.id,
                )
                for server in ordered_servers
            ]

        return [
            WriteReplicaPlan(
                zone=zone,
                ftp_path=ftp_path,
                is_primary=True,
                target_server_id=primary_server.id if primary_server.id in existing_replicas else None,
            )
        ]

    def _choose_primary_server(
        self,
        *,
        zone: Zone,
        servers: list[ZoneServer],
        existing_replicas: dict[int, ObjectReplica],
    ) -> ZoneServer | None:
        primary_replica = next(
            (
                replica
                for replica in existing_replicas.values()
                if replica.zone_id == zone.id and replica.is_primary and any(server.id == replica.zone_server_id for server in servers)
            ),
            None,
        )
        if primary_replica is not None:
            return next((server for server in servers if server.id == primary_replica.zone_server_id), None)

        if zone.pool_strategy == "round_robin" and len(servers) > 1:
            return servers[zone.pool_cursor % len(servers)]
        return servers[0]

    def _upload_replica(
        self,
        *,
        zone: Zone,
        ftp_path: str,
        file_stream: BinaryIO,
        size: int | None,
        is_primary: bool,
        target_server_id: int | None,
    ) -> StoredReplica:
        if target_server_id is not None:
            zone_server = next((server for server in self.get_zone_servers(zone) if server.id == target_server_id), None)
            if zone_server is None:
                raise StorageOperationError(f"FTP server {target_server_id} is not enabled for zone '{zone.name}'.")
            ftp_info = self._upload_to_server(zone_server=zone_server, ftp_path=ftp_path, file_stream=file_stream, size=size)
        else:
            zone_server, ftp_info = self._upload_to_zone(zone=zone, ftp_path=ftp_path, file_stream=file_stream, size=size)

        return StoredReplica(zone=zone, zone_server=zone_server, ftp_path=ftp_path, ftp_info=ftp_info, is_primary=is_primary)

    def _upload_to_server(
        self,
        *,
        zone_server: ZoneServer,
        ftp_path: str,
        file_stream: BinaryIO,
        size: int | None,
    ) -> FTPFileInfo:
        if not zone_server.enabled:
            raise StorageOperationError(f"FTP server '{zone_server.name}' is disabled.")
        if not self._server_has_capacity(zone_server, size):
            raise InsufficientZoneStorageError(f"FTP server '{zone_server.name}' does not have enough free capacity.")

        storage = self.get_storage_for_server(zone_server)
        file_stream.seek(0)
        storage.upload_file(ftp_path, file_stream)
        return storage.get_file_info(ftp_path)

    def _upload_to_zone(
        self,
        *,
        zone: Zone,
        ftp_path: str,
        file_stream: BinaryIO,
        size: int | None,
    ) -> tuple[ZoneServer, FTPFileInfo]:
        ordered_servers = self._ordered_servers_for_write(zone)
        if not ordered_servers:
            raise StorageOperationError(f"Zone '{zone.name}' has no enabled FTP servers.")

        failures: list[Exception] = []
        for zone_server in ordered_servers:
            if not self._server_has_capacity(zone_server, size):
                failures.append(InsufficientZoneStorageError(f"FTP server '{zone_server.name}' is full."))
                continue

            storage = self.get_storage_for_server(zone_server)
            try:
                file_stream.seek(0)
                storage.upload_file(ftp_path, file_stream)
                ftp_info = storage.get_file_info(ftp_path)
                self._advance_pool_cursor(zone, zone_server)
                return zone_server, ftp_info
            except (error_perm, OSError) as exc:
                failures.append(exc)
                if self._looks_like_full_storage_error(exc):
                    continue
                raise

        if failures:
            raise InsufficientZoneStorageError(
                f"All enabled FTP servers for zone '{zone.name}' are full or unavailable for {ftp_path}."
            ) from failures[-1]
        raise InsufficientZoneStorageError(f"No enabled FTP server could accept {ftp_path}.")

    def _ordered_servers_for_write(self, zone: Zone) -> list[ZoneServer]:
        servers = self.get_zone_servers(zone)
        if not servers:
            return []

        if zone.pool_strategy == "round_robin" and len(servers) > 1:
            start_index = zone.pool_cursor % len(servers)
            return servers[start_index:] + servers[:start_index]
        return servers

    def _advance_pool_cursor(self, zone: Zone, used_server: ZoneServer) -> None:
        if zone.pool_strategy != "round_robin":
            return
        servers = self.get_zone_servers(zone)
        if len(servers) <= 1:
            return
        for index, server in enumerate(servers):
            if server.id == used_server.id:
                zone.pool_cursor = (index + 1) % len(servers)
                self.db.add(zone)
                return

    def _server_has_capacity(self, zone_server: ZoneServer, size: int | None) -> bool:
        if zone_server.capacity_bytes is None or size is None:
            return True
        used_bytes = int(
            self.db.execute(
                select(func.coalesce(func.sum(Object.size), 0))
                .select_from(ObjectReplica)
                .join(Object, Object.id == ObjectReplica.object_id)
                .where(ObjectReplica.zone_server_id == zone_server.id)
            ).scalar_one()
            or 0
        )
        return used_bytes + size <= int(zone_server.capacity_bytes)

    @staticmethod
    def _looks_like_full_storage_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return (
            message.startswith("552")
            or "no space" in message
            or "quota" in message
            or "disk full" in message
            or "insufficient storage" in message
        )

    def _existing_replicas_by_server(self, row: Object | None) -> dict[int, ObjectReplica]:
        if row is None:
            return {}
        replicas = list(
            self.db.execute(
                select(ObjectReplica).where(ObjectReplica.object_id == row.id).order_by(ObjectReplica.is_primary.desc(), ObjectReplica.id)
            ).scalars()
        )
        return {replica.zone_server_id: replica for replica in replicas}

    def _candidate_replicas_for_download(
        self,
        bucket: Bucket,
        source_zone: Zone,
        object_key: str,
        row: Object | None,
    ) -> list[StoredReplica]:
        ftp_path = row.ftp_path if row is not None else self.build_ftp_path(bucket, object_key)
        candidates: list[StoredReplica] = []
        seen_server_ids: set[int] = set()

        if row is not None:
            replica_rows = list(
                self.db.execute(
                    select(ObjectReplica)
                    .where(ObjectReplica.object_id == row.id)
                    .order_by(ObjectReplica.is_primary.desc(), ObjectReplica.id)
                ).scalars()
            )
            for replica in replica_rows:
                zone_server = self.db.get(ZoneServer, replica.zone_server_id)
                if zone_server is None or not zone_server.enabled:
                    continue
                if zone_server.id in seen_server_ids:
                    continue
                if replica.zone_id != source_zone.id:
                    continue
                candidates.append(
                    StoredReplica(
                        zone=source_zone,
                        zone_server=zone_server,
                        ftp_path=replica.ftp_path,
                        ftp_info=FTPFileInfo(ftp_path=replica.ftp_path),
                        is_primary=replica.is_primary,
                    )
                )
                seen_server_ids.add(zone_server.id)

        for zone_server in self.get_zone_servers(source_zone):
            if zone_server.id in seen_server_ids:
                continue
            candidates.append(
                StoredReplica(
                    zone=source_zone,
                    zone_server=zone_server,
                    ftp_path=ftp_path,
                    ftp_info=FTPFileInfo(ftp_path=ftp_path),
                    is_primary=False,
                )
            )
        return candidates

    def _candidate_replicas_for_delete(
        self,
        bucket: Bucket,
        source_zone: Zone,
        object_key: str,
        row: Object | None,
    ) -> list[StoredReplica]:
        ftp_path = row.ftp_path if row is not None else self.build_ftp_path(bucket, object_key)
        result: list[StoredReplica] = []
        seen_pairs: set[tuple[int, str]] = set()

        if row is not None:
            replica_rows = list(
                self.db.execute(select(ObjectReplica).where(ObjectReplica.object_id == row.id).order_by(ObjectReplica.id)).scalars()
            )
            for replica in replica_rows:
                zone = self.db.get(Zone, replica.zone_id) or source_zone
                zone_server = self.db.get(ZoneServer, replica.zone_server_id)
                if zone_server is None:
                    continue
                pair = (zone_server.id, replica.ftp_path)
                if pair in seen_pairs:
                    continue
                result.append(
                    StoredReplica(
                        zone=zone,
                        zone_server=zone_server,
                        ftp_path=replica.ftp_path,
                        ftp_info=FTPFileInfo(ftp_path=replica.ftp_path),
                        is_primary=replica.is_primary,
                    )
                )
                seen_pairs.add(pair)

        for zone_server in self.get_zone_servers(source_zone):
            pair = (zone_server.id, ftp_path)
            if pair in seen_pairs:
                continue
            result.append(
                StoredReplica(
                    zone=source_zone,
                    zone_server=zone_server,
                    ftp_path=ftp_path,
                    ftp_info=FTPFileInfo(ftp_path=ftp_path),
                    is_primary=False,
                )
            )
        return result

    def _remove_stale_replica_rows(self, object_row: Object, *, keep_server_ids: set[int]) -> None:
        stale_rows = list(
            self.db.execute(select(ObjectReplica).where(ObjectReplica.object_id == object_row.id).order_by(ObjectReplica.id)).scalars()
        )
        for replica in stale_rows:
            if replica.zone_server_id in keep_server_ids:
                continue
            zone_server = self.db.get(ZoneServer, replica.zone_server_id)
            if zone_server is not None:
                try:
                    storage = self.get_storage_for_server(zone_server)
                    storage.delete_file(replica.ftp_path)
                except error_perm as exc:
                    if not str(exc).startswith("550"):
                        raise
                except OSError:
                    logger.warning(
                        "Could not remove stale replica %s from server %s during upload cleanup.",
                        replica.ftp_path,
                        zone_server.name,
                    )
            self.db.delete(replica)

    def _cleanup_uploaded_replicas(self, uploaded: list[StoredReplica]) -> None:
        for replica in uploaded:
            try:
                storage = self.get_storage_for_server(replica.zone_server)
                storage.delete_file(replica.ftp_path)
            except Exception:
                logger.exception("Cleanup failed for %s on %s", replica.ftp_path, replica.zone_server.name)
