"""Durable, content-addressed storage for statement files.

Statement files are the one thing in this application that can be genuinely
irreplaceable. A parsed ledger can always be rebuilt by re-reading the files;
the files themselves cannot be rebuilt from anything. A statement pulled from
Gmail could in principle be fetched again, but one the user uploaded by hand -
an Amex PDF, a statement from a bank with no email delivery, a document they
downloaded once and deleted - exists nowhere else.

They used to be written to `data/uploads/<run_id>/<name>`, and the "start over"
button deleted every one of those directories outright. Clearing a derived
ledger destroyed the only copy of its own source.

Files now live at `data/statements/<user>/<first2>/<sha256><ext>`:

  - Under the owner, because these are somebody's bank statements and the app
    now serves more than one person. Content addressing alone would have two
    users sharing a single copy of an identical file - convenient, and exactly
    the kind of convenience that turns into one account's deletion removing
    another's only copy.
  - Addressed by content, so re-uploading the same statement resolves to the
    same path instead of accumulating copies under new run ids.
  - Not tied to a run, so nothing that clears run data can take them with it.
  - Sharded by the first two hex characters, so a few thousand files do not
    land in one directory.

The Gmail cache is under the owner for the same reason.

Which user that is comes from the same tenant the database layer uses, rather
than from an argument threaded through every call site - so a caller cannot
forget it, and there is one answer to "whose files are these" per request.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from .config import config
from .db.engine import IsolationError, current_tenant

log = logging.getLogger(__name__)

DATA_DIR = Path(config.DATA_DIR)
STATEMENT_STORE = DATA_DIR / "statements"
#: Where Gmail downloads land, per user. Reached through `gmail_cache()`
#: rather than directly, so nothing can read across accounts by accident.
GMAIL_CACHE = DATA_DIR / "gmail_cache"



def _owned(base: Path) -> Path:
    """`base` narrowed to the signed-in user's own directory."""
    tenant = current_tenant()
    if not tenant:
        raise IsolationError(
            "file storage needs a signed-in user; no tenant is bound")
    return base / tenant


def statement_store() -> Path:
    return _owned(STATEMENT_STORE)


def gmail_cache() -> Path:
    return _owned(GMAIL_CACHE)


def store_path(digest: str, suffix: str = ".pdf") -> Path:
    """Where a file with this content hash belongs."""
    if not digest or len(digest) < 2:
        raise ValueError("a content hash is required to place a file")
    suffix = suffix if suffix.startswith(".") else f".{suffix}"
    return statement_store() / digest[:2] / f"{digest}{suffix}"


def store_file(source: Path, digest: str) -> Path:
    """Copy `source` into the durable store and return its resting place.

    Idempotent: content already stored is left alone rather than rewritten,
    which makes re-uploading the same statement free and keeps the original
    mtime meaningful.
    """
    target = store_path(digest, Path(source).suffix.lower() or ".pdf")
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    # Copied to a temporary name and renamed into place, so an interrupted
    # copy can never leave a half-written file sitting at the path that the
    # content hash promises is complete.
    staging = target.with_suffix(target.suffix + ".part")
    shutil.copyfile(source, staging)
    staging.replace(target)
    return target


def adopt(source: Path, digest: str, *, remove_source: bool = False) -> Path:
    """Bring an existing file into the store, optionally removing the original."""
    target = store_file(source, digest)
    if remove_source and Path(source).resolve() != target.resolve():
        try:
            Path(source).unlink()
        except OSError:  # a file we cannot remove is not worth failing over
            log.debug("could not remove %s after adopting it", source)
    return target



def _files_under(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return [p for p in root.rglob("*") if p.is_file()
            and not p.name.endswith(".part")]


def stored_files() -> list[Path]:
    """Every statement file this user owns, across both stores."""
    return _files_under(statement_store()) + _files_under(gmail_cache())


def store_stats() -> dict[str, int]:
    """Counts split by whether the file could be obtained again.

    The distinction is the whole point of the split. A Gmail-sourced file can
    be re-downloaded; a manually uploaded one - an Amex statement, anything
    from a bank that does not email them - may be the only copy in existence,
    and no clearing action should treat the two the same way.
    """
    uploaded = _files_under(statement_store())
    cached = _files_under(gmail_cache())
    return {
        "count": len(uploaded) + len(cached),
        "bytes": sum(p.stat().st_size for p in uploaded + cached),
        "uploaded_count": len(uploaded),
        "uploaded_bytes": sum(p.stat().st_size for p in uploaded),
        "gmail_cached_count": len(cached),
        "gmail_cached_bytes": sum(p.stat().st_size for p in cached),
    }
