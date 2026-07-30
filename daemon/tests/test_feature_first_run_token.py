"""Tests for generating an access token on a fresh install.

Token auth shipped in v2.4.0 as opt-in, which left a fresh install wide open with
only a startup warning to say so. This closes that for **new** installs while
leaving upgrades exactly as they were.

The asymmetry is the whole design, so most of these tests are about it. Turning auth
on for an existing deployment would lock people out of a web UI they have
bookmarked, and the only place the new token would exist is the log of a service
they can no longer reach through the UI. So: new installs are protected, upgrades
keep the previous behaviour and the warning.

Two failure modes worth naming, because each would make the feature useless while
looking fine:

* If anything writes a config before first-run is decided, every install looks like
  an upgrade and this silently never fires.
* If it fired on upgrade, existing users would be locked out by an update.
"""

from __future__ import annotations

import pytest

from cloud_drive_sync.config import Config
from cloud_drive_sync.daemon import Daemon

UPGRADE_CONFIG = '[general]\nlog_level = "info"\n'


def _daemon(config_path, *, first_run, token=None, port=8080, demo=False):
    d = Daemon(http_token=token, http_port=port, demo=demo, config_path=config_path)
    d._config = Config.load(config_path)
    d._resolve_http_token(first_run)
    return d


@pytest.fixture
def cfg(tmp_path):
    return tmp_path / "config.toml"


# ── Fresh installs are protected ────────────────────────────────────────


def test_a_fresh_install_generates_a_token(cfg):
    d = _daemon(cfg, first_run=True)

    assert d._http_token, "a new install is still unauthenticated"
    assert len(d._http_token) >= 32


def test_the_generated_token_is_persisted(cfg):
    """It has to survive a restart, or every restart invalidates the browser
    session and the token the user just wrote down."""
    d = _daemon(cfg, first_run=True)

    assert cfg.exists()
    assert Config.load(cfg).http.token == d._http_token


def test_a_restart_keeps_the_same_token(cfg):
    first = _daemon(cfg, first_run=True)._http_token

    # Second start: the config now exists, so this is no longer a first run.
    again = _daemon(cfg, first_run=False)

    assert again._http_token == first


def test_two_installs_get_different_tokens(tmp_path):
    a = _daemon(tmp_path / "a.toml", first_run=True)._http_token
    b = _daemon(tmp_path / "b.toml", first_run=True)._http_token

    assert a != b


def test_a_token_is_generated_even_when_http_is_disabled(cfg):
    """So that enabling the port later is already protected — by then the config
    exists, so it would no longer count as a first run."""
    d = _daemon(cfg, first_run=True, port=0)

    assert d._http_token
    assert Config.load(cfg).http.token == d._http_token


def test_the_token_is_logged_where_a_container_user_can_find_it(cfg, caplog):
    """For a headless deployment this log line is the only copy of the token."""
    with caplog.at_level("WARNING"):
        d = _daemon(cfg, first_run=True)

    assert d._http_token in caplog.text
    assert "First run" in caplog.text


# ── Upgrades are left alone ─────────────────────────────────────────────


def test_an_upgrade_does_not_gain_a_token(cfg):
    """The compatibility guarantee. An update must not lock anyone out."""
    cfg.write_text(UPGRADE_CONFIG)

    d = _daemon(cfg, first_run=False)

    assert d._http_token is None


def test_an_upgrade_config_is_not_rewritten(cfg):
    cfg.write_text(UPGRADE_CONFIG)

    _daemon(cfg, first_run=False)

    assert cfg.read_text() == UPGRADE_CONFIG


def test_an_existing_configured_token_is_used(cfg):
    cfg.write_text('[http]\ntoken = "already-configured"\n')

    d = _daemon(cfg, first_run=False)

    assert d._http_token == "already-configured"


# ── Precedence and demo mode ────────────────────────────────────────────


def test_an_explicit_token_wins_over_the_config(cfg):
    cfg.write_text('[http]\ntoken = "from-config"\n')

    d = _daemon(cfg, first_run=False, token="from-flag")

    assert d._http_token == "from-flag"


def test_an_explicit_token_suppresses_generation(cfg):
    """Nothing should be written when the operator supplied a token themselves."""
    d = _daemon(cfg, first_run=True, token="from-flag")

    assert d._http_token == "from-flag"
    assert not cfg.exists()


def test_demo_mode_does_not_write_a_token(cfg):
    """``--demo`` shares the real config file, so writing to it as a side effect of
    a demo run would be an unwanted change to the user's actual configuration."""
    d = _daemon(cfg, first_run=True, demo=True)

    assert d._http_token is None
    assert not cfg.exists()


# ── Robustness ──────────────────────────────────────────────────────────


def test_a_token_still_applies_when_the_config_cannot_be_saved(cfg, monkeypatch, caplog):
    """A read-only config must not mean starting unprotected. The token holds for
    this session, and the warning says it will change on restart."""
    def _boom(self, path=None):
        raise OSError("read-only file system")

    monkeypatch.setattr(Config, "save", _boom)

    with caplog.at_level("WARNING"):
        d = _daemon(cfg, first_run=True)

    assert d._http_token, "failed to persist, so it gave up on protecting the port"
    assert "could not save" in caplog.text
    assert "restart" in caplog.text


def test_the_config_round_trips_the_token(tmp_path):
    """Guards the serialiser: a token that saves but does not load back would make
    every restart mint a new one."""
    path = tmp_path / "config.toml"
    c = Config.load(path)
    c.http.token = "round-trip-me"
    c.save(path)

    assert Config.load(path).http.token == "round-trip-me"


def test_an_unset_token_is_omitted_from_the_file(tmp_path):
    """So an upgraded config does not gain an empty line that reads like a setting
    somebody deliberately cleared."""
    path = tmp_path / "config.toml"
    c = Config.load(path)
    c.save(path)

    assert "[http]" not in path.read_text()


# ── The detection itself ────────────────────────────────────────────────


def test_config_load_does_not_create_the_file(tmp_path):
    """The fact first-run detection rests on.

    If ``Config.load`` ever started writing a default file, every install would look
    like an upgrade and this feature would quietly stop working — no error, no
    warning, just deployments that are open again.
    """
    path = tmp_path / "config.toml"

    Config.load(path)

    assert not path.exists()
