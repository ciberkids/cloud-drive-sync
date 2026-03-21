"""Tests for the async retry decorator."""

from __future__ import annotations

import pytest

from cloud_drive_sync.util.retry import async_retry


@pytest.mark.asyncio
async def test_succeeds_on_first_try():
    call_count = 0

    @async_retry(max_retries=3, base_delay=0.01)
    async def succeeds():
        nonlocal call_count
        call_count += 1
        return "ok"

    result = await succeeds()
    assert result == "ok"
    assert call_count == 1


@pytest.mark.asyncio
async def test_retries_on_failure_then_succeeds():
    call_count = 0

    @async_retry(max_retries=3, base_delay=0.01, jitter=False)
    async def fails_twice():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ValueError("not yet")
        return "success"

    result = await fails_twice()
    assert result == "success"
    assert call_count == 3


@pytest.mark.asyncio
async def test_exhausts_retries():
    call_count = 0

    @async_retry(max_retries=2, base_delay=0.01, jitter=False)
    async def always_fails():
        nonlocal call_count
        call_count += 1
        raise RuntimeError("always fails")

    with pytest.raises(RuntimeError, match="always fails"):
        await always_fails()
    # 1 initial + 2 retries = 3 total
    assert call_count == 3


@pytest.mark.asyncio
async def test_only_catches_specified_exceptions():
    @async_retry(max_retries=3, base_delay=0.01, exceptions=(ValueError,))
    async def raises_type_error():
        raise TypeError("not caught")

    with pytest.raises(TypeError, match="not caught"):
        await raises_type_error()


@pytest.mark.asyncio
async def test_retry_with_zero_retries():
    call_count = 0

    @async_retry(max_retries=0, base_delay=0.01)
    async def fails():
        nonlocal call_count
        call_count += 1
        raise ValueError("fail")

    with pytest.raises(ValueError):
        await fails()
    assert call_count == 1


@pytest.mark.asyncio
async def test_preserves_function_name():
    @async_retry(max_retries=1, base_delay=0.01)
    async def my_function():
        return True

    assert my_function.__name__ == "my_function"


@pytest.mark.asyncio
async def test_passes_args_and_kwargs():
    @async_retry(max_retries=1, base_delay=0.01)
    async def adder(a, b, extra=0):
        return a + b + extra

    result = await adder(1, 2, extra=3)
    assert result == 6
