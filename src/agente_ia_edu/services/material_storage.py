"""PHASE 26 - Authorial Material Ingestion Engine: minimal file storage.

No object-storage (S3/GCS) abstraction exists anywhere in this codebase
(audited: ``ingestion_documents.storage_uri`` simply holds the local
filesystem path it was given - it never copies the file anywhere). Per spec
s19 ("se não existir, criar a menor abstração necessária"), this is exactly
that: a managed local directory the original file is COPIED into (never
moved/renamed/mutated in place), addressed by content hash so re-ingesting
the same bytes never creates a second copy. Trivially swappable for a real
object-storage backend later (same return shape) - documented as a limitation.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

_DEFAULT_ROOT = Path("var") / "material_storage"


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class MaterialStorage:
    """Content-addressed local storage. Read-only against the source file -
    it is opened for reading and copied; never opened for writing."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or _DEFAULT_ROOT

    def managed_path(self, document_hash: str, filename: str) -> Path:
        return self.root / document_hash[:2] / document_hash / filename

    def store(self, source_path: Path, *, document_hash: str | None = None) -> tuple[Path, str]:
        """Copy ``source_path`` into managed storage (idempotent - a second
        call with the same hash is a no-op) and return (managed_path, hash).
        The source file is never modified, moved, or deleted."""
        digest = document_hash or file_sha256(source_path)
        dest = self.managed_path(digest, source_path.name)
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, dest)  # copy2: never touches the source
        return dest, digest
