"""Secure, bounded disk staging for streamed ingestion request bodies.

The API layer can pass ``request.stream()`` directly to :func:`stage_request_stream`.
The returned object exposes a verified private path for parsers/media and a bounded
reader for object-store persistence. Source filenames and locators are never used
in temporary path names or exception messages.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shutil
import stat
import tempfile
import uuid
from collections.abc import AsyncIterable, Iterator
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Protocol

DEFAULT_STAGE_WRITE_CHUNK_BYTES = 1024 * 1024
_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


class _HashDigest(Protocol):
    def update(self, data: bytes | bytearray | memoryview) -> None: ...


class UploadStagingError(RuntimeError):
    """Fail-closed request staging error with a safe external code."""

    def __init__(self, message: str, *, code: str = "DOCUMENT_UNREADABLE") -> None:
        super().__init__(message)
        self.code = code


class UploadSizeExceededError(UploadStagingError):
    """The streamed body crossed its configured admission bound."""

    def __init__(self, *, max_bytes: int, observed_bytes: int) -> None:
        super().__init__(
            "Upload exceeds the configured ingestion size limit",
            code="INGEST_ADMISSION_MAX_SIZE_EXCEEDED",
        )
        self.max_bytes = max_bytes
        self.observed_bytes = observed_bytes


class UploadIntegrityError(UploadStagingError):
    """The completed staged body does not match its declared identity."""


@dataclass(slots=True, repr=False)
class StagedUpload:
    """Trusted private spool produced by :func:`stage_request_stream`.

    Instances own their private staging directory. Call :meth:`cleanup` after
    every terminal path, or use the synchronous/asynchronous context-manager
    protocols. The custom representation deliberately omits the absolute path.
    """

    path: Path = field(repr=False)
    size_bytes: int
    sha256: str
    _directory: Path = field(repr=False)
    _stat_signature: tuple[int, int, int, int] = field(repr=False)
    _cleaned: bool = field(default=False, init=False, repr=False)

    def __repr__(self) -> str:
        return (
            "StagedUpload("
            f"size_bytes={self.size_bytes}, sha256={self.sha256!r}, cleaned={self._cleaned}"
            ")"
        )

    def verified_path(self) -> Path:
        """Return the private path after detecting replacement or mutation."""
        if self._cleaned:
            raise UploadStagingError("Staged upload has already been cleaned up")
        try:
            current = self.path.lstat()
        except OSError as exc:
            raise UploadStagingError("Staged upload is unavailable") from exc
        if not stat.S_ISREG(current.st_mode) or self.path.is_symlink():
            raise UploadStagingError("Staged upload is not a trusted regular file")
        signature = (
            current.st_dev,
            current.st_ino,
            current.st_size,
            current.st_mtime_ns,
        )
        if signature != self._stat_signature:
            raise UploadIntegrityError(
                "Staged upload changed after validation",
                code="SHA256_MISMATCH",
            )
        return self.path

    def open_reader(self) -> BinaryIO:
        """Open a bounded-reader-compatible handle to the verified spool."""
        path = self.verified_path()
        try:
            return path.open("rb")
        except OSError as exc:
            raise UploadStagingError("Staged upload cannot be opened") from exc

    def iter_chunks(
        self,
        chunk_size_bytes: int = DEFAULT_STAGE_WRITE_CHUNK_BYTES,
    ) -> Iterator[bytes]:
        """Yield the verified body without any whole-file read."""
        if (
            isinstance(chunk_size_bytes, bool)
            or not isinstance(chunk_size_bytes, int)
            or chunk_size_bytes <= 0
        ):
            raise ValueError("chunk_size_bytes must be a positive integer")
        with self.open_reader() as reader:
            while chunk := reader.read(chunk_size_bytes):
                yield chunk

    def verify_integrity(self) -> None:
        """Re-hash the spool in bounded chunks before a path-only handoff."""
        digest = hashlib.sha256()
        size_bytes = 0
        for chunk in self.iter_chunks():
            digest.update(chunk)
            size_bytes += len(chunk)
        if size_bytes != self.size_bytes or digest.hexdigest() != self.sha256:
            raise UploadIntegrityError(
                "Staged upload failed its integrity recheck",
                code="SHA256_MISMATCH",
            )

    def cleanup(self) -> None:
        """Remove the staged body and its private directory, idempotently."""
        if self._cleaned:
            return
        try:
            if self.path.is_symlink():
                self.path.unlink()
            elif self.path.exists():
                self.path.chmod(0o600)
                self.path.unlink()
            self._directory.rmdir()
        except OSError as exc:
            raise UploadStagingError(
                "Failed to clean up staged upload",
                code="INGEST_STAGING_CLEANUP_FAILED",
            ) from exc
        self._cleaned = True

    close = cleanup

    def __enter__(self) -> StagedUpload:
        self.verified_path()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        self.cleanup()

    async def __aenter__(self) -> StagedUpload:
        self.verified_path()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        self.cleanup()


def _normalize_expectations(
    *,
    max_bytes: int,
    expected_sha256: str | None,
    expected_size_bytes: int | None,
    write_chunk_size_bytes: int,
) -> tuple[str | None, int | None]:
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("max_bytes must be a positive integer")
    if (
        isinstance(write_chunk_size_bytes, bool)
        or not isinstance(write_chunk_size_bytes, int)
        or write_chunk_size_bytes <= 0
    ):
        raise ValueError("write_chunk_size_bytes must be a positive integer")
    normalized_sha256: str | None = None
    if expected_sha256 is not None:
        if not isinstance(expected_sha256, str) or not _SHA256_PATTERN.fullmatch(expected_sha256):
            raise UploadIntegrityError(
                "Upload SHA-256 declaration is invalid",
                code="SHA256_MISMATCH",
            )
        normalized_sha256 = expected_sha256.lower()
    if expected_size_bytes is not None and (
        isinstance(expected_size_bytes, bool)
        or not isinstance(expected_size_bytes, int)
        or expected_size_bytes < 0
    ):
        raise ValueError("expected_size_bytes must be a non-negative integer")
    if expected_size_bytes is not None and expected_size_bytes > max_bytes:
        raise UploadSizeExceededError(
            max_bytes=max_bytes,
            observed_bytes=expected_size_bytes,
        )
    return normalized_sha256, expected_size_bytes


def _create_private_stage(staging_root: str | os.PathLike[str] | None) -> tuple[Path, Path]:
    directory: Path | None = None
    try:
        root: Path | None = None
        if staging_root is not None:
            requested_root = Path(staging_root)
            requested_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            if requested_root.is_symlink() or not requested_root.is_dir():
                raise OSError("staging root is not a trusted directory")
            root = requested_root.resolve()
        directory = Path(
            tempfile.mkdtemp(
                prefix="idis-upload-",
                dir=str(root) if root is not None else None,
            )
        ).resolve()
        directory.chmod(0o700)
        path = directory / f"body-{uuid.uuid4().hex}.bin"
        return directory, path
    except OSError as exc:
        if directory is not None:
            shutil.rmtree(directory, ignore_errors=True)
        raise UploadStagingError("Unable to initialize secure upload staging") from exc


def _write_bounded(
    output: BinaryIO,
    chunk: memoryview,
    *,
    digest: _HashDigest,
    write_chunk_size_bytes: int,
) -> None:
    offset = 0
    while offset < chunk.nbytes:
        end = min(offset + write_chunk_size_bytes, chunk.nbytes)
        part = chunk[offset:end]
        written = output.write(part)
        if written is None or written <= 0:
            raise OSError("staging write made no progress")
        digest.update(part[:written])
        offset += written


def _cleanup_failed_stage(directory: Path, path: Path) -> None:
    with suppress(OSError):
        if path.exists() and not path.is_symlink():
            path.chmod(0o600)
    shutil.rmtree(directory, ignore_errors=True)


async def stage_request_stream(
    chunks: AsyncIterable[bytes],
    *,
    max_bytes: int,
    expected_sha256: str | None = None,
    expected_size_bytes: int | None = None,
    staging_root: str | os.PathLike[str] | None = None,
    write_chunk_size_bytes: int = DEFAULT_STAGE_WRITE_CHUNK_BYTES,
) -> StagedUpload:
    """Consume an ASGI request stream into a secure, bounded disk spool.

    The function stops on the first chunk crossing ``max_bytes`` and removes all
    staged content on oversize, disconnect, cancellation, hash/size mismatch, or
    filesystem failure. The caller owns the returned :class:`StagedUpload`.
    """
    normalized_sha256, normalized_size = _normalize_expectations(
        max_bytes=max_bytes,
        expected_sha256=expected_sha256,
        expected_size_bytes=expected_size_bytes,
        write_chunk_size_bytes=write_chunk_size_bytes,
    )
    directory, path = _create_private_stage(staging_root)
    digest = hashlib.sha256()
    size_bytes = 0
    output: BinaryIO | None = None
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            output = os.fdopen(descriptor, "wb")
        except BaseException:
            with suppress(OSError):
                os.close(descriptor)
            raise
        async for raw_chunk in chunks:
            if not isinstance(raw_chunk, (bytes, bytearray, memoryview)):
                raise UploadStagingError("Upload stream returned a non-binary chunk")
            chunk = memoryview(raw_chunk)
            if chunk.nbytes == 0:
                continue
            next_size = size_bytes + chunk.nbytes
            if next_size > max_bytes:
                raise UploadSizeExceededError(
                    max_bytes=max_bytes,
                    observed_bytes=next_size,
                )
            if normalized_size is not None and next_size > normalized_size:
                raise UploadIntegrityError(
                    "Upload exceeded its declared size",
                    code="DATA_ROOM_DURABLE_HANDOFF_INCOMPLETE",
                )
            _write_bounded(
                output,
                chunk,
                digest=digest,
                write_chunk_size_bytes=write_chunk_size_bytes,
            )
            size_bytes = next_size

        if normalized_size is not None and size_bytes != normalized_size:
            raise UploadIntegrityError(
                "Upload size does not match its declaration",
                code="DATA_ROOM_DURABLE_HANDOFF_INCOMPLETE",
            )
        actual_sha256 = digest.hexdigest()
        if normalized_sha256 is not None and actual_sha256 != normalized_sha256:
            raise UploadIntegrityError(
                "Upload SHA-256 does not match its declaration",
                code="SHA256_MISMATCH",
            )
        output.flush()
        os.fsync(output.fileno())
        output.close()
        output = None
        path.chmod(0o400)
        staged_stat = path.lstat()
        signature = (
            staged_stat.st_dev,
            staged_stat.st_ino,
            staged_stat.st_size,
            staged_stat.st_mtime_ns,
        )
        return StagedUpload(
            path=path,
            size_bytes=size_bytes,
            sha256=actual_sha256,
            _directory=directory,
            _stat_signature=signature,
        )
    except asyncio.CancelledError:
        if output is not None:
            with suppress(OSError):
                output.close()
            output = None
        _cleanup_failed_stage(directory, path)
        raise
    except UploadStagingError:
        if output is not None:
            with suppress(OSError):
                output.close()
            output = None
        _cleanup_failed_stage(directory, path)
        raise
    except Exception as exc:
        if output is not None:
            with suppress(OSError):
                output.close()
            output = None
        _cleanup_failed_stage(directory, path)
        raise UploadStagingError("Failed to stage upload request body") from exc
    finally:
        if output is not None:
            with suppress(OSError):
                output.close()


__all__ = [
    "DEFAULT_STAGE_WRITE_CHUNK_BYTES",
    "StagedUpload",
    "UploadIntegrityError",
    "UploadSizeExceededError",
    "UploadStagingError",
    "stage_request_stream",
]
