from __future__ import annotations

from tempfile import SpooledTemporaryFile
from typing import BinaryIO, Iterator

from fastapi import Request


async def request_to_spooled_file(request: Request, spool_size: int = 5 * 1024 * 1024) -> tuple[BinaryIO, int]:
    temp_file = SpooledTemporaryFile(max_size=spool_size, mode="w+b")

    cached_body = getattr(request.state, "cached_body_bytes", None)
    if cached_body is not None:
        temp_file.write(cached_body)
        temp_file.seek(0)
        return temp_file, len(cached_body)

    total_bytes = 0

    async for chunk in request.stream():
        if not chunk:
            continue
        temp_file.write(chunk)
        total_bytes += len(chunk)

    temp_file.seek(0)
    return temp_file, total_bytes


def iter_file_chunks(file_obj: BinaryIO, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
    try:
        while True:
            chunk = file_obj.read(chunk_size)
            if not chunk:
                break
            yield chunk
    finally:
        file_obj.close()
