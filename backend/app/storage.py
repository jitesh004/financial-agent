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

Files now live at `data/statements/<first2>/<sha256><ext>`:

  - Addressed by content, so re-uploading the same statement resolves to the
    same path instead of accumulating copies under new run ids.
  - Not tied to a run, so nothing that clears run data can take them with it.
  - Sharded by the first two hex characters, so a few thousand files do not
    land in one directory.

The Gmail cache is left exactly where it is. It is already flat and already
run-independent, and moving files a working system is pointing at buys nothing.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
STATEMENT_STORE = ROOT / "data" / "statements"


def store_path(digest: str, suffix: str = ".pdf") -> Path:
    """Where a file with this content hash belongs."""
    if not digest or len(digest) < 2:
        raise ValueError("a content hash is required to place a file")
    suffix = suffix if suffix.startswith(".") else f".{suffix}"
    return STATEMENT_STORE / digest[:2] / f"{digest}{suffix}"


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


#: Where Gmail downloads have always landed. Flat and run-independent
#: already, so files here are left in place rather than migrated.
GMAIL_CACHE = ROOT / "data" / "gmail_cache"


def _files_under(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return [p for p in root.rglob("*") if p.is_file()
            and not p.name.endswith(".part")]


def stored_files() -> list[Path]:
    """Every statement file the app owns, across both stores."""
    return _files_under(STATEMENT_STORE) + _files_under(GMAIL_CACHE)


def store_stats() -> dict[str, int]:
    """Counts split by whether the file could be obtained again.

    The distinction is the whole point of the split. A Gmail-sourced file can
    be re-downloaded; a manually uploaded one - an Amex statement, anything
    from a bank that does not email them - may be the only copy in existence,
    and no clearing action should treat the two the same way.
    """
    uploaded = _files_under(STATEMENT_STORE)
    cached = _files_under(GMAIL_CACHE)
    return {
        "count": len(uploaded) + len(cached),
        "bytes": sum(p.stat().st_size for p in uploaded + cached),
        "uploaded_count": len(uploaded),
        "uploaded_bytes": sum(p.stat().st_size for p in uploaded),
        "gmail_cached_count": len(cached),
        "gmail_cached_bytes": sum(p.stat().st_size for p in cached),
    }
