"""Structured logging setup for cloud-drive-sync."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from cloud_drive_sync.util.paths import data_dir

LOG_FORMAT = "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


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

    # Console handler (stderr)
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(numeric_level)
    console.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
    logger.addHandler(console)

    # File handler
    if log_file is None:
        log_dir = data_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "cloud-drive-sync.log"

    try:
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
        logger.addHandler(file_handler)
    except OSError:
        logger.warning("Could not open log file %s, logging to console only", log_file)

    return logger


def get_logger(name: str) -> logging.Logger:
    """Get a child logger under the cloud_drive_sync namespace."""
    return logging.getLogger(f"cloud_drive_sync.{name}")
