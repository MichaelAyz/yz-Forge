# TODO Person 1
# save_blob(file_bytes) -> sha256_hex
# get_blob(sha256_hex) -> file_bytes or path
# blob_exists(sha256_hex) -> bool

"""
storage.py — Content-addressable blob storage for the Forge registry.

Blobs are stored on disk under:
    <storage_path>/blobs/<sha256_hex>

Files are named by their SHA-256 hash.
"""

import hashlib
import os
from pathlib import Path


_storage_path: str = ""


def init_storage(storage_path: str) -> None:
    """Called by main.py on startup to set the storage path and ensure the
    blobs directory exists.

    Args:
        storage_path: Root directory for blob storage, e.g. ``./data/blobs``.
    """
    global _storage_path
    _storage_path = storage_path
    _blobs_dir().mkdir(parents=True, exist_ok=True)



def _blobs_dir() -> Path:
    if not _storage_path:
        raise RuntimeError("storage not initialised — call init_storage() first")
    return Path(_storage_path) / "blobs"


def _blob_path(sha256_hex: str) -> Path:
    """Return the full path for a blob given its SHA-256 hex digest."""
    return _blobs_dir() / sha256_hex



def save_blob(file_bytes: bytes) -> str:
    """Write bytes to disk under their SHA-256 hash and return the hash.

    If a blob with the same hash already exists the write is skipped — the
    content is identical so there is nothing to do.

    Args:
        file_bytes: Raw bytes of the artifact file.

    Returns:
        The SHA-256 hex digest of the content.
    """
    sha256_hex = hashlib.sha256(file_bytes).hexdigest()
    path = _blob_path(sha256_hex)

    if not path.exists():
        path.write_bytes(file_bytes)

    return sha256_hex


def get_blob(sha256_hex: str) -> Path:
    """Return the filesystem path of a blob.

    Returning a Path rather than the raw bytes avoids loading large files
    (e.g. 50 MB artifacts) into memory. The caller can stream the file
    directly using FastAPI's ``FileResponse`` or an equivalent.

    Args:
        sha256_hex: The SHA-256 hex digest identifying the blob.

    Returns:
        A ``pathlib.Path`` pointing to the blob on disk.

    Raises:
        FileNotFoundError: if no blob with that hash exists.
    """
    path = _blob_path(sha256_hex)
    if not path.exists():
        raise FileNotFoundError(
            f"Blob not found: {sha256_hex}. "
            "The artifact may not have been uploaded yet."
        )
    return path


def blob_exists(sha256_hex: str) -> bool:
    """Return True if a blob with the given SHA-256 hash exists on disk.

    Args:
        sha256_hex: The SHA-256 hex digest to check.
    """
    return _blob_path(sha256_hex).exists()


def verify_checksum(file_bytes: bytes, expected_sha256: str) -> bool:
    """Recompute the SHA-256 of received bytes and compare against expected.

    Used at upload time to validate the client-declared checksum, and at
    pull time to verify integrity before handing the artifact to a build job.

    Args:
        file_bytes:      The received file bytes.
        expected_sha256: The hex digest the client or lockfile declared.

    Returns:
        True if the computed hash matches expected, False otherwise.
    """
    actual = hashlib.sha256(file_bytes).hexdigest()
    return actual == expected_sha256
