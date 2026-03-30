from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from ftplib import FTP, error_perm
from pathlib import PurePosixPath
from tempfile import SpooledTemporaryFile
from typing import BinaryIO, Iterator

from app.pathing import normalize_ftp_dir


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class FTPFileInfo:
    ftp_path: str
    size: int | None = None
    last_modified: datetime | None = None


class FTPStorage:
    def __init__(self, host: str, port: int, username: str, password: str, timeout: int = 30) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.timeout = timeout

    def connect(self) -> FTP:
        ftp = FTP()
        ftp.connect(self.host, self.port, timeout=self.timeout)
        ftp.login(self.username, self.password)
        ftp.encoding = "utf-8"
        return ftp

    @contextmanager
    def _connection(self) -> Iterator[FTP]:
        ftp = self.connect()
        try:
            yield ftp
        finally:
            try:
                ftp.quit()
            except Exception:
                ftp.close()

    def list_objects(self, base_path: str) -> list[FTPFileInfo]:
        normalized_base = normalize_ftp_dir(base_path)
        with self._connection() as ftp:
            objects = self._list_recursive(ftp, normalized_base)
        return sorted(objects, key=lambda item: item.ftp_path)

    def upload_file(self, path: str, file_stream: BinaryIO) -> None:
        normalized_path = normalize_ftp_dir(path)
        parent_dir = str(PurePosixPath(normalized_path).parent)
        with self._connection() as ftp:
            self._ensure_remote_dirs(ftp, parent_dir)
            file_stream.seek(0)
            ftp.storbinary(f"STOR {normalized_path}", file_stream)

    def download_file(self, path: str) -> BinaryIO:
        normalized_path = normalize_ftp_dir(path)
        temp_file = SpooledTemporaryFile(max_size=5 * 1024 * 1024, mode="w+b")

        with self._connection() as ftp:
            ftp.retrbinary(f"RETR {normalized_path}", temp_file.write)

        temp_file.seek(0)
        return temp_file

    def delete_file(self, path: str) -> None:
        normalized_path = normalize_ftp_dir(path)
        with self._connection() as ftp:
            ftp.delete(normalized_path)

    def file_exists(self, path: str) -> bool:
        normalized_path = normalize_ftp_dir(path)
        with self._connection() as ftp:
            try:
                ftp.size(normalized_path)
                return True
            except error_perm:
                return False

    def get_file_info(self, path: str) -> FTPFileInfo:
        normalized_path = normalize_ftp_dir(path)
        with self._connection() as ftp:
            return self._get_file_info(ftp, normalized_path)

    def _list_recursive(self, ftp: FTP, current_path: str) -> list[FTPFileInfo]:
        try:
            entries = list(ftp.mlsd(current_path))
            return self._list_recursive_mlsd(ftp, current_path, entries)
        except (AttributeError, error_perm):
            logger.debug("MLSD not available for %s, falling back to NLST", current_path)
            return self._list_recursive_nlst(ftp, current_path)

    def _list_recursive_mlsd(
        self,
        ftp: FTP,
        current_path: str,
        entries: list[tuple[str, dict[str, str]]],
    ) -> list[FTPFileInfo]:
        files: list[FTPFileInfo] = []

        for name, facts in entries:
            if name in (".", ".."):
                continue

            item_path = normalize_ftp_dir(f"{current_path}/{name}")
            item_type = facts.get("type", "file")

            if item_type == "dir":
                files.extend(self._list_recursive(ftp, item_path))
                continue

            if item_type != "file":
                continue

            files.append(
                FTPFileInfo(
                    ftp_path=item_path,
                    size=self._parse_size(facts.get("size")),
                    last_modified=self._parse_modify_time(facts.get("modify")),
                )
            )

        return files

    def _list_recursive_nlst(self, ftp: FTP, current_path: str) -> list[FTPFileInfo]:
        files: list[FTPFileInfo] = []
        try:
            names = ftp.nlst(current_path)
        except error_perm as exc:
            if str(exc).startswith("550"):
                return files
            raise

        for raw_name in names:
            if raw_name in (".", "..", current_path):
                continue

            if raw_name.startswith("/"):
                item_path = normalize_ftp_dir(raw_name)
            else:
                item_path = normalize_ftp_dir(f"{current_path}/{raw_name}")

            if self._is_directory(ftp, item_path):
                files.extend(self._list_recursive_nlst(ftp, item_path))
            else:
                files.append(self._get_file_info(ftp, item_path))

        return files

    def _is_directory(self, ftp: FTP, path: str) -> bool:
        original_dir = ftp.pwd()
        try:
            ftp.cwd(path)
            ftp.cwd(original_dir)
            return True
        except error_perm:
            return False

    def _ensure_remote_dirs(self, ftp: FTP, directory: str) -> None:
        normalized_directory = normalize_ftp_dir(directory)
        if normalized_directory == "/":
            return

        current_path = ""
        for part in PurePosixPath(normalized_directory).parts:
            if part == "/":
                continue
            current_path = f"{current_path}/{part}"
            try:
                ftp.mkd(current_path)
            except error_perm as exc:
                if not str(exc).startswith("550"):
                    raise

    def _get_file_info(self, ftp: FTP, path: str) -> FTPFileInfo:
        size: int | None = None
        last_modified: datetime | None = None

        try:
            size = ftp.size(path)
        except error_perm:
            size = None

        try:
            response = ftp.sendcmd(f"MDTM {path}")
            if response.startswith("213 "):
                last_modified = self._parse_modify_time(response.split(" ", 1)[1].strip())
        except error_perm:
            last_modified = None

        return FTPFileInfo(ftp_path=path, size=size, last_modified=last_modified)

    @staticmethod
    def _parse_modify_time(raw_value: str | None) -> datetime | None:
        if not raw_value:
            return None
        try:
            return datetime.strptime(raw_value, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    @staticmethod
    def _parse_size(raw_value: str | None) -> int | None:
        if raw_value is None:
            return None
        try:
            return int(raw_value)
        except ValueError:
            return None
