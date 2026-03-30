from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Bucket, Object, ZoneServer
from app.pathing import ftp_path_to_object_key
from app.services.storage_manager import StorageManager


logger = logging.getLogger(__name__)

SYNC_STATUS_REGISTRY: dict[str, dict[str, Any]] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SyncService:
    def __init__(self, db: Session, ftp_timeout: int = 30) -> None:
        self.db = db
        self.storage_manager = StorageManager(db=db, ftp_timeout=ftp_timeout)

    def scan_bucket(self, bucket_name: str) -> dict[str, Any]:
        return self.storage_manager.scan_bucket_source_zone(bucket_name)

    def compare_ftp_to_db(self, bucket_name: str, *, record_status: bool = True) -> dict[str, Any]:
        scan = self.scan_bucket(bucket_name)
        bucket: Bucket = scan["bucket"]
        ftp_files: list[dict[str, Any]] = scan["files"]
        db_rows = list(
            self.db.execute(select(Object).where(Object.bucket_id == bucket.id).order_by(Object.object_key)).scalars()
        )

        ftp_by_key = {item["object_key"]: item for item in ftp_files}
        ftp_by_path = {item["ftp_path"]: item for item in ftp_files}
        db_by_key = {row.object_key: row for row in db_rows}
        db_by_path = {row.ftp_path: row for row in db_rows}

        ftp_only_files: list[dict[str, Any]] = []
        db_only_files: list[dict[str, Any]] = []
        path_mismatches: list[dict[str, Any]] = []
        size_mismatches: list[dict[str, Any]] = []
        matched_db_ids: set[int] = set()

        for ftp_item in ftp_files:
            db_row = db_by_key.get(ftp_item["object_key"])
            if db_row is not None:
                matched_db_ids.add(db_row.id)
                if db_row.ftp_path != ftp_item["ftp_path"]:
                    path_mismatches.append(self._diff_from_pair(ftp_item, db_row))
                    continue
                if (
                    ftp_item["size"] is not None
                    and db_row.size is not None
                    and int(db_row.size) != int(ftp_item["size"])
                ):
                    size_mismatches.append(self._diff_from_pair(ftp_item, db_row))
                continue

            db_row = db_by_path.get(ftp_item["ftp_path"])
            if db_row is not None:
                matched_db_ids.add(db_row.id)
                path_mismatches.append(self._diff_from_pair(ftp_item, db_row))
                continue

            ftp_only_files.append(
                {
                    "object_key": ftp_item["object_key"],
                    "ftp_path": ftp_item["ftp_path"],
                    "size": ftp_item["size"],
                    "last_modified": ftp_item["last_modified"],
                }
            )

        for db_row in db_rows:
            if db_row.id in matched_db_ids:
                continue
            if db_row.object_key in ftp_by_key or db_row.ftp_path in ftp_by_path:
                continue
            db_only_files.append(
                {
                    "object_key": db_row.object_key,
                    "ftp_path": db_row.ftp_path,
                    "size": db_row.size,
                    "last_modified": db_row.last_modified,
                    "db_object_key": db_row.object_key,
                    "db_ftp_path": db_row.ftp_path,
                    "db_size": db_row.size,
                    "db_last_modified": db_row.last_modified,
                }
            )

        summary = {
            "ftp_total": len(ftp_files),
            "db_total": len(db_rows),
            "ftp_only": len(ftp_only_files),
            "db_only": len(db_only_files),
            "path_mismatches": len(path_mismatches),
            "size_mismatches": len(size_mismatches),
            "repaired_rows": 0,
        }

        preview = {
            "bucket": bucket.name,
            "summary": summary,
            "ftp_only_files": ftp_only_files,
            "db_only_files": db_only_files,
            "path_mismatches": path_mismatches,
            "size_mismatches": size_mismatches,
        }
        if record_status:
            self._store_status(bucket.name, action="preview", status="idle", summary=summary)
        return preview

    def repair_bucket(self, bucket_name: str) -> dict[str, Any]:
        self._store_status(bucket_name, action="repair", status="running")
        preview = self.compare_ftp_to_db(bucket_name, record_status=False)
        bucket, zone = self.storage_manager.get_bucket_and_zone(bucket_name)
        scan = self.scan_bucket(bucket_name)

        inserted = 0
        updated = 0
        deleted = 0

        try:
            for ftp_item in scan["files"]:
                row, created, changed = self.storage_manager.upsert_object_metadata(
                    bucket=bucket,
                    zone=zone,
                    object_key=ftp_item["object_key"],
                    ftp_path=ftp_item["ftp_path"],
                    size=ftp_item["size"],
                    last_modified=ftp_item["last_modified"],
                )
                zone_server = self.db.get(ZoneServer, ftp_item["zone_server_id"])
                if zone_server is not None:
                    self.storage_manager.upsert_object_replica(
                        object_row=row,
                        zone=zone,
                        zone_server=zone_server,
                        ftp_path=ftp_item["ftp_path"],
                        is_primary=True,
                    )
                if created:
                    inserted += 1
                elif changed:
                    updated += 1

            stale_rows = list(
                self.db.execute(select(Object).where(Object.bucket_id == bucket.id).order_by(Object.object_key)).scalars()
            )
            ftp_paths = {item["ftp_path"] for item in scan["files"]}
            ftp_keys = {item["object_key"] for item in scan["files"]}
            for row in stale_rows:
                if row.ftp_path not in ftp_paths and row.object_key not in ftp_keys:
                    self.db.delete(row)
                    deleted += 1

            self.db.commit()
        except Exception as exc:
            self.db.rollback()
            self._store_status(bucket_name, action="repair", status="error", error=str(exc))
            logger.exception("Repair failed for bucket %s", bucket_name)
            raise

        repaired_rows = inserted + updated + deleted
        summary = {
            **preview["summary"],
            "repaired_rows": repaired_rows,
        }
        result = {
            "bucket": bucket_name,
            "inserted": inserted,
            "updated": updated,
            "deleted": deleted,
            "repaired_rows": repaired_rows,
            "summary": summary,
        }
        self._store_status(bucket_name, action="repair", status="idle", summary=summary)
        return result

    def sync_object_metadata(self, bucket_name: str, ftp_path: str) -> dict[str, Any]:
        bucket, zone = self.storage_manager.get_bucket_and_zone(bucket_name)
        file_info = None
        source_server = None
        for zone_server in self.storage_manager.get_zone_servers(zone):
            storage = self.storage_manager.get_storage_for_server(zone_server)
            try:
                file_info = storage.get_file_info(ftp_path)
                source_server = zone_server
                break
            except Exception:
                continue

        if file_info is None or source_server is None:
            raise ValueError(f"FTP path '{ftp_path}' was not found in the source zone.")

        object_key = ftp_path_to_object_key(bucket.base_dir, ftp_path)
        row, _created, _changed = self.storage_manager.upsert_object_metadata(
            bucket=bucket,
            zone=zone,
            object_key=object_key,
            ftp_path=file_info.ftp_path,
            size=file_info.size,
            last_modified=file_info.last_modified,
        )
        self.storage_manager.upsert_object_replica(
            object_row=row,
            zone=zone,
            zone_server=source_server,
            ftp_path=file_info.ftp_path,
            is_primary=True,
        )
        self.db.commit()
        self.db.refresh(row)
        return {
            "bucket": bucket_name,
            "object_key": row.object_key,
            "ftp_path": row.ftp_path,
            "size": row.size,
            "last_modified": row.last_modified,
        }

    def rescan_all_buckets(self) -> list[dict[str, Any]]:
        buckets = list(self.db.execute(select(Bucket).where(Bucket.enabled.is_(True)).order_by(Bucket.name)).scalars())
        results: list[dict[str, Any]] = []
        for bucket in buckets:
            try:
                preview = self.compare_ftp_to_db(bucket.name)
                results.append({"bucket": bucket.name, "status": "ok", "summary": preview["summary"]})
            except Exception as exc:
                results.append({"bucket": bucket.name, "status": "error", "error": str(exc)})
                self._store_status(bucket.name, action="preview", status="error", error=str(exc))
        return results

    def get_status(self, bucket_name: str) -> dict[str, Any]:
        status = SYNC_STATUS_REGISTRY.get(bucket_name)
        if status is None:
            return {
                "bucket": bucket_name,
                "status": "idle",
                "action": None,
                "updated_at": None,
                "summary": None,
                "error": None,
            }
        return status

    def list_statuses(self) -> list[dict[str, Any]]:
        return [self.get_status(bucket_name) for bucket_name in sorted(SYNC_STATUS_REGISTRY)]

    def _store_status(
        self,
        bucket_name: str,
        *,
        action: str | None,
        status: str,
        summary: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        SYNC_STATUS_REGISTRY[bucket_name] = {
            "bucket": bucket_name,
            "status": status,
            "action": action,
            "updated_at": _utcnow(),
            "summary": summary,
            "error": error,
        }

    @staticmethod
    def _diff_from_pair(ftp_item: dict[str, Any], db_row: Object) -> dict[str, Any]:
        return {
            "object_key": ftp_item["object_key"],
            "ftp_path": ftp_item["ftp_path"],
            "size": ftp_item["size"],
            "last_modified": ftp_item["last_modified"],
            "db_object_key": db_row.object_key,
            "db_ftp_path": db_row.ftp_path,
            "db_size": db_row.size,
            "db_last_modified": db_row.last_modified,
        }
