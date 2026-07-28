"""Regression test: Config.save() must write back to where it was loaded from.

`Config.load(path)` honoured a custom path but `save()` defaulted to
`config_path()`, the standard location. So `cloud-drive-sync --config
/etc/cloud-drive-sync/config.toml` read the custom file and wrote every
subsequent settings change to `~/.config/cloud-drive-sync/config.toml` instead.

The failure was quiet in the worst way: the change took effect in memory, so it
looked like it worked, and disappeared on restart — while silently modifying a
file the user was not using. `docs/Configuration.md` documents `--config` as a
supported way to relocate the config, so this was a broken promise, not just an
internal quirk.
"""

from __future__ import annotations

from cloud_drive_sync.config import Config


def test_save_writes_back_to_the_loaded_path(tmp_path):
    custom = tmp_path / "custom.toml"
    custom.write_text('[sync]\npoll_interval = 42\n')

    cfg = Config.load(custom)
    cfg.sync.poll_interval = 99
    cfg.save()

    assert "poll_interval = 99" in custom.read_text()


def test_save_does_not_touch_the_default_location(tmp_path, monkeypatch):
    """The actual harm: writing to a file the user never asked about."""
    default = tmp_path / "default" / "config.toml"
    monkeypatch.setattr("cloud_drive_sync.config.config_path", lambda: default)

    custom = tmp_path / "custom.toml"
    custom.write_text('[sync]\npoll_interval = 42\n')

    cfg = Config.load(custom)
    cfg.sync.poll_interval = 7
    cfg.save()

    assert not default.exists(), "save() wrote to the default path instead"
    assert "poll_interval = 7" in custom.read_text()


def test_an_explicit_path_still_wins(tmp_path):
    custom = tmp_path / "custom.toml"
    custom.write_text('[sync]\npoll_interval = 1\n')
    elsewhere = tmp_path / "elsewhere.toml"

    cfg = Config.load(custom)
    cfg.save(elsewhere)

    assert elsewhere.exists()
    assert "poll_interval = 1" in custom.read_text(), "the source must be left alone"


def test_a_config_loaded_from_defaults_still_saves_to_defaults(tmp_path, monkeypatch):
    default = tmp_path / "config.toml"
    monkeypatch.setattr("cloud_drive_sync.config.config_path", lambda: default)

    cfg = Config.load()
    cfg.sync.poll_interval = 55
    cfg.save()

    assert "poll_interval = 55" in default.read_text()


def test_a_config_never_loaded_falls_back_to_the_default_path(tmp_path, monkeypatch):
    """Config() constructed directly, e.g. in tests, has no source path."""
    default = tmp_path / "config.toml"
    monkeypatch.setattr("cloud_drive_sync.config.config_path", lambda: default)

    Config().save()

    assert default.exists()
