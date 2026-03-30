from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from typing import Iterable
from urllib.parse import parse_qsl, quote, unquote, urlparse, urlunparse

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Bucket
from app.services.access_keys import get_access_key_by_access_key_id, touch_access_key
from app.services.app_settings import EffectiveS3Settings, load_effective_s3_settings


AWS4_PREFIX = "AWS4"
SIGV4_ALGORITHM = "AWS4-HMAC-SHA256"
UNSIGNED_PAYLOAD = "UNSIGNED-PAYLOAD"


async def require_s3_signature(
    request: Request,
    db: Session = Depends(get_db),
    bucket_name: str | None = None,
) -> dict[str, str] | None:
    effective_settings = load_effective_s3_settings(db, request_base_url=str(request.base_url).rstrip("/"))
    if not effective_settings.s3_require_sigv4:
        return None

    if "X-Amz-Signature" in request.query_params:
        return await _validate_query_signature(request, db, effective_settings, bucket_name)

    authorization = request.headers.get("authorization")
    if not authorization:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing Authorization header.")

    return await _validate_header_signature(request, db, effective_settings, bucket_name, authorization)


def generate_presigned_get_url(
    *,
    effective_settings: EffectiveS3Settings,
    bucket_name: str,
    object_key: str,
    access_key_id: str,
    secret_access_key: str,
    region: str,
    expires_in: int,
    request_time: datetime | None = None,
) -> tuple[str, datetime]:
    timestamp = request_time or datetime.now(timezone.utc)
    amz_date = timestamp.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = timestamp.strftime("%Y%m%d")
    credential_scope = f"{date_stamp}/{region}/{effective_settings.s3_service_name}/aws4_request"

    base_url = effective_settings.public_base_url.rstrip("/")
    parsed_base = urlparse(base_url)
    bucket_segment = quote(bucket_name, safe="-_.~")
    object_segment = _quote_path_segments(object_key)
    base_path = parsed_base.path.rstrip("/")
    canonical_uri = f"{base_path}/{bucket_segment}/{object_segment}" if base_path else f"/{bucket_segment}/{object_segment}"
    host = parsed_base.netloc

    params = [
        ("X-Amz-Algorithm", SIGV4_ALGORITHM),
        ("X-Amz-Credential", f"{access_key_id}/{credential_scope}"),
        ("X-Amz-Date", amz_date),
        ("X-Amz-Expires", str(expires_in)),
        ("X-Amz-SignedHeaders", "host"),
    ]
    canonical_query_string = _canonical_query_string_from_pairs(params)
    canonical_request = "\n".join(
        [
            "GET",
            canonical_uri,
            canonical_query_string,
            f"host:{host}\n",
            "host",
            UNSIGNED_PAYLOAD,
        ]
    )
    string_to_sign = "\n".join(
        [
            SIGV4_ALGORITHM,
            amz_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    signing_key = _derive_signing_key(
        secret_key=secret_access_key,
        date_stamp=date_stamp,
        region=region,
        service=effective_settings.s3_service_name,
    )
    signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    final_query = f"{canonical_query_string}&X-Amz-Signature={signature}"
    url = urlunparse((parsed_base.scheme, parsed_base.netloc, canonical_uri, "", final_query, ""))
    return url, timestamp + timedelta(seconds=expires_in)


async def _validate_header_signature(
    request: Request,
    db: Session,
    effective_settings: EffectiveS3Settings,
    bucket_name: str | None,
    authorization: str,
) -> dict[str, str]:
    region = _resolve_region(bucket_name, db, effective_settings)
    parsed = _parse_authorization_header(authorization)

    if parsed["algorithm"] != SIGV4_ALGORITHM:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Unsupported authorization algorithm.")

    access_key_id, credential_date, credential_region, credential_service = _parse_credential_scope(
        parsed["credential"],
        expected_service=effective_settings.s3_service_name,
    )
    access_key = get_access_key_by_access_key_id(db, access_key_id)
    if access_key is None or not access_key.enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid access key.")

    if credential_region != region:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Bucket expects region '{region}', but request was signed for '{credential_region}'.",
            headers={"x-amz-bucket-region": region},
        )

    request_time = _parse_amz_date(request.headers.get("x-amz-date"))
    _validate_request_timestamp(request_time, effective_settings.s3_max_clock_skew_seconds)
    if credential_date != request_time.strftime("%Y%m%d"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Credential date does not match x-amz-date.")

    signed_headers = parsed["signed_headers"].split(";")
    if signed_headers != sorted(signed_headers):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="SignedHeaders must be sorted.")

    payload_hash = request.headers.get("x-amz-content-sha256")
    if not payload_hash:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing x-amz-content-sha256 header.")

    if payload_hash != UNSIGNED_PAYLOAD:
        body = await request.body()
        request.state.cached_body_bytes = body
        actual_payload_hash = hashlib.sha256(body).hexdigest()
        if actual_payload_hash != payload_hash:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Payload hash mismatch.")

    canonical_request = _build_canonical_request(
        request=request,
        signed_headers=signed_headers,
        payload_hash=payload_hash,
    )
    credential_scope = f"{credential_date}/{credential_region}/{credential_service}/aws4_request"
    _verify_signature(
        signature=parsed["signature"],
        credential_scope=credential_scope,
        canonical_request=canonical_request,
        amz_date=request_time.strftime("%Y%m%dT%H%M%SZ"),
        secret_access_key=access_key.secret_access_key,
        region=credential_region,
        service=credential_service,
    )

    request.state.s3_access_key_id = access_key_id
    request.state.s3_region = region
    touch_access_key(db, access_key)
    return {"access_key_id": access_key_id, "region": region}


async def _validate_query_signature(
    request: Request,
    db: Session,
    effective_settings: EffectiveS3Settings,
    bucket_name: str | None,
) -> dict[str, str]:
    region = _resolve_region(bucket_name, db, effective_settings)
    query = request.query_params

    if query.get("X-Amz-Algorithm") != SIGV4_ALGORITHM:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Unsupported presign algorithm.")

    credential = query.get("X-Amz-Credential")
    signature = query.get("X-Amz-Signature")
    amz_date = query.get("X-Amz-Date")
    expires = query.get("X-Amz-Expires")
    signed_headers_value = query.get("X-Amz-SignedHeaders")
    if not all([credential, signature, amz_date, expires, signed_headers_value]):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing presign parameters.")

    access_key_id, credential_date, credential_region, credential_service = _parse_credential_scope(
        credential,
        expected_service=effective_settings.s3_service_name,
    )
    access_key = get_access_key_by_access_key_id(db, access_key_id)
    if access_key is None or not access_key.enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid access key.")
    if credential_region != region:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Bucket expects region '{region}', but request was signed for '{credential_region}'.",
            headers={"x-amz-bucket-region": region},
        )

    request_time = _parse_amz_date(amz_date)
    if credential_date != request_time.strftime("%Y%m%d"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Credential date does not match x-amz-date.")

    try:
        expires_seconds = int(expires)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid X-Amz-Expires value.") from exc
    if expires_seconds < 1 or expires_seconds > 604800:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="X-Amz-Expires must be between 1 and 604800 seconds.")
    now = datetime.now(timezone.utc)
    if request_time - now > timedelta(seconds=effective_settings.s3_max_clock_skew_seconds):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Presigned URL timestamp is too far in the future.")
    if now > request_time + timedelta(seconds=expires_seconds):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Presigned URL has expired.")

    signed_headers = signed_headers_value.split(";")
    if signed_headers != sorted(signed_headers):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="SignedHeaders must be sorted.")

    canonical_request = _build_canonical_request(
        request=request,
        signed_headers=signed_headers,
        payload_hash=UNSIGNED_PAYLOAD,
        exclude_query_keys={"X-Amz-Signature"},
    )
    credential_scope = f"{credential_date}/{credential_region}/{credential_service}/aws4_request"
    _verify_signature(
        signature=signature,
        credential_scope=credential_scope,
        canonical_request=canonical_request,
        amz_date=request_time.strftime("%Y%m%dT%H%M%SZ"),
        secret_access_key=access_key.secret_access_key,
        region=credential_region,
        service=credential_service,
    )

    request.state.s3_access_key_id = access_key_id
    request.state.s3_region = region
    touch_access_key(db, access_key)
    return {"access_key_id": access_key_id, "region": region}


def _resolve_region(bucket_name: str | None, db: Session, effective_settings: EffectiveS3Settings) -> str:
    if not bucket_name:
        return effective_settings.s3_default_region

    bucket = db.execute(select(Bucket).where(Bucket.name == bucket_name)).scalar_one_or_none()
    if bucket is None:
        return effective_settings.s3_default_region
    return bucket.region or effective_settings.s3_default_region


def _parse_authorization_header(value: str) -> dict[str, str]:
    try:
        algorithm, params_string = value.split(" ", 1)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Malformed Authorization header.") from exc

    params: dict[str, str] = {}
    for part in params_string.split(","):
        if "=" not in part:
            continue
        key, raw_value = part.strip().split("=", 1)
        params[key.lower()] = raw_value

    required_keys = {"credential", "signedheaders", "signature"}
    if not required_keys.issubset(params):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Authorization header is missing required fields.")

    return {
        "algorithm": algorithm,
        "credential": params["credential"],
        "signed_headers": params["signedheaders"],
        "signature": params["signature"],
    }


def _parse_credential_scope(credential: str, expected_service: str) -> tuple[str, str, str, str]:
    credential_parts = credential.split("/")
    if len(credential_parts) != 5:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Malformed credential scope.")

    access_key_id, credential_date, credential_region, credential_service, terminal = credential_parts
    if credential_service != expected_service or terminal != "aws4_request":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid credential scope.")
    return access_key_id, credential_date, credential_region, credential_service


def _parse_amz_date(value: str | None) -> datetime:
    if not value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing x-amz-date value.")
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid x-amz-date value.") from exc


def _validate_request_timestamp(request_time: datetime, max_skew_seconds: int) -> None:
    now = datetime.now(timezone.utc)
    skew_seconds = abs((now - request_time).total_seconds())
    if skew_seconds > max_skew_seconds:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Request timestamp is outside the allowed skew.")


def _build_canonical_request(
    request: Request,
    signed_headers: Iterable[str],
    payload_hash: str,
    exclude_query_keys: set[str] | None = None,
) -> str:
    canonical_uri = _canonical_uri(request)
    canonical_query_string = _canonical_query_string(request, exclude_query_keys=exclude_query_keys)
    canonical_headers = _canonical_headers(request, signed_headers)
    signed_headers_str = ";".join(signed_headers)

    return "\n".join(
        [
            request.method.upper(),
            canonical_uri,
            canonical_query_string,
            canonical_headers,
            signed_headers_str,
            payload_hash,
        ]
    )


def _canonical_uri(request: Request) -> str:
    raw_path = request.scope.get("raw_path", b"/")
    raw_value = raw_path.decode("utf-8") if isinstance(raw_path, (bytes, bytearray)) else str(raw_path)
    decoded_path = unquote(raw_value or "/")
    return quote(decoded_path or "/", safe="/-_.~")


def _canonical_query_string(request: Request, exclude_query_keys: set[str] | None = None) -> str:
    raw_query = request.scope.get("query_string", b"")
    query_text = raw_query.decode("utf-8") if isinstance(raw_query, (bytes, bytearray)) else str(raw_query)
    pairs = parse_qsl(query_text, keep_blank_values=True)
    if exclude_query_keys:
        pairs = [(key, value) for key, value in pairs if key not in exclude_query_keys]
    return _canonical_query_string_from_pairs(pairs)


def _canonical_query_string_from_pairs(pairs: Iterable[tuple[str, str]]) -> str:
    encoded_pairs = [(quote(key, safe="-_.~"), quote(value, safe="-_.~")) for key, value in pairs]
    encoded_pairs.sort()
    return "&".join(f"{key}={value}" for key, value in encoded_pairs)


def _canonical_headers(request: Request, signed_headers: Iterable[str]) -> str:
    lines: list[str] = []
    for header_name in signed_headers:
        value = request.headers.get(header_name)
        if value is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Signed header '{header_name}' is missing from the request.",
            )
        normalized_value = " ".join(value.strip().split())
        lines.append(f"{header_name}:{normalized_value}")
    return "\n".join(lines) + "\n"


def _verify_signature(
    *,
    signature: str,
    credential_scope: str,
    canonical_request: str,
    amz_date: str,
    secret_access_key: str,
    region: str,
    service: str,
) -> None:
    canonical_request_hash = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
    string_to_sign = "\n".join(
        [
            SIGV4_ALGORITHM,
            amz_date,
            credential_scope,
            canonical_request_hash,
        ]
    )
    date_stamp = credential_scope.split("/", 1)[0]
    signing_key = _derive_signing_key(
        secret_key=secret_access_key,
        date_stamp=date_stamp,
        region=region,
        service=service,
    )
    expected_signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_signature, signature):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Signature does not match.")


def _derive_signing_key(secret_key: str, date_stamp: str, region: str, service: str) -> bytes:
    key_date = _sign((AWS4_PREFIX + secret_key).encode("utf-8"), date_stamp)
    key_region = _sign(key_date, region)
    key_service = _sign(key_region, service)
    return _sign(key_service, "aws4_request")


def _sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _quote_path_segments(path: str) -> str:
    return "/".join(quote(part, safe="-_.~") for part in path.split("/"))
