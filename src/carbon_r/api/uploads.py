"""Where an uploaded document goes, and what is refused at the door.

Three rules, each of which exists because of a specific failure:

1. **The stored name is generated, never the client's.** A filename is attacker-chosen
   input; `../../.env` as a name is the oldest trick there is. The client's filename is
   kept as a label and never touches the filesystem.
2. **The extension is an allowlist.** `extract_from_document` rasterises with `pdftoppm`
   and sends page images; anything else is not a document this app can read, and
   refusing it at upload gives a clear message instead of a confusing tool error later.
3. **There is a size cap.** The bytes are read into memory to be hashed and written, and
   an unbounded upload is an unbounded allocation.

Uploads land in `data/uploads/`, which is git-ignored. An uploaded bill is exactly the
kind of document CLAUDE.md says never enters the repo.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from carbon_r.agent.session import Session, StoredDocument
from carbon_r.config import PROJECT_ROOT

UPLOAD_DIR = PROJECT_ROOT / "data" / "uploads"

ALLOWED_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # a bill is ~100 KB; 10 MB is a generous scan

_CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


class UploadRejected(ValueError):
    """The upload was refused. The message is safe to return to the caller."""


def _suffix_of(filename: str) -> str:
    """The extension, taken from the NAME ONLY - never used as a path component."""
    return Path(filename or "").suffix.lower()


def save_upload(
    session: Session, *, filename: str, data: bytes, content_type: str | None = None
) -> StoredDocument:
    """Validate and store one upload, registering it with the session."""
    if not data:
        raise UploadRejected("The uploaded file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadRejected(
            f"That file is {len(data) / 1_048_576:.1f} MB; the limit is "
            f"{MAX_UPLOAD_BYTES // 1_048_576} MB."
        )

    suffix = _suffix_of(filename)
    if suffix not in ALLOWED_SUFFIXES:
        allowed = ", ".join(sorted(ALLOWED_SUFFIXES))
        raise UploadRejected(
            f"{filename!r} is not a document this app can read. Allowed types: {allowed}."
        )

    document_id = f"doc_{uuid.uuid4().hex[:12]}"
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    # Generated name + validated suffix: the path cannot be influenced by the client.
    path = UPLOAD_DIR / f"{document_id}{suffix}"
    path.write_bytes(data)

    document = StoredDocument(
        document_id=document_id,
        filename=Path(filename).name,
        content_type=content_type or _CONTENT_TYPES[suffix],
        size_bytes=len(data),
        path=path,
    )
    session.add_document(document)
    return document
