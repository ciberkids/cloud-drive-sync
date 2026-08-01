"""Tests for daemon startup, the pid file, and per-account isolation.

``daemon.py`` was 20% covered, and it is where the `PUID` crash hid across several
releases — a startup failure no unit test could see. The `docker-smoke` CI job now
boots the container; this covers the startup *variants* a container run does not
exercise.

The pid-file tests are the important ones, because liveness was being confused with
identity. ``is_running`` read a number and asked "is something alive with this pid?",
which is the wrong question in two situations that both happen:

* **In a container the daemon is PID 1.** A pid file left by an unclean stop says
  ``1``. On the next start ``os.kill(1, 0)`` succeeds — it is asking about *itself* —
  so every start refuses with "Daemon is already running". The run directory is a
  named volume, so the file survives restarts and a restart policy loops forever on a
  daemon that never starts.
* **Pids get reused.** After an unclean death the number may belong to something else,
  and ``stop`` would SIGTERM an unrelated process.

Objects are constructed and single methods called. ``run()`` is never driven end to
end — it opens databases, binds sockets and installs signal handlers, and a test that
tried would be testing the harness.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from cloud_drive_sync.config import Account, Config
from cloud_drive_sync.daemon import Daemon

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process semantics")
linux_only = pytest.mark.skipif(
    sys.platform != "linux", reason="identity check uses /proc, which is Linux-only"
)


@pytest.fixture
def pid_file(tmp_path, monkeypatch):
    """Point the pid path at a temp file, in both places it is looked up."""
    import cloud_drive_sync.daemon as daemon_mod
    from cloud_drive_sync.util import paths

    path = tmp_path / "cloud-drive-sync.pid"
    monkeypatch.setattr(daemon_mod, "pid_path", lambda: path)
    monkeypatch.setattr(paths, "pid_path", lambda: path)
    return path


# ── The pid file: identity, not just liveness ───────────────────────────


def test_no_pid_file_means_not_running(pid_file):
    assert Daemon.is_running() is False


@linux_only
def test_pid_one_does_not_read_as_already_running(pid_file):
    """The container lockout. PID 1 is alive by definition — it is the daemon asking
    about itself — so believing the file made every restart refuse to start."""
    pid_file.write_text("1")

    assert Daemon.is_running() is False, "a leftover pid of 1 still blocks startup"


@linux_only
def test_our_own_pid_does_not_read_as_already_running(pid_file):
    pid_file.write_text(str(os.getpid()))

    assert Daemon.is_running() is False


@linux_only
def test_a_stale_pid_file_is_discarded_so_it_cannot_block_forever(pid_file):
    pid_file.write_text("1")

    Daemon.is_running()

    assert not pid_file.exists()


@pytest.mark.parametrize("content", ["", "   ", "not-a-number", "12.5", "-3"])
def test_a_corrupt_pid_file_is_treated_as_stale(pid_file, content):
    pid_file.write_text(content)

    assert Daemon.is_running() is False


@posix_only
def test_a_pid_that_does_not_exist_is_treated_as_stale(pid_file):
    # A pid this high is not in use; if it somehow were, the identity check rejects it
    # anyway, so the assertion holds either way.
    pid_file.write_text("4194303")

    assert Daemon.is_running() is False


@posix_only
def test_an_unremovable_stale_pid_file_does_not_raise(pid_file, tmp_path):
    """``unlink`` used to raise straight out of ``is_running``, so ``status`` and
    ``start`` both died with a bare PermissionError when the runtime directory was
    read-only — a common state after the container's PUID remap. A pid file that
    cannot be deleted must not stop the daemon from starting.
    """
    pid_file.write_text("4194303")
    os.chmod(tmp_path, 0o500)
    try:
        assert Daemon.is_running() is False
    finally:
        os.chmod(tmp_path, 0o700)


# ── Stopping: never signal a process we cannot identify ──────────────────


def test_stopping_with_no_pid_file_reports_failure(pid_file):
    assert Daemon.stop_running() is False


@linux_only
def test_stopping_refuses_to_signal_an_unrelated_process(pid_file):
    """The reused-pid case. SIGTERM to somebody else's process is a far worse outcome
    than declining to stop, so identity is checked before signalling."""
    victim = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    pid_file.write_text(str(victim.pid))
    try:
        result = Daemon.stop_running()

        assert result is False, "signalled a process that is not the daemon"
        assert victim.poll() is None, "an unrelated process was killed"
    finally:
        victim.kill()
        victim.wait()


@linux_only
def test_stopping_refuses_pid_one(pid_file):
    """In a container this is the daemon itself, and in a normal system it is init."""
    pid_file.write_text("1")

    assert Daemon.stop_running() is False


@linux_only
def test_stopping_discards_the_pid_file_it_refused(pid_file):
    pid_file.write_text("1")

    Daemon.stop_running()

    assert not pid_file.exists(), "the unusable pid file was left to block the next stop"


@linux_only
def test_this_process_is_recognised_as_a_daemon_when_it_looks_like_one():
    """The identity check must not be so strict that it rejects the real daemon.

    A check that always said "not the daemon" would make ``stop`` never work, which is
    the opposite failure — so this pins that a cloud-drive-sync process is accepted.
    Asserted against a real child process whose command line names the package.
    """
    child = subprocess.Popen(
        [sys.executable, "-c", "import cloud_drive_sync, time; time.sleep(30)"]
    )
    try:
        assert Daemon._pid_is_this_daemon(child.pid) is True
    finally:
        child.kill()
        child.wait()


@linux_only
def test_an_ordinary_process_is_not_mistaken_for_the_daemon():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert Daemon._pid_is_this_daemon(child.pid) is False
    finally:
        child.kill()
        child.wait()


def test_pids_below_two_are_never_the_daemon():
    assert Daemon._pid_is_this_daemon(0) is False
    assert Daemon._pid_is_this_daemon(1) is False
    assert Daemon._pid_is_this_daemon(-1) is False


# ── One broken account must not stop the daemon ─────────────────────────


class _RaisingAuth:
    """A provider whose credential file cannot be read."""

    def load_credentials(self, account_id):
        raise OSError("Permission denied: credentials.enc")


class _FakeDriveClient:
    def __init__(self, creds, proxy=None) -> None:
        self.creds = creds


def _valid_creds(_email):
    return type("Creds", (), {"valid": True})()


@pytest.fixture
def daemon_with_two_accounts():
    d = Daemon()
    d._config = Config()
    d._config.accounts = [
        Account(email="broken@example.com", provider="dropbox"),
        Account(email="fine@example.com", provider="gdrive"),
    ]
    return d


async def test_a_broken_account_raises_only_for_itself(daemon_with_two_accounts, monkeypatch):
    """Only KeyError used to be caught, so an unreadable credential file, a decryption
    failure, or a provider SDK raising while building its client escaped the loop and
    aborted startup for *every* account. One damaged file meant the daemon would not
    start at all, when the right outcome is one account showing as disconnected.
    """
    from cloud_drive_sync.providers import registry

    real_get = registry.get
    monkeypatch.setattr(
        registry,
        "get",
        lambda name: (
            type("E", (), {"available": True, "auth_cls": staticmethod(_RaisingAuth)})()
            if name == "dropbox"
            else real_get(name)
        ),
    )

    d = daemon_with_two_accounts
    clients: dict = {}
    failures = []
    for account in d._config.accounts:
        try:
            await d._load_account_client(account, clients, _valid_creds, _FakeDriveClient)
        except Exception as exc:
            failures.append(account.email)
            assert isinstance(exc, OSError)

    assert failures == ["broken@example.com"]
    assert "gdrive:fine@example.com" in clients, "a broken account blocked a healthy one"


async def test_an_unknown_provider_is_skipped_not_raised(monkeypatch):
    d = Daemon()
    d._config = Config()
    account = Account(email="x@example.com", provider="not-a-provider")
    clients: dict = {}

    await d._load_account_client(account, clients, _valid_creds, _FakeDriveClient)

    assert clients == {}


async def test_an_unavailable_provider_is_skipped(monkeypatch):
    """A provider whose optional extra is missing should disable that account, not
    take the daemon down."""
    from cloud_drive_sync.providers import registry

    monkeypatch.setattr(
        registry,
        "get",
        lambda name: type("E", (), {"available": False, "auth_cls": staticmethod(_RaisingAuth)})(),
    )
    d = Daemon()
    d._config = Config()
    clients: dict = {}

    await d._load_account_client(
        Account(email="x@example.com", provider="dropbox"), clients, _valid_creds, _FakeDriveClient
    )

    assert clients == {}


async def test_an_account_with_no_credentials_is_skipped(monkeypatch):
    d = Daemon()
    d._config = Config()
    clients: dict = {}

    await d._load_account_client(
        Account(email="x@example.com", provider="gdrive"),
        clients,
        lambda _e: None,
        _FakeDriveClient,
    )

    assert clients == {}


async def test_a_healthy_google_account_is_registered(monkeypatch):
    d = Daemon()
    d._config = Config()
    clients: dict = {}

    await d._load_account_client(
        Account(email="ok@example.com", provider="gdrive"),
        clients,
        _valid_creds,
        _FakeDriveClient,
    )

    assert "gdrive:ok@example.com" in clients


# ── A failed MCP bind must not take the daemon with it ───────────────────


async def test_a_busy_mcp_port_raises_a_catchable_error():
    """uvicorn reports a failed bind by calling ``sys.exit()``.

    ``start()`` used to create the serve task and return, so the bind happened later
    inside that task — and a ``SystemExit`` raised in a background task is a
    ``BaseException`` that escapes the caller's ``except Exception`` and unwinds the
    whole daemon. A busy or privileged MCP port therefore took sync down with it,
    exactly the opposite of MCP being optional. Binding in the caller's coroutine and
    translating the exit into ``OSError`` makes it an ordinary, handled failure.
    """
    import socket

    from cloud_drive_sync.mcp import is_available

    if not is_available():
        pytest.skip("mcp extra not installed")
    from cloud_drive_sync.mcp.server import McpServer

    blocker = socket.socket()
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    try:
        server = McpServer(handler=None, host="127.0.0.1", port=port)

        with pytest.raises(OSError, match="could not bind"):
            await server.start()
    finally:
        blocker.close()


async def test_the_daemon_survives_a_failed_mcp_start(monkeypatch):
    """The caller's side of the same defect: `_start_mcp` swallows the failure and
    leaves the daemon running without MCP."""
    d = Daemon(mcp_port=8081)
    d._config = Config()

    from cloud_drive_sync.mcp import is_available

    if not is_available():
        pytest.skip("mcp extra not installed")

    import cloud_drive_sync.mcp.server as mcp_server_mod

    class _Failing:
        def __init__(self, *a, **kw) -> None:
            pass

        async def start(self):
            raise OSError("could not bind the MCP server to 0.0.0.0:8081")

    monkeypatch.setattr(mcp_server_mod, "McpServer", _Failing)

    await d._start_mcp(handler=None)  # must not raise

    assert d._mcp_server is None, "a failed MCP server was left registered"


# ── The documented paths must be the real ones ──────────────────────────


def test_the_documented_runtime_paths_match_the_code():
    """Every doc named the wrong pid and socket paths.

    `docs/CLI.md` had both the directory and the filenames wrong
    (`~/.local/run/cloud-drive-sync/daemon.pid`), and API.md, ARCHITECTURE.md and
    UI.md all omitted the `cloud-drive-sync/` subdirectory. That matters more than a
    typo usually would: these are the paths someone follows when the daemon will not
    start, which is exactly the lockout this module's other tests are about — so the
    docs sent them to a file that does not exist.

    Pinned here rather than left to a proofread, because a path is checkable.
    """
    import pathlib
    import re

    from cloud_drive_sync.util.paths import pid_path, socket_path

    docs = pathlib.Path(__file__).resolve().parents[2] / "docs"
    if not docs.is_dir():  # pragma: no cover - source checkout only
        pytest.skip("docs/ not present in this layout")

    # The trailing component the docs must name, e.g. cloud-drive-sync.pid, and the
    # parent directory it sits in.
    pid_name, sock_name = pid_path().name, socket_path().name
    parent = pid_path().parent.name

    text = "\n".join(p.read_text() for p in docs.glob("*.md"))
    stale = re.findall(r"XDG_RUNTIME_DIR/cloud-drive-sync\.(?:pid|sock)", text)
    assert not stale, f"docs name the socket/pid outside {parent}/: {set(stale)}"
    assert "local/run/cloud-drive-sync" not in text, "docs still name the old ~/.local/run path"

    for name in (pid_name, sock_name):
        assert f"{parent}/{name}" in text, f"no doc names the real path .../{parent}/{name}"


@linux_only
def test_pid_one_is_rejected_even_when_proc_says_it_is_the_daemon(monkeypatch):
    """The container case, which cannot occur naturally on a developer machine.

    Inside the image the daemon really *is* PID 1, so `/proc/1/cmdline` genuinely
    names cloud_drive_sync. The `/proc` check therefore cannot save us there — the
    `pid <= 1` guard is the one doing the work, and on a normal host it is masked
    because `/proc/1` is init. So `/proc` is stubbed to reproduce the container.
    """
    import pathlib as _pathlib

    import cloud_drive_sync.daemon as daemon_mod

    real_read = _pathlib.Path.read_bytes

    def fake_read(self, *a, **kw):
        if str(self) == "/proc/1/cmdline":
            return b"python\x00-m\x00cloud_drive_sync\x00start\x00--foreground\x00"
        return real_read(self, *a, **kw)

    monkeypatch.setattr(daemon_mod.Path, "read_bytes", fake_read)

    assert Daemon._pid_is_this_daemon(1) is False, (
        "in a container this is the daemon asking about itself, so believing it "
        "blocks every restart"
    )


async def test_the_mcp_port_is_bound_before_start_returns():
    """Eagerness is the fix, not just the error type.

    If the bind still happened inside the serve task, `start()` would return before
    anything was listening and the failure would surface later as SystemExit escaping
    into the event loop. Asserted by connecting immediately after `start()` returns.
    """
    import socket

    from cloud_drive_sync.mcp import is_available

    if not is_available():
        pytest.skip("mcp extra not installed")
    from cloud_drive_sync.mcp.server import McpServer

    # Pick a free port, release it, then let the server take it.
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    server = McpServer(handler=None, host="127.0.0.1", port=port)
    await server.start()
    try:
        # The port is claimed as soon as start() returns. Accepting connections comes
        # slightly later, when the serve task calls listen() — but the *bind* is what
        # detects a port conflict, and it having already happened is the fix.
        clash = socket.socket()
        with pytest.raises(OSError):
            clash.bind(("127.0.0.1", port))
        clash.close()
    finally:
        await server.stop()
