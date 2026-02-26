"""Exponential backoff retry decorator for async functions."""

from __future__ import annotations

import asyncio
import functools
import random
from collections.abc import Callable
from typing import Any

from gdrive_sync.util.logging import get_logger

log = get_logger("retry")


def async_retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
    jitter: bool = True,
) -> Callable[..., Any]:
    """Decorator that retries an async function with exponential backoff.

    Args:
        max_retries: Maximum number of retry attempts.
        base_delay: Initial delay in seconds between retries.
        max_delay: Maximum delay cap in seconds.
        exceptions: Tuple of exception types to catch and retry on.
        jitter: Whether to add random jitter to the delay.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: BaseException | None = None
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt == max_retries:
                        log.error(
                            "All %d retries exhausted for %s: %s",
                            max_retries,
                            func.__name__,
                            exc,
                        )
                        raise
                    delay = min(base_delay * (2**attempt), max_delay)
                    if jitter:
                        delay *= 0.5 + random.random()
                    log.warning(
                        "Retry %d/%d for %s after %.1fs: %s",
                        attempt + 1,
                        max_retries,
                        func.__name__,
                        delay,
                        exc,
                    )
                    await asyncio.sleep(delay)
            raise last_exc  # type: ignore[misc]

        return wrapper

    return decorator
