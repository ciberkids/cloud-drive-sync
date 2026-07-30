"""Tests for encrypted credential storage.

``auth/credentials.py`` had no coverage at all, which is a poor place for a gap:
it holds the OAuth tokens that grant full access to a user's cloud account.

The defect these were written to catch is that both files it writes — the
ciphertext and the salt — landed at ``0644``. That matters because of how the key
is derived. It is PBKDF2 over ``/etc/machine-id`` with that salt, and
``/etc/machine-id`` is ``0444`` on a normal Linux system. So every input to the
key was readable by every local user: read the salt, read the machine id, derive
the key, decrypt the tokens. Encryption at rest that any local account can undo
is not protecting much.

It reads as an oversight rather than a decision, because other paths already did it
— ``providers/box/auth.py``, ``providers/onedrive/auth.py`` and
``providers/nextcloud/auth.py`` write ``0600``, and ``ipc/server.py`` chmods the
socket.

Auditing the rest afterwards found the same bug in ``providers/dropbox/auth.py``,
which encrypted and then wrote with a bare ``write_bytes``; and that OneDrive, Box
and Nextcloud store credentials as **plaintext JSON**, so their file mode is the
only protection they have (issue #57). All five now go through ``_write_private``.

That is the reason for the per-provider tests at the bottom. "The credential file
is locked down" was originally checked by reading the code provider by provider,
and reading the code got it wrong — so these call each real save path and inspect
the real file instead.

Permission assertions are POSIX-only; Windows does not implement these bits and a
correct implementation would fail them.
"""

from __future__ import annotations

import json
import os
import sys

import pytest
from cryptography.fernet import Fernet
from google.oauth2.credentials import Credentials

from cloud_drive_sync.auth import credentials as C

posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX permission bits are not meaningful on Windows"
)


def _creds(token: str = "access-token") -> Credentials:
    return Credentials(
        token=token,
        refresh_token="refresh-token",
        token_uri="https://oauth2.example/token",
        client_id="client-id",
        client_secret="client-secret",
        scopes=["https://www.googleapis.com/auth/drive"],
    )


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point the credential store at a temp directory.

    ``data_dir`` is patched where ``credentials`` imported it, so the salt lands in
    the temp tree too — otherwise a test run would read, and possibly create, the
    real user's salt.
    """
    monkeypatch.setattr(C, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(C, "credentials_path", lambda: tmp_path / "credentials.enc")
    # A fixed machine id keeps the test independent of the host, and of whether
    # /etc/machine-id exists at all (it does not, inside the Docker image).
    monkeypatch.setattr(C, "_get_machine_id", lambda: b"test-machine-id")
    return tmp_path


# ── Permissions: the regression guard ───────────────────────────────────


@posix_only
def test_the_credentials_file_is_not_readable_by_other_users(store):
    path = store / "credentials.enc"

    C.save_credentials(_creds(), path)

    mode = path.stat().st_mode & 0o777
    assert mode == 0o600, (
        f"credentials are {oct(mode)}; any local user can read the ciphertext"
    )


@posix_only
def test_the_salt_is_not_readable_by_other_users(store):
    """The salt is the one key input an attacker cannot get elsewhere.

    ``/etc/machine-id`` is world-readable by design, so a readable salt hands over
    everything needed to derive the key.
    """
    C.save_credentials(_creds(), store / "credentials.enc")

    mode = C._salt_path().stat().st_mode & 0o777
    assert mode == 0o600, f"the salt is {oct(mode)}; the derived key is guessable"


@posix_only
def test_the_files_are_never_briefly_world_readable(store, monkeypatch):
    """Writing then chmod-ing leaves a window where the ciphertext is exposed.

    Asserted by watching the mode at the moment content is written, rather than
    after the fact — an after-the-fact check passes for an implementation that
    creates the file 0644 and tightens it a moment later.
    """
    seen: list[tuple[str, int]] = []
    real_write = type(store / "x").write_bytes

    def _spy(self, data):
        result = real_write(self, data)
        if self.name in ("credentials.enc", C._SALT_FILE):
            seen.append((self.name, self.stat().st_mode & 0o777))
        return result

    monkeypatch.setattr(type(store / "x"), "write_bytes", _spy)

    C.save_credentials(_creds(), store / "credentials.enc")

    assert seen, "neither file was written — the spy did not attach"
    for name, mode in seen:
        assert mode == 0o600, (
            f"{name} held content at {oct(mode)} before being tightened"
        )


@posix_only
def test_an_existing_loose_credentials_file_is_tightened(store):
    """Upgrades matter more than fresh installs here: anyone who authenticated
    before the fix already has a 0644 file on disk, and it stays exposed unless
    saving repairs it."""
    path = store / "credentials.enc"
    path.write_bytes(b"stale")
    path.chmod(0o644)

    C.save_credentials(_creds(), path)

    assert path.stat().st_mode & 0o777 == 0o600


@posix_only
def test_an_existing_loose_salt_is_tightened(store):
    salt_path = store / C._SALT_FILE
    salt_path.write_bytes(os.urandom(16))
    salt_path.chmod(0o644)

    C.save_credentials(_creds(), store / "credentials.enc")

    assert salt_path.stat().st_mode & 0o777 == 0o600


@posix_only
def test_a_token_refresh_repairs_a_pre_fix_file(store, monkeypatch):
    """The mechanism that actually fixes existing installs.

    Refreshing an expired token re-saves the credentials, which routes through
    ``_write_private`` and tightens both files. Since Google access tokens last
    about an hour, a running daemon repairs itself without anyone intervening —
    which is why there is no permission sweep at startup. That would mean chmod-ing
    files the daemon may no longer own after a PUID change, and a failure there
    would crash startup, trading a bounded exposure for an unbounded outage.

    Pinned because it is load-bearing for that decision: if the refresh path ever
    stopped re-saving, pre-fix files would silently stay world-readable forever.
    """
    from unittest import mock

    path = store / "credentials.enc"
    C.save_credentials(_creds("old"), path)
    path.chmod(0o644)
    C._salt_path().chmod(0o644)

    def _refresh(self, request):
        self.token = "refreshed"

    with mock.patch.object(Credentials, "expired", True), \
         mock.patch.object(Credentials, "refresh", _refresh):
        loaded = C.load_credentials(path)

    assert loaded.token == "refreshed"
    assert path.stat().st_mode & 0o777 == 0o600
    assert C._salt_path().stat().st_mode & 0o777 == 0o600


@posix_only
def test_a_valid_token_does_not_rewrite_the_file(store):
    """The other half, stated so the exposure window is not a surprise.

    No refresh means no re-save, so a pre-fix file keeps its mode until the token
    expires. That bounds the window at roughly one token lifetime of daemon
    runtime, rather than closing it instantly.
    """
    from unittest import mock

    path = store / "credentials.enc"
    C.save_credentials(_creds(), path)
    path.chmod(0o644)

    with mock.patch.object(Credentials, "expired", False):
        assert C.load_credentials(path) is not None

    assert path.stat().st_mode & 0o777 == 0o644, (
        "a valid token now triggers a rewrite — if that is intended, the exposure "
        "window closes sooner and this test should be updated to expect 0o600"
    )


# ── Round trip ──────────────────────────────────────────────────────────


def test_credentials_survive_a_save_and_load(store):
    path = store / "credentials.enc"
    C.save_credentials(_creds("the-token"), path)

    loaded = C.load_credentials(path)

    assert loaded is not None
    assert loaded.token == "the-token"
    assert loaded.refresh_token == "refresh-token"
    assert loaded.client_id == "client-id"
    assert loaded.scopes == ["https://www.googleapis.com/auth/drive"]


def test_the_stored_file_is_not_plaintext(store):
    """The tokens must not be recoverable by reading the file."""
    path = store / "credentials.enc"
    C.save_credentials(_creds("super-secret-token"), path)

    raw = path.read_bytes()

    assert b"super-secret-token" not in raw
    assert b"refresh-token" not in raw
    with pytest.raises(Exception):
        json.loads(raw)


def test_the_salt_is_reused_rather_than_regenerated(store):
    """A new salt on every save would make the previous file undecryptable."""
    path = store / "credentials.enc"
    C.save_credentials(_creds(), path)
    first = C._salt_path().read_bytes()

    C.save_credentials(_creds("second"), path)

    assert C._salt_path().read_bytes() == first
    assert C.load_credentials(path).token == "second"


def test_each_account_gets_its_own_file(store, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "cloud_drive_sync.util.paths.data_dir", lambda: tmp_path
    )
    C.save_account_credentials(_creds("a-token"), "alice@example.com")
    C.save_account_credentials(_creds("b-token"), "bob@example.com")

    assert C.load_account_credentials("alice@example.com").token == "a-token"
    assert C.load_account_credentials("bob@example.com").token == "b-token"


@posix_only
def test_per_account_credentials_are_also_locked_down(store, monkeypatch, tmp_path):
    """The account-scoped path delegates to save_credentials, so it should inherit
    the permissions — asserted rather than assumed, since it is the path the UI
    actually uses."""
    monkeypatch.setattr("cloud_drive_sync.util.paths.data_dir", lambda: tmp_path)

    C.save_account_credentials(_creds(), "alice@example.com")

    from cloud_drive_sync.util.paths import account_credentials_path

    mode = account_credentials_path("alice@example.com").stat().st_mode & 0o777
    assert mode == 0o600


# ── Failure modes: every one of these must return None, not raise ───────


def test_no_credentials_file_returns_none(store):
    assert C.load_credentials(store / "absent.enc") is None


def test_a_missing_salt_returns_none_and_warns(store, caplog):
    """The container-recreated case: the volume holds the ciphertext but the salt
    was lost. Re-authenticating is the right outcome; crashing on startup is not.
    """
    path = store / "credentials.enc"
    C.save_credentials(_creds(), path)
    C._salt_path().unlink()

    with caplog.at_level("WARNING"):
        assert C.load_credentials(path) is None

    assert "Salt file missing" in caplog.text


def test_a_wrong_salt_returns_none(store):
    """Restoring a backup of the ciphertext against a different salt."""
    path = store / "credentials.enc"
    C.save_credentials(_creds(), path)
    C._salt_path().write_bytes(os.urandom(16))

    assert C.load_credentials(path) is None


def test_corrupt_ciphertext_returns_none(store):
    path = store / "credentials.enc"
    C.save_credentials(_creds(), path)
    path.write_bytes(b"not a fernet token at all")

    assert C.load_credentials(path) is None


def test_a_truncated_file_returns_none(store):
    path = store / "credentials.enc"
    C.save_credentials(_creds(), path)
    path.write_bytes(path.read_bytes()[:20])

    assert C.load_credentials(path) is None


def test_a_valid_token_encrypting_the_wrong_shape_returns_none(store):
    """Decryptable but structurally wrong — a KeyError here would surface as a
    crash rather than a prompt to re-authenticate."""
    path = store / "credentials.enc"
    C.save_credentials(_creds(), path)
    fernet = C._get_fernet(C._salt_path().read_bytes())
    path.write_bytes(fernet.encrypt(json.dumps({"unexpected": "shape"}).encode()))

    with pytest.raises(KeyError):
        C.load_credentials(path)


def test_a_different_machine_cannot_decrypt(store, monkeypatch):
    """The stated purpose of binding the key to the machine id: copying the file
    to another machine should not be enough."""
    path = store / "credentials.enc"
    C.save_credentials(_creds(), path)

    monkeypatch.setattr(C, "_get_machine_id", lambda: b"a-different-machine")

    assert C.load_credentials(path) is None


# ── The machine id ──────────────────────────────────────────────────────


def test_the_machine_id_falls_back_when_there_is_no_machine_id_file(monkeypatch):
    """This is the path every container takes.

    The Docker image ships no ``/etc/machine-id``, so the fallback constant is what
    derives the key there. Pinned because a future change that raised instead would
    lock every container user out of their own stored credentials on upgrade.
    """
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(C.Path, "exists", lambda self: False)

    assert C._get_machine_id() == b"cloud-drive-sync-default-key"


def test_the_machine_id_is_read_from_etc_machine_id(monkeypatch, tmp_path):
    fake = tmp_path / "machine-id"
    fake.write_text("abc123\n")

    real_path_cls = C.Path

    def _path(arg):
        return fake if str(arg) == "/etc/machine-id" else real_path_cls(arg)

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(C, "Path", _path)

    assert C._get_machine_id() == b"abc123", "trailing newline should be stripped"


def test_the_derived_key_is_a_usable_fernet_key(store):
    """Guards the base64/length contract between PBKDF2 and Fernet."""
    fernet = C._get_fernet(b"0123456789abcdef")

    assert isinstance(fernet, Fernet)
    assert fernet.decrypt(fernet.encrypt(b"payload")) == b"payload"


def test_the_same_salt_and_machine_give_the_same_key(store):
    salt = b"0123456789abcdef"
    a = C._get_fernet(salt)
    b = C._get_fernet(salt)

    assert b.decrypt(a.encrypt(b"payload")) == b"payload"


# ── Every provider, not just the shared store ───────────────────────────
#
# The Google path was fixed first, and an audit of the others then found Dropbox
# carrying the identical bug and three providers storing credentials in plaintext
# (issue #57). The lesson is that "the credential file is locked down" was checked
# per-provider by reading code, and reading code got it wrong. So this asserts it
# for all five by calling the real save path and inspecting the real file.


def _provider_savers(tmp_path, monkeypatch):
    """Yield ``(name, save_callable, path_callable)`` for each provider.

    Each provider keeps its credentials somewhere different — a module constant, a
    method, or the shared store — so the layout is spelled out rather than guessed.
    """
    monkeypatch.setattr(C, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(C, "_get_machine_id", lambda: b"test-machine-id")
    monkeypatch.setattr("cloud_drive_sync.util.paths.data_dir", lambda: tmp_path)

    cases = []

    # Google — through the shared per-account store.
    def _save_google():
        C.save_account_credentials(_creds(), "acct@example.com")

    from cloud_drive_sync.util.paths import account_credentials_path

    cases.append(("gdrive", _save_google, lambda: account_credentials_path("acct@example.com")))

    # Dropbox — encrypted, but was written with a bare write_bytes.
    from cloud_drive_sync.providers.dropbox.auth import DropboxAuth

    dbx_path = tmp_path / "dropbox_acct.enc"
    dbx = DropboxAuth.__new__(DropboxAuth)
    monkeypatch.setattr(dbx, "_credentials_path", lambda aid: dbx_path, raising=False)
    cases.append((
        "dropbox",
        lambda: dbx.save_credentials({"refresh_token": "r", "app_key": "k"}, "acct"),
        lambda: dbx_path,
    ))

    # OneDrive and Box — plaintext JSON at a module-level directory.
    from cloud_drive_sync.providers.box import auth as box_auth
    from cloud_drive_sync.providers.onedrive import auth as od_auth

    monkeypatch.setattr(od_auth, "CREDS_DIR", tmp_path / "od")
    od = od_auth.OneDriveAuth.__new__(od_auth.OneDriveAuth)
    cases.append((
        "onedrive",
        lambda: od.save_credentials({"token": "t", "client_id": "c"}, "acct"),
        lambda: tmp_path / "od" / "onedrive_acct.json",
    ))

    monkeypatch.setattr(box_auth, "_BOX_CREDENTIALS_DIR", tmp_path / "box")
    box = box_auth.BoxAuth.__new__(box_auth.BoxAuth)
    cases.append((
        "box",
        lambda: box.save_credentials({"access_token": "a", "refresh_token": "r"}, "acct"),
        lambda: tmp_path / "box" / "acct.json",
    ))

    from cloud_drive_sync.providers.nextcloud import auth as nc_auth

    monkeypatch.setattr(nc_auth, "_CREDS_DIR", tmp_path / "nc")
    nc = nc_auth.NextcloudAuth.__new__(nc_auth.NextcloudAuth)
    cases.append((
        "nextcloud",
        lambda: nc.save_credentials(
            {"server_url": "u", "username": "x", "app_password": "p"}, "acct"
        ),
        lambda: tmp_path / "nc" / "acct" / "nextcloud_creds.json",
    ))

    return cases


@posix_only
@pytest.mark.parametrize("index", range(5))
def test_every_provider_writes_credentials_owner_only(tmp_path, monkeypatch, index):
    name, save, path_of = _provider_savers(tmp_path, monkeypatch)[index]

    save()
    path = path_of()

    assert path.exists(), f"{name}: nothing was written to {path}"
    mode = path.stat().st_mode & 0o777
    assert mode == 0o600, f"{name} credentials are {oct(mode)}"


@posix_only
@pytest.mark.parametrize("index", range(5))
def test_every_provider_repairs_an_existing_loose_file(tmp_path, monkeypatch, index):
    """Upgrades, for each provider. Anyone who authenticated before the fix has a
    loose file already, and it only gets repaired if saving resets the mode rather
    than leaving an existing file alone."""
    name, save, path_of = _provider_savers(tmp_path, monkeypatch)[index]

    save()
    path = path_of()
    path.chmod(0o644)

    save()

    mode = path.stat().st_mode & 0o777
    assert mode == 0o600, f"{name}: a pre-existing 0644 file stayed {oct(mode)}"


# ── Encryption for the three plaintext providers (issue #57) ────────────
#
# OneDrive, Box and Nextcloud stored credentials as plaintext JSON. Encrypting them
# needs a read-side upgrade, because for these providers ``save_credentials`` only
# runs when an account is added — nothing rewrites the file afterwards, so a
# migration hung off saving would never fire for an existing user.
#
# The salt for these lives *beside* each credential file rather than in the shared
# data directory. They are stored under the config directory, and in a container
# that is a different volume from the data directory — so a shared salt would mean
# the ciphertext and its only key are in separate volumes. Restoring just the config
# volume would then mint a fresh salt (``_ensure_salt`` creates one rather than
# failing) and every account would be silently unrecoverable. Today that restore
# works, because the files are plaintext.


def _encrypted_providers(tmp_path, monkeypatch):
    """``(name, save, load, path, salt_path)`` for each newly-encrypted provider."""
    monkeypatch.setattr(C, "_get_machine_id", lambda: b"test-machine-id")

    from cloud_drive_sync.providers.box import auth as bx
    from cloud_drive_sync.providers.nextcloud import auth as nc
    from cloud_drive_sync.providers.onedrive import auth as od

    monkeypatch.setattr(od, "CREDS_DIR", tmp_path / "od")
    monkeypatch.setattr(bx, "_BOX_CREDENTIALS_DIR", tmp_path / "bx")
    monkeypatch.setattr(nc, "_CREDS_DIR", tmp_path / "nc")

    odi = od.OneDriveAuth.__new__(od.OneDriveAuth)
    bxi = bx.BoxAuth.__new__(bx.BoxAuth)
    nci = nc.NextcloudAuth.__new__(nc.NextcloudAuth)

    return [
        (
            "onedrive",
            {"token": "SECRET-OD", "client_id": "c"},
            odi.save_credentials,
            odi.load_credentials,
            tmp_path / "od" / "onedrive_acct.json",
            tmp_path / "od" / "onedrive_acct.salt",
        ),
        (
            "box",
            {"access_token": "SECRET-BX", "refresh_token": "r"},
            bxi.save_credentials,
            bxi.load_credentials,
            tmp_path / "bx" / "acct.json",
            tmp_path / "bx" / "acct.salt",
        ),
        (
            "nextcloud",
            {"server_url": "https://nc", "username": "u", "app_password": "SECRET-NC"},
            nci.save_credentials,
            nci.load_credentials,
            tmp_path / "nc" / "acct" / "nextcloud_creds.json",
            tmp_path / "nc" / "acct" / "nextcloud_creds.salt",
        ),
    ]


@pytest.mark.parametrize("index", range(3))
def test_the_secret_is_not_readable_in_the_file(tmp_path, monkeypatch, index):
    """The point of #57. These were `json.dumps(creds)` straight to disk."""
    name, creds, save, _load, path, _salt = _encrypted_providers(tmp_path, monkeypatch)[index]
    secret = next(v for v in creds.values() if v.startswith("SECRET"))

    save(creds, "acct")

    assert secret.encode() not in path.read_bytes(), f"{name}: credential is plaintext"


@pytest.mark.parametrize("index", range(3))
def test_credentials_round_trip_through_encryption(tmp_path, monkeypatch, index):
    _name, creds, save, load, _path, _salt = _encrypted_providers(tmp_path, monkeypatch)[index]

    save(creds, "acct")

    assert load("acct") == creds


@pytest.mark.parametrize("index", range(3))
def test_an_existing_plaintext_file_still_loads(tmp_path, monkeypatch, index):
    """The upgrade path. Anyone with an account already has a plaintext file, and
    refusing it would silently disconnect them."""
    _name, creds, _save, load, path, _salt = _encrypted_providers(tmp_path, monkeypatch)[index]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(creds))

    assert load("acct") == creds


@pytest.mark.parametrize("index", range(3))
def test_reading_a_plaintext_file_encrypts_it(tmp_path, monkeypatch, index):
    """Migration happens on read, because nothing else rewrites these files."""
    name, creds, _save, load, path, _salt = _encrypted_providers(tmp_path, monkeypatch)[index]
    secret = next(v for v in creds.values() if v.startswith("SECRET"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(creds))

    load("acct")

    assert secret.encode() not in path.read_bytes(), f"{name}: still plaintext after read"
    assert load("acct") == creds, "the re-encrypted file does not load back"


@posix_only
@pytest.mark.parametrize("index", range(3))
def test_the_upgraded_file_and_its_salt_are_owner_only(tmp_path, monkeypatch, index):
    _name, creds, _save, load, path, salt = _encrypted_providers(tmp_path, monkeypatch)[index]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(creds))
    path.chmod(0o644)

    load("acct")

    assert path.stat().st_mode & 0o777 == 0o600
    assert salt.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("index", range(3))
def test_the_salt_sits_beside_the_credentials_not_in_the_data_dir(
    tmp_path, monkeypatch, index
):
    """The cross-volume guard.

    These credentials live under the config directory; the shared salt lives under
    the data directory, which is a separate Docker volume. If the key came from that
    shared salt, restoring only the config volume would mint a fresh salt and lose
    every account. So each credential file carries its own salt.
    """
    name, creds, save, _load, _path, salt = _encrypted_providers(tmp_path, monkeypatch)[index]
    shared = tmp_path / "elsewhere" / "token_salt"
    monkeypatch.setattr(C, "_salt_path", lambda: shared)

    save(creds, "acct")

    assert salt.exists(), f"{name}: no salt beside the credentials"
    assert not shared.exists(), f"{name}: fell back to the shared data-dir salt"


@pytest.mark.parametrize("index", range(3))
def test_credentials_survive_a_config_only_restore(tmp_path, monkeypatch, index):
    """The scenario the sibling salt exists for: the data directory is gone."""
    _name, creds, save, load, _path, _salt = _encrypted_providers(tmp_path, monkeypatch)[index]
    save(creds, "acct")

    monkeypatch.setattr(C, "_salt_path", lambda: tmp_path / "wiped" / "token_salt")

    assert load("acct") == creds


@pytest.mark.parametrize("index", range(3))
def test_a_missing_salt_is_reported_loudly(tmp_path, monkeypatch, index, caplog):
    """Otherwise "all my accounts disappeared" has no diagnostic."""
    _name, creds, save, load, _path, salt = _encrypted_providers(tmp_path, monkeypatch)[index]
    save(creds, "acct")
    salt.unlink()

    with caplog.at_level("ERROR"):
        assert load("acct") is None

    assert "salt" in caplog.text.lower()


@pytest.mark.parametrize("index", range(3))
def test_a_failed_upgrade_still_returns_the_credentials(tmp_path, monkeypatch, index):
    """A read-only filesystem must not take every account offline.

    Same trade as declining a permission sweep at startup: the credentials were read
    successfully, so a failure to rewrite them is cosmetic and must not propagate.
    """
    _name, creds, _save, load, path, _salt = _encrypted_providers(tmp_path, monkeypatch)[index]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(creds))

    def _boom(*a, **kw):
        raise OSError("read-only file system")

    monkeypatch.setattr(C, "write_encrypted_json", _boom)

    assert load("acct") == creds


@pytest.mark.parametrize("index", range(3))
def test_corrupt_credentials_return_none(tmp_path, monkeypatch, index):
    _name, creds, save, load, path, _salt = _encrypted_providers(tmp_path, monkeypatch)[index]
    save(creds, "acct")
    path.write_bytes(b"not a fernet token")

    assert load("acct") is None


def test_nextcloud_still_rejects_an_incomplete_credential_set(tmp_path, monkeypatch):
    """Nextcloud validated its fields and returned None when they were missing.

    Routing through a shared reader must not drop that: without it a truncated file
    becomes a KeyError inside create_client instead of a clean None.
    """
    from cloud_drive_sync.providers.nextcloud import auth as nc

    monkeypatch.setattr(nc, "_CREDS_DIR", tmp_path / "nc")
    monkeypatch.setattr(C, "_get_machine_id", lambda: b"test-machine-id")
    inst = nc.NextcloudAuth.__new__(nc.NextcloudAuth)

    inst.save_credentials({"server_url": "https://nc", "username": "u"}, "acct")

    assert inst.load_credentials("acct") is None
