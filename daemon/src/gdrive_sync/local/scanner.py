"""Full directory scanner with MD5 hashing."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path

from gdrive_sync.local.hasher import md5_hash
from gdrive_sync.util.logging import get_logger

log = get_logger("local.scanner")

DEFAULT_IGNORE_PATTERNS = [
    ".git",
    ".git/**",
    "__pycache__",
    "*.pyc",
    ".DS_Store",
    "Thumbs.db",
    ".gdrive-sync-*",
]


@dataclass
class LocalFileInfo:
    """Info about a local file."""

    md5: str
    mtime: float
    size: int
    is_dir: bool = False


def _is_ignored(rel_path: str, patterns: list[str]) -> bool:
    """Check whether a relative path matches any ignore pattern."""
    parts = Path(rel_path).parts
    for pattern in patterns:
        if fnmatch.fnmatch(rel_path, pattern):
            return True
        # Also match any path component
        for part in parts:
            if fnmatch.fnmatch(part, pattern):
                return True
    return False


def load_ignore_file(root: Path) -> list[str]:
    """Load ignore patterns from .gdrive-sync-ignore file."""
    ignore_file = root / ".gdrive-sync-ignore"
    if not ignore_file.is_file():
        return []
    patterns = []
    for line in ignore_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    return patterns


def _is_hidden(rel_path: str) -> bool:
    """Check if any path component starts with a dot."""
    return any(part.startswith(".") for part in Path(rel_path).parts)


async def scan_directory(
    root: Path,
    ignore_patterns: list[str] | None = None,
    ignore_hidden: bool = True,
) -> dict[str, LocalFileInfo]:
    """Recursively scan a directory, computing MD5 hashes.

    Args:
        root: Root directory to scan.
        ignore_patterns: Glob patterns to ignore. Uses defaults if None.

    Returns:
        Dict mapping relative paths to LocalFileInfo.
    """
    patterns = ignore_patterns if ignore_patterns is not None else DEFAULT_IGNORE_PATTERNS
    result: dict[str, LocalFileInfo] = {}

    if not root.is_dir():
        log.warning("Scan target %s is not a directory", root)
        return result

    log.info("Scanning %s ...", root)
    count = 0

    for path in root.rglob("*"):
        rel = str(path.relative_to(root))
        if _is_ignored(rel, patterns):
            continue
        if ignore_hidden and _is_hidden(rel):
            continue

        if path.is_dir():
            # Include directories so the planner can match remote folders
            result[rel] = LocalFileInfo(md5="", mtime=path.stat().st_mtime, size=0, is_dir=True)
            continue

        if not path.is_file():
            continue

        try:
            stat = path.stat()
            digest = await md5_hash(path)
            result[rel] = LocalFileInfo(md5=digest, mtime=stat.st_mtime, size=stat.st_size)
            count += 1
        except OSError as exc:
            log.warning("Could not scan %s: %s", rel, exc)

    log.info("Scan complete: %d files in %s", count, root)
    return result
