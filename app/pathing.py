from __future__ import annotations

from pathlib import PurePosixPath


class InvalidPathError(ValueError):
    """Raised when an object key or FTP path is unsafe."""


def _sanitize_parts(raw_path: str, *, allow_empty: bool) -> list[str]:
    cleaned = raw_path.replace("\\", "/").strip()
    if not cleaned:
        return [] if allow_empty else _raise_empty()

    parts: list[str] = []
    for part in cleaned.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise InvalidPathError("Path traversal is not allowed.")
        parts.append(part)

    if not parts and not allow_empty:
        raise InvalidPathError("Path cannot be empty.")
    return parts


def _raise_empty() -> list[str]:
    raise InvalidPathError("Path cannot be empty.")


def normalize_object_key(value: str) -> str:
    parts = _sanitize_parts(value, allow_empty=False)
    return "/".join(parts)


def normalize_prefix(value: str | None) -> str:
    if value is None:
        return ""
    parts = _sanitize_parts(value, allow_empty=True)
    return "/".join(parts)


def normalize_ftp_dir(value: str) -> str:
    parts = _sanitize_parts(value, allow_empty=True)
    return "/" + "/".join(parts) if parts else "/"


def join_ftp_path(base_dir: str, object_key: str) -> str:
    base = normalize_ftp_dir(base_dir)
    key = normalize_object_key(object_key)
    if base == "/":
        return f"/{key}"
    return f"{base}/{key}"


def ftp_path_to_object_key(base_dir: str, ftp_path: str) -> str:
    normalized_base = normalize_ftp_dir(base_dir)
    normalized_path = normalize_ftp_dir(ftp_path)

    if normalized_base == "/":
        relative = normalized_path.lstrip("/")
    elif normalized_path == normalized_base:
        relative = ""
    elif normalized_path.startswith(f"{normalized_base}/"):
        relative = normalized_path[len(normalized_base) + 1 :]
    else:
        raise InvalidPathError("FTP path is outside the bucket base directory.")

    return normalize_object_key(relative)


def basename(path: str) -> str:
    return PurePosixPath(path).name
