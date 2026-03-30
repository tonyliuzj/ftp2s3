from __future__ import annotations

import mimetypes
from ftplib import error_perm

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.http_helpers import iter_file_chunks, request_to_spooled_file
from app.pathing import InvalidPathError
from app.s3_auth import require_s3_signature
from app.services.storage_manager import InsufficientZoneStorageError, StorageManager, StorageOperationError


router = APIRouter(tags=["s3-lite"], dependencies=[Depends(require_s3_signature)])


@router.get("/")
def list_buckets(db: Session = Depends(get_db)):
    manager = StorageManager(db=db, ftp_timeout=settings.ftp_timeout)
    buckets = manager.list_buckets()
    return {
        "note": "This endpoint accepts SigV4 credentials and presigned X-Amz query URLs, uses path-style bucket URLs, and still returns JSON instead of AWS XML.",
        "buckets": [
            {
                "name": bucket.name,
                "region": bucket.region,
                "zone_id": bucket.zone_id,
                "base_dir": bucket.base_dir,
            }
            for bucket in buckets
        ],
    }


@router.get("/{bucket_name}")
def list_objects(
    bucket_name: str,
    response: Response,
    prefix: str | None = None,
    location: str | None = None,
    db: Session = Depends(get_db),
):
    manager = StorageManager(db=db, ftp_timeout=settings.ftp_timeout)
    try:
        bucket, _zone = manager.get_bucket_and_zone(bucket_name)
        response.headers["x-amz-bucket-region"] = bucket.region
        if location is not None:
            return {
                "bucket": bucket.name,
                "region": bucket.region,
                "endpoint_style": "path",
            }
        data = manager.list_objects_for_bucket(bucket_name, prefix=prefix)
        return {
            "note": "This endpoint accepts SigV4 credentials and presigned X-Amz query URLs but still returns JSON instead of AWS XML.",
            "region": bucket.region,
            **data,
        }
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
    except OSError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Could not connect to the FTP server: {exc}") from exc


@router.put("/{bucket_name}/{object_path:path}")
async def upload_object(
    bucket_name: str,
    object_path: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    manager = StorageManager(db=db, ftp_timeout=settings.ftp_timeout)
    try:
        temp_file, size = await request_to_spooled_file(request)
        row = manager.put_object(bucket_name, object_path, temp_file, size=size)
        response.headers["x-amz-bucket-region"] = getattr(request.state, "s3_region", settings.s3_default_region)
        return {
            "message": "Upload complete.",
            "bucket": bucket_name,
            "object_key": row.object_key,
            "ftp_path": row.ftp_path,
            "size": row.size,
        }
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


@router.get("/{bucket_name}/{object_path:path}")
def download_object(bucket_name: str, object_path: str, request: Request, db: Session = Depends(get_db)) -> StreamingResponse:
    manager = StorageManager(db=db, ftp_timeout=settings.ftp_timeout)
    try:
        file_obj, _row = manager.download_object(bucket_name, object_path)
        headers = {
            "x-amz-bucket-region": getattr(request.state, "s3_region", settings.s3_default_region),
        }
        media_type = mimetypes.guess_type(object_path)[0] or "application/octet-stream"
        return StreamingResponse(iter_file_chunks(file_obj), media_type=media_type, headers=headers)
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


@router.delete("/{bucket_name}/{object_path:path}")
def delete_object(bucket_name: str, object_path: str, request: Request, db: Session = Depends(get_db)):
    manager = StorageManager(db=db, ftp_timeout=settings.ftp_timeout)
    try:
        manager.delete_object(bucket_name, object_path)
        return {
            "message": "Object deleted.",
            "bucket": bucket_name,
            "object_key": object_path,
            "region": getattr(request.state, "s3_region", settings.s3_default_region),
        }
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidPathError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except StorageOperationError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Could not connect to the FTP server: {exc}") from exc


@router.head("/{bucket_name}")
def head_bucket(bucket_name: str, response: Response, db: Session = Depends(get_db)) -> Response:
    manager = StorageManager(db=db, ftp_timeout=settings.ftp_timeout)
    try:
        bucket, _zone = manager.get_bucket_and_zone(bucket_name)
        response.headers["x-amz-bucket-region"] = bucket.region
        return response
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Could not connect to the FTP server: {exc}") from exc


@router.head("/{bucket_name}/{object_path:path}")
def head_object(bucket_name: str, object_path: str, response: Response, db: Session = Depends(get_db)) -> Response:
    manager = StorageManager(db=db, ftp_timeout=settings.ftp_timeout)
    try:
        bucket, _zone = manager.get_bucket_and_zone(bucket_name)
        normalized_info = manager.get_object_file_info(bucket_name, object_path)
        response.headers["x-amz-bucket-region"] = bucket.region
        if normalized_info.size is not None:
            response.headers["content-length"] = str(normalized_info.size)
        return response
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
