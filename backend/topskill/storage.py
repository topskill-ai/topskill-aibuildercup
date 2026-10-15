"""Blob storage (SPEC §14.2): Cloud Storage in prod, a local directory in dev/test.

Path conventions: ``skills/{skill_id}/{relpath}``, ``knowledge/{agent_id}/{file_id}/{filename}``,
``avatars/{avatar_id}.webp``.
"""

from __future__ import annotations

import asyncio
import shutil
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Protocol, runtime_checkable

from google.api_core import exceptions as gexc

from topskill.config import get_settings
from topskill.errors import ApiError, Codes


@runtime_checkable
class BlobStore(Protocol):
    async def put(self, path: str, data: bytes, content_type: str) -> None: ...
    async def get(self, path: str) -> bytes: ...  # raises ApiError(404, "not_found")
    async def delete(self, path: str) -> None: ...  # missing is fine
    async def delete_prefix(self, prefix: str) -> None: ...


def _not_found() -> ApiError:
    return ApiError(404, Codes.NOT_FOUND, "File not found.")


def normalize_path(path: str, *, allow_prefix: bool = False) -> str:
    """Validate a relative blob path: no absolute paths, ``..``, backslashes or empty segments."""
    if not isinstance(path, str) or not path or "\\" in path or "\x00" in path or path.startswith("/"):
        raise ValueError(f"invalid blob path: {path!r}")
    trimmed = path[:-1] if allow_prefix and path.endswith("/") else path
    parts = PurePosixPath(trimmed).parts
    if not parts or any(part in ("", ".", "..") for part in trimmed.split("/")):
        raise ValueError(f"invalid blob path: {path!r}")
    return path


class LocalBlobStore:
    """Files under a root directory (``LOCAL_STORAGE_DIR``)."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _file(self, path: str) -> Path:
        normalize_path(path)
        target = (self.root / path).resolve()
        if self.root not in target.parents:
            raise ValueError(f"invalid blob path: {path!r}")
        return target

    async def put(self, path: str, data: bytes, content_type: str) -> None:
        target = self._file(path)

        def _write() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(target)

        await asyncio.to_thread(_write)

    async def get(self, path: str) -> bytes:
        target = self._file(path)
        try:
            return await asyncio.to_thread(target.read_bytes)
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError) as exc:
            raise _not_found() from exc

    async def delete(self, path: str) -> None:
        target = self._file(path)
        await asyncio.to_thread(target.unlink, True)

    async def delete_prefix(self, prefix: str) -> None:
        normalize_path(prefix, allow_prefix=True)
        base = (self.root / prefix.rstrip("/")).resolve()
        if self.root not in base.parents:
            raise ValueError(f"invalid blob prefix: {prefix!r}")

        def _delete() -> None:
            if prefix.endswith("/"):
                if base.is_dir():
                    shutil.rmtree(base, ignore_errors=True)
                return
            parent = base.parent
            if not parent.is_dir():
                return
            for child in parent.iterdir():
                if child.name.startswith(base.name):
                    if child.is_dir():
                        shutil.rmtree(child, ignore_errors=True)
                    else:
                        child.unlink(missing_ok=True)

        await asyncio.to_thread(_delete)


class GcsBlobStore:
    """Objects in one Cloud Storage bucket (ADC)."""

    def __init__(self, bucket: str, project: str | None = None) -> None:
        from google.cloud import storage

        self._client = storage.Client(project=project)
        self._bucket = self._client.bucket(bucket)

    async def put(self, path: str, data: bytes, content_type: str) -> None:
        normalize_path(path)
        blob = self._bucket.blob(path)
        await asyncio.to_thread(blob.upload_from_string, data, content_type=content_type)

    async def get(self, path: str) -> bytes:
        normalize_path(path)
        blob = self._bucket.blob(path)
        try:
            return await asyncio.to_thread(blob.download_as_bytes)
        except gexc.NotFound as exc:
            raise _not_found() from exc

    async def delete(self, path: str) -> None:
        normalize_path(path)
        blob = self._bucket.blob(path)
        try:
            await asyncio.to_thread(blob.delete)
        except gexc.NotFound:
            return

    async def delete_prefix(self, prefix: str) -> None:
        normalize_path(prefix, allow_prefix=True)

        def _delete() -> None:
            for blob in self._client.list_blobs(self._bucket, prefix=prefix):
                try:
                    blob.delete()
                except gexc.NotFound:
                    continue

        await asyncio.to_thread(_delete)


@lru_cache(maxsize=4)
def _store_for(backend: str, location: str, project: str) -> BlobStore:
    if backend == "local":
        return LocalBlobStore(Path(location))
    return GcsBlobStore(location, project=project)


def get_blob_store() -> BlobStore:
    settings = get_settings()
    if settings.storage_backend == "local":
        return _store_for("local", str(settings.local_storage_path), settings.google_cloud_project)
    return _store_for("gcs", settings.gcs_bucket, settings.google_cloud_project)
