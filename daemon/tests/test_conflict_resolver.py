"""Tests for ConflictResolver class and ask_user flow."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from gdrive_sync.db.models import ConflictRecord
from gdrive_sync.sync.conflict import ConflictResolver, resolve_ask_user
from gdrive_sync.sync.planner import ActionType


class TestConflictResolver:
    @pytest.mark.asyncio
    async def test_keep_both_creates_copy_and_downloads(self, tmp_path: Path):
        local_file = tmp_path / "file.txt"
        local_file.write_text("local content")

        resolver = ConflictResolver("keep_both")
        conflict = ConflictRecord(
            path="file.txt", pair_id="p0",
            local_md5="aaa", remote_md5="bbb",
        )

        result = await resolver.resolve(
            path="file.txt",
            local_path=local_file,
            local_mtime=100.0,
            remote_mtime=200.0,
            conflict=conflict,
        )

        assert result is not None
        assert result.action == ActionType.DOWNLOAD
        assert "keep_both" in result.reason

        # Conflict copy should exist
        copies = list(tmp_path.glob("*conflict*"))
        assert len(copies) == 1

    @pytest.mark.asyncio
    async def test_newest_wins_local_newer(self, tmp_path: Path):
        local_file = tmp_path / "file.txt"
        local_file.write_text("local")

        resolver = ConflictResolver("newest_wins")
        conflict = ConflictRecord(path="file.txt", pair_id="p0")

        result = await resolver.resolve(
            path="file.txt",
            local_path=local_file,
            local_mtime=200.0,
            remote_mtime=100.0,
            conflict=conflict,
        )

        assert result is not None
        assert result.action == ActionType.UPLOAD

    @pytest.mark.asyncio
    async def test_newest_wins_remote_newer(self, tmp_path: Path):
        local_file = tmp_path / "file.txt"
        local_file.write_text("local")

        resolver = ConflictResolver("newest_wins")
        conflict = ConflictRecord(path="file.txt", pair_id="p0")

        result = await resolver.resolve(
            path="file.txt",
            local_path=local_file,
            local_mtime=100.0,
            remote_mtime=200.0,
            conflict=conflict,
        )

        assert result is not None
        assert result.action == ActionType.DOWNLOAD

    @pytest.mark.asyncio
    async def test_ask_user_returns_none(self, tmp_path: Path):
        local_file = tmp_path / "file.txt"
        local_file.write_text("local")

        resolver = ConflictResolver("ask_user")
        conflict = ConflictRecord(path="file.txt", pair_id="p0")

        result = await resolver.resolve(
            path="file.txt",
            local_path=local_file,
            local_mtime=100.0,
            remote_mtime=200.0,
            conflict=conflict,
        )

        # ask_user returns None (waits for user input)
        assert result is None

    @pytest.mark.asyncio
    async def test_unknown_strategy_returns_none(self, tmp_path: Path):
        local_file = tmp_path / "file.txt"
        local_file.write_text("local")

        resolver = ConflictResolver("unknown_strategy")
        conflict = ConflictRecord(path="file.txt", pair_id="p0")

        result = await resolver.resolve(
            path="file.txt",
            local_path=local_file,
            local_mtime=100.0,
            remote_mtime=200.0,
            conflict=conflict,
        )

        assert result is None

    def test_strategy_property(self):
        resolver = ConflictResolver("keep_both")
        assert resolver.strategy == "keep_both"

        resolver.strategy = "newest_wins"
        assert resolver.strategy == "newest_wins"

    def test_set_user_resolution(self):
        resolver = ConflictResolver("ask_user")
        loop = asyncio.new_event_loop()
        future = loop.create_future()
        resolver._pending_resolutions[42] = future

        resolver.set_user_resolution(42, "keep_local")
        assert future.result() == "keep_local"
        loop.close()

    def test_set_user_resolution_nonexistent(self):
        resolver = ConflictResolver("ask_user")
        # Should not raise
        resolver.set_user_resolution(999, "keep_local")


class TestResolveAskUser:
    @pytest.mark.asyncio
    async def test_calls_notify_callback(self):
        notifications = []

        async def notify(method, params):
            notifications.append((method, params))

        conflict = ConflictRecord(
            id=42, path="file.txt", pair_id="p0",
            local_md5="aaa", remote_md5="bbb",
        )

        result = await resolve_ask_user("file.txt", conflict, notify_callback=notify)
        assert result is None
        assert len(notifications) == 1
        assert notifications[0][0] == "conflict_detected"
        assert notifications[0][1]["path"] == "file.txt"
        assert notifications[0][1]["id"] == 42

    @pytest.mark.asyncio
    async def test_no_callback(self):
        conflict = ConflictRecord(path="file.txt", pair_id="p0")
        result = await resolve_ask_user("file.txt", conflict, notify_callback=None)
        assert result is None
