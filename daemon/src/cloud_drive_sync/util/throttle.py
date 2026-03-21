"""Bandwidth throttling utility."""

from __future__ import annotations

import time


class BandwidthThrottle:
    """Tracks bytes per second and returns sleep duration to stay within rate limit.

    Args:
        max_kbps: Maximum kilobytes per second. 0 means unlimited.
    """

    def __init__(self, max_kbps: int = 0) -> None:
        self._max_kbps = max_kbps
        self._window_start = time.monotonic()
        self._window_bytes = 0

    def sleep_duration(self, bytes_transferred: int) -> float:
        """Return how long to sleep (in seconds) to stay within the rate limit.

        Args:
            bytes_transferred: Number of bytes just transferred in the last chunk.

        Returns:
            Seconds to sleep. 0.0 if unlimited or under the limit.
        """
        if self._max_kbps <= 0:
            return 0.0

        self._window_bytes += bytes_transferred
        elapsed = time.monotonic() - self._window_start

        max_bytes_per_sec = self._max_kbps * 1024
        expected_time = self._window_bytes / max_bytes_per_sec

        delay = expected_time - elapsed
        if delay <= 0:
            # Reset window periodically to avoid drift
            if elapsed > 2.0:
                self._window_start = time.monotonic()
                self._window_bytes = 0
            return 0.0

        return delay

    def update_limit(self, max_kbps: int) -> None:
        """Update the rate limit.

        Args:
            max_kbps: New maximum kilobytes per second. 0 means unlimited.
        """
        self._max_kbps = max_kbps
        # Reset window to avoid stale accounting
        self._window_start = time.monotonic()
        self._window_bytes = 0
