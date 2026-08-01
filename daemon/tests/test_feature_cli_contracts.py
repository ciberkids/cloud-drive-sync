"""Tests for the CLI's contract: what it prints, and what it exits with.

``cli.py`` was 29% covered. Every command here is a documented promise in
``docs/CLI.md``, and the defects found were all the same shape — **the command
reported success for something that had not happened**:

* ``pause 0`` / ``resume 0`` / ``sync 0`` — the id form ``pair list`` prints — matched
  nothing, because the engine keys pairs as ``pair_0``. The daemon answered
  ``not_found``, the CLI discarded the status and printed "Sync paused."
* ``pause`` with no id affected only the *first* pair while the output, the docs and
  the web UI's global toggle all say every pair.
* ``repair`` given an id matching nothing scanned nothing and reported "everything
  looks healthy" — the most misleading answer a repair tool can give.
* ``pair add`` accepted an account that does not exist, reported success, and the pair
  was then discarded by the next ``pair list``.

A silent no-op that reports success is worse than an error, because the user believes
the thing is done. So these assert **exit codes and stderr**, not just that a command
runs.

The daemon is not started. ``_run_client_call`` is replaced with a fake that records
the method and params and returns whatever the daemon would, which is what lets the
"reported success while the daemon said not_found" cases be tested at all.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from cloud_drive_sync import cli as cli_mod


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def fake_daemon(monkeypatch):
    """Stand in for the running daemon.

    ``calls`` records ``(method, params)``; ``replies`` maps a method name to the
    result the daemon should return.
    """

    class Fake:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []
            self.replies: dict = {}

        def __call__(self, method, params=None):
            self.calls.append((method, params or {}))
            if method in self.replies:
                reply = self.replies[method]
                if isinstance(reply, Exception):
                    raise reply
                return reply
            return {}

        def params_for(self, method):
            return next(p for m, p in self.calls if m == method)

    fake = Fake()
    monkeypatch.setattr(cli_mod, "_run_client_call", fake)
    return fake


# ── An unresolvable pair id must fail, not report success ────────────────


@pytest.mark.parametrize(
    ("command", "method"),
    [("sync", "force_sync"), ("pause", "pause_sync"), ("resume", "resume_sync")],
)
def test_an_unknown_pair_id_exits_non_zero(runner, fake_daemon, command, method):
    """The daemon says not_found; the CLI used to throw that away and print success."""
    fake_daemon.replies[method] = {"status": "not_found"}

    result = runner.invoke(cli_mod.cli, [command, "7"])

    assert result.exit_code != 0, f"{command} reported success for an unknown pair"
    assert "no such sync pair" in result.output.lower()
    assert "7" in result.output


@pytest.mark.parametrize(
    ("command", "method"),
    [("sync", "force_sync"), ("pause", "pause_sync"), ("resume", "resume_sync")],
)
def test_a_known_pair_id_succeeds_and_is_passed_through(runner, fake_daemon, command, method):
    fake_daemon.replies[method] = {"status": "ok" if method == "force_sync" else method.split("_")[0] + "d"}

    result = runner.invoke(cli_mod.cli, [command, "0"])

    assert result.exit_code == 0, result.output
    assert fake_daemon.params_for(method).get("pair_id") == "0"


def test_pausing_without_an_id_says_it_paused_everything(runner, fake_daemon):
    """And is telling the truth: the handler now pauses all pairs.

    It used to pause only the first pair while printing "Sync paused.", so a user with
    three pairs believed all three were stopped when two were still syncing.
    """
    fake_daemon.replies["pause_sync"] = {"status": "paused", "pairs": 3}

    result = runner.invoke(cli_mod.cli, ["pause"])

    assert result.exit_code == 0
    assert "all" in result.output.lower()
    assert "3" in result.output
    assert "pair_id" not in fake_daemon.params_for("pause_sync")


def test_resuming_without_an_id_says_it_resumed_everything(runner, fake_daemon):
    fake_daemon.replies["resume_sync"] = {"status": "resumed", "pairs": 2}

    result = runner.invoke(cli_mod.cli, ["resume"])

    assert result.exit_code == 0
    assert "all" in result.output.lower()


def test_syncing_without_an_id_says_it_covered_all_pairs(runner, fake_daemon):
    result = runner.invoke(cli_mod.cli, ["sync"])

    assert result.exit_code == 0
    assert "all" in result.output.lower()


# ── repair must not claim health after checking nothing ──────────────────


def test_repair_that_scanned_nothing_does_not_claim_health(runner, fake_daemon):
    fake_daemon.replies["repair"] = {"repaired": 0, "pairs_scanned": 0, "stubs": []}

    result = runner.invoke(cli_mod.cli, ["repair", "9"])

    assert result.exit_code != 0, "reported success having examined nothing"
    assert "healthy" not in result.output.lower(), "claimed health after scanning nothing"
    assert "nothing was checked" in result.output.lower()


def test_repair_that_found_nothing_after_scanning_reports_health(runner, fake_daemon):
    """The legitimate healthy case must still read as healthy."""
    fake_daemon.replies["repair"] = {"repaired": 0, "pairs_scanned": 2, "stubs": []}

    result = runner.invoke(cli_mod.cli, ["repair"])

    assert result.exit_code == 0
    assert "healthy" in result.output.lower()


def test_repair_reports_the_stubs_it_found(runner, fake_daemon):
    fake_daemon.replies["repair"] = {
        "repaired": 2,
        "pairs_scanned": 1,
        "stubs": ["/home/u/a.txt", "/home/u/b.txt"],
    }

    result = runner.invoke(cli_mod.cli, ["repair"])

    assert result.exit_code == 0
    assert "/home/u/a.txt" in result.output


def test_a_dry_run_repair_says_so(runner, fake_daemon):
    fake_daemon.replies["repair"] = {
        "repaired": 1, "pairs_scanned": 1, "stubs": ["/home/u/a.txt"]
    }

    result = runner.invoke(cli_mod.cli, ["repair", "--dry-run"])

    assert "dry-run" in result.output.lower()
    assert "would delete" in result.output.lower()
    assert fake_daemon.params_for("repair")["dry_run"] is True


# ── account remove: the provider option the docs now promise ─────────────


def test_account_remove_forwards_the_provider(runner, fake_daemon):
    """Without this the CLI removed every provider's account for the address, which is
    the cross-provider credential loss fixed one layer down in v2.4.2."""
    result = runner.invoke(
        cli_mod.cli, ["account", "remove", "a@example.com", "--provider", "dropbox"]
    )

    assert result.exit_code == 0, result.output
    params = fake_daemon.params_for("remove_account")
    assert params["email"] == "a@example.com"
    assert params["provider"] == "dropbox"


def test_account_remove_without_a_provider_sends_none(runner, fake_daemon):
    """The single-provider case stays a one-liner; the daemon refuses only if the
    address is genuinely ambiguous."""
    result = runner.invoke(cli_mod.cli, ["account", "remove", "a@example.com"])

    assert result.exit_code == 0
    assert "provider" not in fake_daemon.params_for("remove_account")


def test_account_remove_rejects_an_unknown_provider(runner, fake_daemon):
    result = runner.invoke(
        cli_mod.cli, ["account", "remove", "a@example.com", "--provider", "nosuchcloud"]
    )

    assert result.exit_code != 0
    assert fake_daemon.calls == [], "an invalid provider still reached the daemon"


def test_an_ambiguous_removal_surfaces_the_daemons_error(runner, fake_daemon):
    fake_daemon.replies["remove_account"] = RuntimeError(
        "a@example.com has accounts on dropbox, gdrive. Say which one to remove."
    )

    result = runner.invoke(cli_mod.cli, ["account", "remove", "a@example.com"])

    assert result.exit_code != 0
    assert "dropbox" in result.output and "gdrive" in result.output


# ── Failures are reported, not swallowed ────────────────────────────────


@pytest.mark.parametrize(
    "argv",
    [
        ["sync"],
        ["pause"],
        ["resume"],
        ["account", "list"],
        ["account", "remove", "a@example.com"],
        ["pair", "list"],
        ["repair"],
        ["activity"],
        ["conflicts"],
    ],
)
def test_a_daemon_that_is_not_running_exits_non_zero(runner, fake_daemon, argv):
    """Every one of these needs the daemon. A command that printed nothing useful and
    exited 0 would make a script think it had succeeded."""
    method_agnostic = ConnectionRefusedError("No such file or directory")
    fake_daemon.replies = dict.fromkeys(
        [
            "force_sync", "pause_sync", "resume_sync", "list_accounts", "remove_account",
            "get_sync_pairs", "repair", "get_activity_log", "get_conflicts",
        ],
        method_agnostic,
    )

    result = runner.invoke(cli_mod.cli, argv)

    assert result.exit_code != 0, f"{argv} exited 0 with no daemon"
    assert "error" in result.output.lower()


# ── gen-token, which the docs tell people to use ────────────────────────


def test_gen_token_prints_a_usable_token(runner):
    result = runner.invoke(cli_mod.cli, ["gen-token"])

    assert result.exit_code == 0
    token = result.output.strip()
    assert len(token) >= 32, f"token too short to be useful: {token!r}"
    assert " " not in token, "a token with a space in it cannot be pasted into a header"


def test_gen_token_is_different_every_time(runner):
    a = runner.invoke(cli_mod.cli, ["gen-token"]).output.strip()
    b = runner.invoke(cli_mod.cli, ["gen-token"]).output.strip()

    assert a != b


def test_gen_token_does_not_need_a_running_daemon(runner, fake_daemon):
    """It is the first thing a user runs, before anything is up."""
    runner.invoke(cli_mod.cli, ["gen-token"])

    assert fake_daemon.calls == []


# ── The documented commands all exist ───────────────────────────────────


def test_every_command_documented_in_the_quick_reference_exists(runner):
    """Guards against docs/CLI.md promising a command that was renamed or removed.

    Only the top-level command names are checked; options are covered by the tests
    above that assert what actually reaches the daemon.
    """
    import pathlib
    import re

    doc = pathlib.Path(__file__).resolve().parents[2] / "docs" / "CLI.md"
    if not doc.is_file():  # pragma: no cover - source checkout only
        pytest.skip("docs/CLI.md not present in this layout")

    block = re.search(r"```\ncloud-drive-sync \[OPTIONS\] COMMAND\n(.*?)```", doc.read_text(encoding="utf-8"), re.S)
    assert block, "the quick-reference block in docs/CLI.md has moved"

    documented = set()
    for line in block.group(1).splitlines():
        line = line.strip()
        if not line or line.endswith(":"):
            continue
        first = line.split()[0]
        if first.isalpha() or "-" in first:
            documented.add(first)

    available = set(cli_mod.cli.commands)
    missing = {c for c in documented if c not in available}
    assert not missing, f"docs/CLI.md documents commands that do not exist: {sorted(missing)}"
