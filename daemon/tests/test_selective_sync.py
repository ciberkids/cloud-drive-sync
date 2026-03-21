"""Tests for selective sync ignore patterns."""
import pytest
from cloud_drive_sync.local.scanner import _is_ignored, load_ignore_file, scan_directory, DEFAULT_IGNORE_PATTERNS
from cloud_drive_sync.config import SyncPair, Config


class TestIsIgnored:
    def test_custom_pattern_match(self):
        assert _is_ignored("build/output.js", ["build/**"])

    def test_custom_pattern_no_match(self):
        assert not _is_ignored("src/main.py", ["build/**"])

    def test_wildcard_extension(self):
        assert _is_ignored("logs/app.log", ["*.log"])

    def test_directory_pattern(self):
        assert _is_ignored("node_modules/package/index.js", ["node_modules"])

    def test_multiple_patterns(self):
        patterns = ["*.log", "build/**", "node_modules"]
        assert _is_ignored("app.log", patterns)
        assert _is_ignored("build/out.js", patterns)
        assert _is_ignored("node_modules/x", patterns)
        assert not _is_ignored("src/main.py", patterns)


class TestLoadIgnoreFile:
    def test_load_existing_file(self, tmp_path):
        ignore_file = tmp_path / ".cloud-drive-sync-ignore"
        ignore_file.write_text("*.log\n# comment\n\nbuild/\n  spaces  \n")
        patterns = load_ignore_file(tmp_path)
        assert patterns == ["*.log", "build/", "spaces"]

    def test_no_file(self, tmp_path):
        assert load_ignore_file(tmp_path) == []


class TestConfigIgnorePatterns:
    def test_sync_pair_default(self):
        pair = SyncPair()
        assert pair.ignore_patterns == []

    def test_sync_pair_with_patterns(self):
        pair = SyncPair(ignore_patterns=["*.log", "build/"])
        assert pair.ignore_patterns == ["*.log", "build/"]

    def test_config_save_load_roundtrip(self, tmp_path):
        config = Config()
        config.sync.pairs.append(SyncPair(
            local_path="/tmp/test",
            ignore_patterns=["*.log", "node_modules"],
        ))
        config_file = tmp_path / "config.toml"
        config.save(config_file)
        loaded = Config.load(config_file)
        assert loaded.sync.pairs[0].ignore_patterns == ["*.log", "node_modules"]


@pytest.mark.asyncio
async def test_scan_directory_with_custom_patterns(tmp_path):
    (tmp_path / "keep.txt").write_text("keep")
    (tmp_path / "skip.log").write_text("skip")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "out.js").write_text("out")

    result = await scan_directory(
        tmp_path,
        ignore_patterns=DEFAULT_IGNORE_PATTERNS + ["*.log", "build"],
        ignore_hidden=False,
    )
    assert "keep.txt" in result
    assert "skip.log" not in result
    assert "build/out.js" not in result
