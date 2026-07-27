"""Structured logging setup for cloud-drive-sync."""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from cloud_drive_sync.util.paths import data_dir

LOG_FORMAT = "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Provider exceptions stringify their whole request payload, so a single failed
# action could write a ~440 KB line (issue #48). Healthy lines are well under
# 600 B, so this keeps every real message intact while capping the pathological
# ones. The diagnostic part — action, path, status code — is always at the front.
MAX_MESSAGE_CHARS = 2000

# Cap total log footprint at 60 MB; before this the file grew without limit and
# reached 4.6 GB on a live instance.
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUP_COUNT = 5

_TRUNCATION_MARKER = "… [truncated,"


class TruncatingFilter(logging.Filter):
    """Caps the rendered length of each log message.

    Attached to handlers rather than to the logger: a logger's filters only run
    for records emitted on that exact logger, so a filter on ``cloud_drive_sync``
    would never see records from ``cloud_drive_sync.sync.executor`` — which is
    where the oversized messages come from.
    """

    def __init__(self, max_chars: int = MAX_MESSAGE_CHARS) -> None:
        super().__init__()
        self.max_chars = max_chars

    def filter(self, record: logging.LogRecord) -> bool:
        # The payload arrives as an exception object in ``args``, so the message
        # has to be rendered before it can be measured.
        message = record.getMessage()
        if len(message) <= self.max_chars or _TRUNCATION_MARKER in message:
            return True

        record.msg = (
            f"{message[: self.max_chars]}{_TRUNCATION_MARKER} {len(message)} chars total]"
        )
        # Args are already folded into msg; leaving them would re-expand it.
        record.args = ()
        return True


def setup_logging(level: str = "info", log_file: Path | None = None) -> logging.Logger:
    """Configure root logger with console and optional file handlers.

    Args:
        level: Log level name (debug, info, warning, error, critical).
        log_file: Path to log file. Defaults to data_dir/cloud-drive-sync.log.

    Returns:
        The configured root logger for cloud_drive_sync.
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logger = logging.getLogger("cloud_drive_sync")
    logger.setLevel(numeric_level)

    # Remove existing handlers to allow reconfiguration
    logger.handlers.clear()

    # One shared instance: it mutates the record, so whichever handler runs
    # first truncates and the rest see the already-truncated message.
    truncator = TruncatingFilter()

    # Console handler (stderr)
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(numeric_level)
    console.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
    console.addFilter(truncator)
    logger.addHandler(console)

    # File handler
    if log_file is None:
        log_dir = data_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "cloud-drive-sync.log"

    try:
        file_handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT
        )
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
        file_handler.addFilter(truncator)
        logger.addHandler(file_handler)
    except OSError:
        logger.warning("Could not open log file %s, logging to console only", log_file)

    return logger


def get_logger(name: str) -> logging.Logger:
    """Get a child logger under the cloud_drive_sync namespace."""
    return logging.getLogger(f"cloud_drive_sync.{name}")
