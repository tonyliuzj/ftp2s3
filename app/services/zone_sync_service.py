from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Object, ObjectReplica, Zone, ZoneServer
from app.services.storage_manager import StorageManager


logger = logging.getLogger(__name__)


class ZoneSyncService:
    def __init__(self, db: Session, ftp_timeout: int = 30) -> None:
        self.db = db
        self.storage_manager = StorageManager(db=db, ftp_timeout=ftp_timeout)

    def compare_bucket(self, bucket_name: str) -> dict[str, Any]:
        scan = self.storage_manager.scan_bucket_zone_topology(bucket_name)
        bucket = scan["bucket"]
        zone: Zone = scan["zone"]
        servers: list[ZoneServer] = scan["servers"]
        expected_replica_count = self.storage_manager.expected_replica_count_for_zone(zone)

        objects = list(
            self.db.execute(select(Object).where(Object.bucket_id == bucket.id).order_by(Object.object_key)).scalars()
        )
        object_by_key = {row.object_key: row for row in objects}
        replica_rows = list(
            self.db.execute(
                select(ObjectReplica).join(Object, Object.id == ObjectReplica.object_id).where(Object.bucket_id == bucket.id)
            ).scalars()
        )
        db_replicas_by_object: dict[int, list[ObjectReplica]] = {}
        for replica in replica_rows:
            db_replicas_by_object.setdefault(replica.object_id, []).append(replica)

        actual_by_key: dict[str, list[dict[str, Any]]] = {}
        for item in scan["files"]:
            actual_by_key.setdefault(item["object_key"], []).append(item)

        missing_objects: list[dict[str, Any]] = []
        missing_expected_copies: list[dict[str, Any]] = []
        unexpected_replicas: list[dict[str, Any]] = []
        db_replica_mismatches: list[dict[str, Any]] = []

        for row in objects:
            actual_entries = self._sorted_actual_entries(actual_by_key.get(row.object_key, []))
            actual_server_names = [entry["zone_server_name"] for entry in actual_entries]
            replica_list = db_replicas_by_object.get(row.id, [])
            zone_replica_list = [replica for replica in replica_list if replica.zone_id == zone.id]
            db_replica_by_server = {replica.zone_server_id: replica for replica in zone_replica_list}
            db_primary = next((replica for replica in zone_replica_list if replica.is_primary), None)

            if not actual_entries:
                missing_objects.append(
                    {
                        "object_key": row.object_key,
                        "ftp_path": row.ftp_path,
                        "zone_name": zone.name,
                        "expected_server_name": self._server_name_for_replica(db_primary),
                        "actual_server_names": [],
                        "detail": "No FTP copy exists on any enabled server in this zone.",
                    }
                )
                continue

            if zone.pool_strategy == "mirror_all":
                actual_by_server = {entry["zone_server_id"]: entry for entry in actual_entries}
                for server in servers:
                    if server.id not in actual_by_server:
                        missing_expected_copies.append(
                            {
                                "object_key": row.object_key,
                                "ftp_path": row.ftp_path,
                                "zone_name": zone.name,
                                "expected_server_name": server.name,
                                "actual_server_names": actual_server_names,
                                "detail": "Mirror All expects this file on every enabled FTP server in the zone.",
                            }
                        )

                for server in servers:
                    actual_entry = actual_by_server.get(server.id)
                    if actual_entry is None:
                        continue
                    db_replica = db_replica_by_server.get(server.id)
                    if db_replica is None:
                        db_replica_mismatches.append(
                            {
                                "object_key": row.object_key,
                                "ftp_path": actual_entry["ftp_path"],
                                "zone_name": zone.name,
                                "expected_server_name": server.name,
                                "actual_server_names": actual_server_names,
                                "detail": "FTP has a zone copy but the database is missing its replica row.",
                            }
                        )
                        continue
                    if db_replica.ftp_path != actual_entry["ftp_path"]:
                        db_replica_mismatches.append(
                            {
                                "object_key": row.object_key,
                                "ftp_path": db_replica.ftp_path,
                                "zone_name": zone.name,
                                "expected_server_name": server.name,
                                "actual_server_names": actual_server_names,
                                "detail": "Database replica metadata points at a different FTP path.",
                            }
                        )

                for db_replica in zone_replica_list:
                    if db_replica.zone_server_id not in actual_by_server:
                        db_replica_mismatches.append(
                            {
                                "object_key": row.object_key,
                                "ftp_path": db_replica.ftp_path,
                                "zone_name": zone.name,
                                "expected_server_name": self._server_name_for_replica(db_replica),
                                "actual_server_names": actual_server_names,
                                "detail": "Database replica metadata points at a server that no longer has the file.",
                            }
                        )
                continue

            canonical_entry = self._choose_canonical_entry(actual_entries, db_primary=db_primary, servers=servers)
            if len(actual_entries) > 1:
                for extra_entry in actual_entries:
                    if extra_entry["zone_server_id"] == canonical_entry["zone_server_id"]:
                        continue
                    unexpected_replicas.append(
                        {
                            "object_key": row.object_key,
                            "ftp_path": extra_entry["ftp_path"],
                            "zone_name": zone.name,
                            "expected_server_name": canonical_entry["zone_server_name"],
                            "actual_server_names": actual_server_names,
                            "detail": "This pool strategy expects only one live FTP copy in the zone.",
                        }
                    )

            if db_primary is None:
                db_replica_mismatches.append(
                    {
                        "object_key": row.object_key,
                        "ftp_path": canonical_entry["ftp_path"],
                        "zone_name": zone.name,
                        "expected_server_name": canonical_entry["zone_server_name"],
                        "actual_server_names": actual_server_names,
                        "detail": "FTP has a live copy but the database has no primary replica row.",
                    }
                )
            elif (
                db_primary.zone_server_id != canonical_entry["zone_server_id"]
                or db_primary.ftp_path != canonical_entry["ftp_path"]
            ):
                db_replica_mismatches.append(
                    {
                        "object_key": row.object_key,
                        "ftp_path": db_primary.ftp_path,
                        "zone_name": zone.name,
                        "expected_server_name": self._server_name_for_replica(db_primary),
                        "actual_server_names": actual_server_names,
                        "detail": "Database primary replica points at a different server or path than FTP.",
                    }
                )

            for db_replica in zone_replica_list:
                if db_replica.zone_server_id == canonical_entry["zone_server_id"]:
                    continue
                db_replica_mismatches.append(
                    {
                        "object_key": row.object_key,
                        "ftp_path": db_replica.ftp_path,
                        "zone_name": zone.name,
                        "expected_server_name": self._server_name_for_replica(db_replica),
                        "actual_server_names": actual_server_names,
                        "detail": "Database still lists an extra replica for a single-copy pool strategy.",
                    }
                )

        for object_key, actual_entries in actual_by_key.items():
            if object_key in object_by_key:
                continue
            actual_server_names = [entry["zone_server_name"] for entry in actual_entries]
            for actual_entry in actual_entries:
                unexpected_replicas.append(
                    {
                        "object_key": object_key,
                        "ftp_path": actual_entry["ftp_path"],
                        "zone_name": zone.name,
                        "expected_server_name": None,
                        "actual_server_names": actual_server_names,
                        "detail": "FTP copy exists without a matching logical object row in the database.",
                    }
                )

        summary = {
            "object_total": len(objects),
            "expected_replicas": len(objects) * expected_replica_count,
            "actual_replicas": len(scan["files"]),
            "missing_objects": len(missing_objects),
            "missing_expected_copies": len(missing_expected_copies),
            "unexpected_replicas": len(unexpected_replicas),
            "db_replica_mismatches": len(db_replica_mismatches),
            "repaired_replicas": 0,
        }
        return {
            "bucket": bucket.name,
            "summary": summary,
            "missing_objects": missing_objects,
            "missing_expected_copies": missing_expected_copies,
            "unexpected_replicas": unexpected_replicas,
            "db_replica_mismatches": db_replica_mismatches,
        }

    def repair_bucket(self, bucket_name: str) -> dict[str, Any]:
        preview = self.compare_bucket(bucket_name)
        scan = self.storage_manager.scan_bucket_zone_topology(bucket_name)
        bucket = scan["bucket"]
        zone: Zone = scan["zone"]
        servers: list[ZoneServer] = scan["servers"]
        server_ids = {server.id for server in servers}

        objects = list(
            self.db.execute(select(Object).where(Object.bucket_id == bucket.id).order_by(Object.object_key)).scalars()
        )
        actual_by_key: dict[str, list[dict[str, Any]]] = {}
        for item in scan["files"]:
            actual_by_key.setdefault(item["object_key"], []).append(item)

        inserted_replicas = 0
        updated_db_rows = 0
        repaired_replicas = 0

        try:
            for row in objects:
                actual_entries = self._sorted_actual_entries(actual_by_key.get(row.object_key, []))
                replica_rows = list(
                    self.db.execute(select(ObjectReplica).where(ObjectReplica.object_id == row.id).order_by(ObjectReplica.id)).scalars()
                )
                zone_replica_rows = [replica for replica in replica_rows if replica.zone_id == zone.id]

                if not actual_entries:
                    for stale_replica in replica_rows:
                        self.db.delete(stale_replica)
                        updated_db_rows += 1
                    continue

                if zone.pool_strategy == "mirror_all":
                    actual_by_server = {entry["zone_server_id"]: entry for entry in actual_entries}
                    primary_server_id = self._choose_primary_server_id(
                        actual_entries=actual_entries,
                        db_replicas=zone_replica_rows,
                        servers=servers,
                    )
                    source_entry = actual_by_server.get(primary_server_id) or actual_entries[0]
                    source_server = self.db.get(ZoneServer, source_entry["zone_server_id"])
                    if source_server is None:
                        continue

                    for server in servers:
                        should_be_primary = server.id == primary_server_id
                        actual_entry = actual_by_server.get(server.id)
                        if actual_entry is None:
                            stored = self.storage_manager.copy_replica_to_server(
                                source_server=source_server,
                                source_path=source_entry["ftp_path"],
                                target_zone=zone,
                                target_server=server,
                                target_path=row.ftp_path,
                                is_primary=should_be_primary,
                            )
                            actual_entry = {
                                "object_key": row.object_key,
                                "ftp_path": stored.ftp_path,
                                "size": stored.ftp_info.size,
                                "last_modified": stored.ftp_info.last_modified,
                                "zone_server_id": server.id,
                                "zone_server_name": server.name,
                            }
                            actual_by_server[server.id] = actual_entry
                            inserted_replicas += 1
                            repaired_replicas += 1

                        db_replica = next((replica for replica in zone_replica_rows if replica.zone_server_id == server.id), None)
                        created, changed = self._sync_db_replica(
                            object_row=row,
                            zone=zone,
                            zone_server=server,
                            ftp_path=actual_entry["ftp_path"],
                            is_primary=should_be_primary,
                            existing_replica=db_replica,
                        )
                        if created or changed:
                            updated_db_rows += 1
                            repaired_replicas += 1

                    for stale_replica in replica_rows:
                        if stale_replica.zone_server_id in server_ids:
                            continue
                        self.db.delete(stale_replica)
                        updated_db_rows += 1
                        repaired_replicas += 1

                    primary_entry = actual_by_server.get(primary_server_id) or actual_entries[0]
                    if self._update_object_metadata_from_actual(row, zone=zone, actual_entry=primary_entry):
                        updated_db_rows += 1
                    continue

                canonical_entry = self._choose_canonical_entry(
                    actual_entries,
                    db_primary=next((replica for replica in zone_replica_rows if replica.is_primary), None),
                    servers=servers,
                )
                canonical_server = self.db.get(ZoneServer, canonical_entry["zone_server_id"])
                if canonical_server is None:
                    continue

                for extra_entry in actual_entries:
                    if extra_entry["zone_server_id"] == canonical_entry["zone_server_id"]:
                        continue
                    extra_server = self.db.get(ZoneServer, extra_entry["zone_server_id"])
                    if extra_server is None:
                        continue
                    storage = self.storage_manager.get_storage_for_server(extra_server)
                    storage.delete_file(extra_entry["ftp_path"])
                    repaired_replicas += 1

                kept_replica = None
                for replica in replica_rows:
                    if replica.zone_server_id == canonical_server.id and replica.zone_id == zone.id:
                        kept_replica = replica
                        continue
                    self.db.delete(replica)
                    updated_db_rows += 1
                    repaired_replicas += 1

                created, changed = self._sync_db_replica(
                    object_row=row,
                    zone=zone,
                    zone_server=canonical_server,
                    ftp_path=canonical_entry["ftp_path"],
                    is_primary=True,
                    existing_replica=kept_replica,
                )
                if created or changed:
                    updated_db_rows += 1
                    repaired_replicas += 1

                if self._update_object_metadata_from_actual(row, zone=zone, actual_entry=canonical_entry):
                    updated_db_rows += 1

            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Zone sync repair failed for bucket %s", bucket_name)
            raise

        result_summary = {
            **preview["summary"],
            "repaired_replicas": repaired_replicas,
        }
        return {
            "bucket": bucket_name,
            "inserted_replicas": inserted_replicas,
            "updated_db_rows": updated_db_rows,
            "repaired_replicas": repaired_replicas,
            "summary": result_summary,
        }

    def _sync_db_replica(
        self,
        *,
        object_row: Object,
        zone: Zone,
        zone_server: ZoneServer,
        ftp_path: str,
        is_primary: bool,
        existing_replica: ObjectReplica | None,
    ) -> tuple[bool, bool]:
        if existing_replica is None:
            _row, created, changed = self.storage_manager.upsert_object_replica(
                object_row=object_row,
                zone=zone,
                zone_server=zone_server,
                ftp_path=ftp_path,
                is_primary=is_primary,
            )
            return created, changed

        before = (
            existing_replica.zone_id,
            existing_replica.zone_server_id,
            existing_replica.ftp_path,
            existing_replica.is_primary,
        )
        self.storage_manager.upsert_object_replica(
            object_row=object_row,
            zone=zone,
            zone_server=zone_server,
            ftp_path=ftp_path,
            is_primary=is_primary,
        )
        after = (
            existing_replica.zone_id,
            existing_replica.zone_server_id,
            existing_replica.ftp_path,
            existing_replica.is_primary,
        )
        return False, before != after

    @staticmethod
    def _update_object_metadata_from_actual(row: Object, *, zone: Zone, actual_entry: dict[str, Any]) -> bool:
        before = (row.zone_id, row.ftp_path, row.size, row.last_modified)
        row.zone_id = zone.id
        row.ftp_path = actual_entry["ftp_path"]
        row.size = actual_entry["size"]
        row.last_modified = actual_entry["last_modified"]
        after = (row.zone_id, row.ftp_path, row.size, row.last_modified)
        return before != after

    @staticmethod
    def _sorted_actual_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(entries, key=lambda entry: (entry["zone_server_name"], entry["ftp_path"]))

    @staticmethod
    def _server_name_for_replica(replica: ObjectReplica | None) -> str | None:
        if replica is None or replica.zone_server is None:
            return None
        return replica.zone_server.name

    @staticmethod
    def _choose_canonical_entry(
        actual_entries: list[dict[str, Any]],
        *,
        db_primary: ObjectReplica | None,
        servers: list[ZoneServer],
    ) -> dict[str, Any]:
        if db_primary is not None:
            preferred = next(
                (entry for entry in actual_entries if entry["zone_server_id"] == db_primary.zone_server_id),
                None,
            )
            if preferred is not None:
                return preferred

        server_order = {server.id: index for index, server in enumerate(servers)}
        return sorted(
            actual_entries,
            key=lambda entry: (
                server_order.get(entry["zone_server_id"], 999999),
                entry["ftp_path"],
            ),
        )[0]

    @staticmethod
    def _choose_primary_server_id(
        *,
        actual_entries: list[dict[str, Any]],
        db_replicas: list[ObjectReplica],
        servers: list[ZoneServer],
    ) -> int:
        actual_server_ids = {entry["zone_server_id"] for entry in actual_entries}
        db_primary = next((replica for replica in db_replicas if replica.is_primary), None)
        if db_primary is not None and db_primary.zone_server_id in actual_server_ids:
            return db_primary.zone_server_id

        server_order = {server.id: index for index, server in enumerate(servers)}
        return sorted(actual_entries, key=lambda entry: server_order.get(entry["zone_server_id"], 999999))[0][
            "zone_server_id"
        ]
