"""Regression tests for issue #48.

Provider exceptions stringify their entire request payload, and the executor
logged the exception object directly, so one failed action wrote a ~440 KB line.
With no rotation the log reached 4.6 GB across 334,366 lines on a live instance.
"""

from __future__ import annotations

import logging
import logging.handlers

from cloud_drive_sync.util.logging import (
    LOG_BACKUP_COUNT,
    LOG_MAX_BYTES,
    MAX_MESSAGE_CHARS,
    TruncatingFilter,
    setup_logging,
)


class _PayloadException(Exception):
    """Stands in for ``NextcloudException``, whose ``str()`` embeds the payload.

    Shape taken from the issue: a short diagnostic head followed by the whole
    serialised property list.
    """

    def __init__(self, count: int = 5000) -> None:
        props = ["nc:lock", "nc:lock-timeout"] * count
        self._text = f"[413] Request Entity Too Large <list: user@example.com, /Documents, {props}>"
        super().__init__(self._text)

    def __str__(self) -> str:
        return self._text


def _record(msg: str, *args: object) -> logging.LogRecord:
    return logging.LogRecord(
        name="cloud_drive_sync.sync.executor",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )


def test_oversized_payload_arrives_via_args_not_msg():
    """Guards the reproduction: the payload is an exception object in ``args``.

    A filter that measured ``record.msg`` instead of the rendered message would
    pass its own tests and still write 440 KB lines in production.
    """
    exc = _PayloadException()
    record = _record("Action %s on %s failed: %s", "mkdir", "Documents/Taxes", exc)

    assert len(record.msg) < 100
    assert len(record.getMessage()) > 100_000


def test_long_message_is_truncated():
    exc = _PayloadException()
    record = _record("Action %s on %s failed: %s", "mkdir", "Documents/Taxes", exc)
    original_length = len(record.getMessage())

    assert TruncatingFilter().filter(record) is True

    rendered = record.getMessage()
    assert len(rendered) < MAX_MESSAGE_CHARS + 60
    assert str(original_length) in rendered, "the real length must stay visible"
    assert "truncated" in rendered


def test_truncation_keeps_the_diagnostic_head():
    """Action, path and status code sit at the front and must survive."""
    exc = _PayloadException()
    record = _record("Action %s on %s failed: %s", "mkdir", "Documents/Taxes/2025", exc)

    TruncatingFilter().filter(record)
    rendered = record.getMessage()

    assert rendered.startswith("Action mkdir on Documents/Taxes/2025 failed: ")
    assert "[413] Request Entity Too Large" in rendered


def test_short_messages_are_untouched():
    record = _record("Action %s on %s failed: %s", "mkdir", "Documents", "[404] Not Found")
    expected = record.getMessage()

    TruncatingFilter().filter(record)

    assert record.getMessage() == expected
    assert record.args == ("mkdir", "Documents", "[404] Not Found")


def test_truncation_is_idempotent_across_handlers():
    """One filter instance is shared by console and file handlers.

    The first handler mutates the record, so the second must not append a second
    marker or re-truncate.
    """
    record = _record("Action %s failed: %s", "mkdir", _PayloadException())
    truncator = TruncatingFilter()

    truncator.filter(record)
    once = record.getMessage()
    truncator.filter(record)
    truncator.filter(record)

    assert record.getMessage() == once
    assert once.count("truncated") == 1


def test_args_are_cleared_so_the_formatter_cannot_re_expand():
    record = _record("Action %s failed: %s", "mkdir", _PayloadException())

    TruncatingFilter().filter(record)

    assert record.args == ()
    formatted = logging.Formatter("%(message)s").format(record)
    assert len(formatted) < MAX_MESSAGE_CHARS + 60


def test_setup_logging_rotates_and_truncates(tmp_path):
    """End-to-end: the real handler set caps both line length and total size."""
    log_file = tmp_path / "cloud-drive-sync.log"
    logger = setup_logging("info", log_file=log_file)
    try:
        file_handlers = [
            h for h in logger.handlers if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert file_handlers, "file handler must rotate; an unrotated one reached 4.6 GB"
        handler = file_handlers[0]
        assert handler.maxBytes == LOG_MAX_BYTES
        assert handler.backupCount == LOG_BACKUP_COUNT

        # Every handler must truncate, including the console one — a 440 KB line
        # also bloats journald/docker logs.
        for h in logger.handlers:
            assert any(isinstance(f, TruncatingFilter) for f in h.filters), h

        child = logging.getLogger("cloud_drive_sync.sync.executor")
        child.error("Action %s on %s failed: %s", "mkdir", "Documents", _PayloadException())

        handler.flush()
        contents = log_file.read_text()
        assert len(contents) < 10_000, "oversized payload reached the log file"
        assert "truncated" in contents
        assert "Action mkdir on Documents failed" in contents
    finally:
        for h in list(logger.handlers):
            h.close()
        logger.handlers.clear()


def test_child_logger_records_are_truncated(tmp_path):
    """The filter is on the handlers precisely because logger filters would not
    see records emitted on child loggers."""
    log_file = tmp_path / "cloud-drive-sync.log"
    logger = setup_logging("info", log_file=log_file)
    try:
        for name in ("providers.nextcloud.changes", "sync.executor", "retry"):
            logging.getLogger(f"cloud_drive_sync.{name}").error(
                "failed: %s", _PayloadException()
            )

        for h in logger.handlers:
            h.flush()
        assert len(log_file.read_text()) < 20_000
    finally:
        for h in list(logger.handlers):
            h.close()
        logger.handlers.clear()


def test_rotation_bounds_total_size_on_disk(tmp_path, monkeypatch):
    """The cap has to hold even under a flood of oversized failures."""
    monkeypatch.setattr("cloud_drive_sync.util.logging.LOG_MAX_BYTES", 8192)
    monkeypatch.setattr("cloud_drive_sync.util.logging.LOG_BACKUP_COUNT", 2)

    log_file = tmp_path / "cloud-drive-sync.log"
    logger = setup_logging("info", log_file=log_file)
    try:
        child = logging.getLogger("cloud_drive_sync.sync.executor")
        for _ in range(200):
            child.error("Action %s failed: %s", "mkdir", _PayloadException())

        for h in logger.handlers:
            h.flush()
        total = sum(p.stat().st_size for p in tmp_path.iterdir())
        # 200 unrotated 150 KB lines would be ~30 MB.
        assert total < 8192 * 4, f"log grew to {total} bytes despite rotation"
    finally:
        for h in list(logger.handlers):
            h.close()
        logger.handlers.clear()
