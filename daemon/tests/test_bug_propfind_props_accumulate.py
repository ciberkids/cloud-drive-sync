"""Regression tests for issue #47.

nc-py-api's ``get_propfind_properties`` did ``r = PROPFIND_PROPERTIES`` followed
by ``r += PROPFIND_LOCKING_PROPERTIES``, growing the shared module-level list by
seven entries on every call. A sync run doing many deletes produced a ~19 MB
PROPFIND body that pinned every PHP-FPM worker on the remote Nextcloud and took
its web UI down.

nc-py-api is an optional extra and is not installed in CI, so the primary tests
here rebuild the upstream module layout — including the bug — and assert that
``nc_patch`` corrects it. The fake first reproduces the runaway growth, so the
tests fail if either the reproduction or the fix stops being faithful. A second
set of checks runs against the real package wherever it is installed.
"""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from cloud_drive_sync.providers.nextcloud import nc_patch

# Trimmed copies of the real upstream constants — enough to show accumulation.
BASE_PROPS = ["d:resourcetype", "d:getetag", "d:getlastmodified", "oc:fileid"]
LOCK_PROPS = ["nc:lock", "nc:lock-owner", "nc:lock-time", "nc:lock-timeout"]

# Capabilities where files.locking is present. check_capabilities returns the
# *missing* capabilities, so an empty result takes the branch that appended.
CAPS_WITH_LOCKING = {"files": {"locking": "1.0"}}
CAPS_WITHOUT_LOCKING: dict = {"files": {}}


def _build_fake_nc_py_api() -> SimpleNamespace:
    """Recreate nc-py-api's module layout, bug included.

    The layout matters as much as the bug: ``files`` and ``files_async`` both do
    ``from ._files import get_propfind_properties``, so each ends up with its own
    name binding, and patching ``_files`` alone fixes nothing.
    """
    root = ModuleType("nc_py_api")
    misc = ModuleType("nc_py_api._misc")
    files_pkg = ModuleType("nc_py_api.files")
    _files = ModuleType("nc_py_api.files._files")
    files = ModuleType("nc_py_api.files.files")
    files_async = ModuleType("nc_py_api.files.files_async")

    def check_capabilities(capability: str, srv_capabilities: dict) -> list[str]:
        group, _, name = capability.partition(".")
        return [] if name in srv_capabilities.get(group, {}) else [capability]

    misc.check_capabilities = check_capabilities

    _files.PROPFIND_PROPERTIES = list(BASE_PROPS)
    _files.PROPFIND_LOCKING_PROPERTIES = list(LOCK_PROPS)

    def get_propfind_properties(capabilities: dict) -> list:
        """The upstream implementation, verbatim in behaviour."""
        r = _files.PROPFIND_PROPERTIES
        if not check_capabilities("files.locking", capabilities):
            r += _files.PROPFIND_LOCKING_PROPERTIES
        return r

    # One function object bound into three namespaces, as the real imports do.
    _files.get_propfind_properties = get_propfind_properties
    files.get_propfind_properties = get_propfind_properties
    files_async.get_propfind_properties = get_propfind_properties

    class FilesAPI:
        """Mirrors the parts of the real FilesAPI that build a PROPFIND."""

        def __init__(self) -> None:
            self._session = SimpleNamespace(capabilities=CAPS_WITH_LOCKING, user="alice")
            self.sent: list[list[str]] = []

        def listdir(self, path: str = "", depth: int = 1, exclude_self: bool = True) -> list:
            properties = files.get_propfind_properties(self._session.capabilities)
            return self._listdir(
                self._session.user,
                path,
                properties=properties,
                depth=depth,
                exclude_self=exclude_self,
            )

        def _listdir(self, user, path, properties, depth, exclude_self, prop_type=0) -> list:
            self.sent.append(list(properties))
            return []

    class AsyncFilesAPI:
        async def _listdir(self, user, path, properties, depth, exclude_self, prop_type=0) -> list:
            return list(properties)

    files.FilesAPI = FilesAPI
    files_async.AsyncFilesAPI = AsyncFilesAPI

    files_pkg._files = _files
    files_pkg.files = files
    files_pkg.files_async = files_async
    root.files = files_pkg
    root._misc = misc

    return SimpleNamespace(
        root=root,
        misc=misc,
        files_pkg=files_pkg,
        _files=_files,
        files=files,
        files_async=files_async,
        original_get=get_propfind_properties,
    )


@pytest.fixture
def fake_nc(monkeypatch):
    """Install the fake package tree and let nc_patch run against it."""
    fake = _build_fake_nc_py_api()

    for name, module in (
        ("nc_py_api", fake.root),
        ("nc_py_api._misc", fake.misc),
        ("nc_py_api.files", fake.files_pkg),
        ("nc_py_api.files._files", fake._files),
        ("nc_py_api.files.files", fake.files),
        ("nc_py_api.files.files_async", fake.files_async),
    ):
        monkeypatch.setitem(sys.modules, name, module)

    # Restored on teardown, so the real package stays patched as it was.
    monkeypatch.setattr(nc_patch, "_applied", False)
    return fake


# ── The bug, as reported ────────────────────────────────────────────


def test_fake_reproduces_the_unbounded_growth(fake_nc):
    """Guards the reproduction itself: without the patch the list must grow."""
    for _ in range(10):
        fake_nc.original_get(CAPS_WITH_LOCKING)

    assert len(fake_nc._files.PROPFIND_PROPERTIES) == len(BASE_PROPS) + 10 * len(LOCK_PROPS)
    assert fake_nc._files.PROPFIND_PROPERTIES.count("nc:lock-timeout") == 10


# ── The fix ─────────────────────────────────────────────────────────


def test_patch_replaces_every_namespace_binding(fake_nc):
    """Patching ``_files`` alone would leave all 11 listdir() call sites buggy.

    find()/by_id() resolve the name in ``_files``' globals while listdir()
    resolves it in ``files``', so all three bindings have to be replaced.
    """
    nc_patch.apply()

    for module in (fake_nc._files, fake_nc.files, fake_nc.files_async):
        assert module.get_propfind_properties is nc_patch._get_propfind_properties, (
            f"{module.__name__} still holds the unpatched get_propfind_properties"
        )


def test_repeated_calls_do_not_grow_the_shared_list(fake_nc):
    nc_patch.apply()
    before = list(fake_nc._files.PROPFIND_PROPERTIES)

    for _ in range(50):
        fake_nc._files.get_propfind_properties(CAPS_WITH_LOCKING)

    assert fake_nc._files.PROPFIND_PROPERTIES == before


def test_listdir_property_count_stays_constant_across_calls(fake_nc):
    """End-to-end over the entry point all 11 of our call sites reach.

    ``NextcloudClient`` hits ``listdir()`` during path resolution, upload, move
    and delete; before the fix each call enlarged every subsequent body.
    """
    nc_patch.apply()
    api = fake_nc.files.FilesAPI()

    for _ in range(20):
        api.listdir("/Docs")

    assert len(api.sent) == 20
    assert all(props == api.sent[0] for props in api.sent), "property list grew between calls"
    assert len(api.sent[0]) == len(set(api.sent[0]))


def test_returned_list_still_carries_the_locking_properties(fake_nc):
    """The fix must not change what is requested, only stop the mutation."""
    nc_patch.apply()

    props = fake_nc._files.get_propfind_properties(CAPS_WITH_LOCKING)

    assert props == BASE_PROPS + LOCK_PROPS


def test_locking_properties_omitted_when_server_lacks_the_capability(fake_nc):
    nc_patch.apply()

    props = fake_nc._files.get_propfind_properties(CAPS_WITHOUT_LOCKING)

    assert props == BASE_PROPS
    assert fake_nc._files.PROPFIND_PROPERTIES == BASE_PROPS


def test_apply_is_idempotent(fake_nc):
    """Imported by the provider package; must not stack wrappers."""
    nc_patch.apply()
    guarded = fake_nc.files.FilesAPI._listdir

    nc_patch._applied = False
    nc_patch.apply()

    assert fake_nc.files.FilesAPI._listdir is guarded


def test_apply_heals_a_list_polluted_before_patching(fake_nc):
    """Something may reach nc-py-api before us; prior growth is repaired."""
    for _ in range(5):
        fake_nc.original_get(CAPS_WITH_LOCKING)
    assert len(fake_nc._files.PROPFIND_PROPERTIES) > len(BASE_PROPS)

    nc_patch.apply()

    assert fake_nc._files.PROPFIND_PROPERTIES == BASE_PROPS + LOCK_PROPS


def test_guard_wraps_both_listdir_implementations(fake_nc):
    nc_patch.apply()

    assert getattr(fake_nc.files.FilesAPI._listdir, "_cds_guarded", False)
    assert getattr(fake_nc.files_async.AsyncFilesAPI._listdir, "_cds_guarded", False)


def test_guard_strips_duplicates_from_a_polluted_list(fake_nc):
    """Defence in depth: a leak through some other upstream path is still capped."""
    nc_patch.apply()
    api = fake_nc.files.FilesAPI()

    api._listdir("alice", "/Docs", properties=BASE_PROPS * 4, depth=1, exclude_self=True)

    assert api.sent == [BASE_PROPS]


def test_apply_is_a_no_op_without_nc_py_api(monkeypatch):
    """``pyproject.toml`` pins only ``nc-py-api>=0.17.0``; import must never fail."""
    for name in ("nc_py_api", "nc_py_api.files", "nc_py_api.files._files"):
        monkeypatch.setitem(sys.modules, name, None)
    monkeypatch.setattr(nc_patch, "_applied", False)

    nc_patch.apply()  # must not raise

    assert nc_patch._applied is False


def test_apply_survives_a_renamed_listdir(fake_nc, caplog):
    """A future upstream rename must cost us the guard, not the whole provider.

    Raising out of ``apply()`` would propagate through
    ``providers/nextcloud/__init__.py`` and disable Nextcloud entirely — a worse
    outcome than the bug being patched.
    """
    del fake_nc.files.FilesAPI._listdir

    with caplog.at_level("WARNING"):
        nc_patch.apply()  # must not raise

    assert "Could not guard" in caplog.text
    # The actual fix is still in place.
    assert fake_nc.files.get_propfind_properties is nc_patch._get_propfind_properties
    before = list(fake_nc._files.PROPFIND_PROPERTIES)
    fake_nc._files.get_propfind_properties(CAPS_WITH_LOCKING)
    assert fake_nc._files.PROPFIND_PROPERTIES == before


def test_apply_still_patches_what_it_can_when_a_module_disappears(fake_nc, monkeypatch):
    """Losing ``files_async`` must not cost us the fix on the sync path we use."""
    monkeypatch.setitem(sys.modules, "nc_py_api.files.files_async", None)

    nc_patch.apply()

    assert fake_nc._files.get_propfind_properties is nc_patch._get_propfind_properties
    assert fake_nc.files.get_propfind_properties is nc_patch._get_propfind_properties
    assert getattr(fake_nc.files.FilesAPI._listdir, "_cds_guarded", False)


# ── The property-list guard ─────────────────────────────────────────


def test_guard_strips_duplicates_passed_positionally():
    """Our change poller passes the property list positionally."""
    args, _ = nc_patch._sanitize_call(("alice", "/Docs", ["d:getetag", "d:getetag", "oc:fileid"], 1, True), {})

    assert args[2] == ["d:getetag", "oc:fileid"]


def test_guard_strips_duplicates_passed_as_keyword():
    """nc-py-api itself passes the property list as a keyword argument."""
    args, kwargs = nc_patch._sanitize_call(
        ("alice", "/Docs"), {"properties": ["d:getetag", "oc:fileid", "oc:fileid"], "depth": 1}
    )

    assert kwargs["properties"] == ["d:getetag", "oc:fileid"]
    assert kwargs["depth"] == 1
    assert args == ("alice", "/Docs")


def test_guard_refuses_an_implausibly_large_property_set():
    """Backstop: never send the body that took the server down."""
    props = [f"nc:prop-{i}" for i in range(nc_patch.MAX_PROPFIND_PROPERTIES + 1)]

    with pytest.raises(nc_patch.PropfindPropertyOverflow):
        nc_patch._sanitize_properties(props, "/Docs")


def test_guard_logs_when_duplicates_are_seen(caplog):
    """Duplicates mean the correction has stopped covering some code path."""
    with caplog.at_level("ERROR"):
        nc_patch._sanitize_properties(["d:getetag", "d:getetag"], "/Docs")

    assert "issue #47" in caplog.text


# ── Against the real package, where it is installed ─────────────────


def test_real_nc_py_api_is_patched_by_provider_import():
    """The provider package applies the correction at import time."""
    pytest.importorskip("nc_py_api", reason="nextcloud extra not installed")
    from nc_py_api.files import _files, files, files_async

    import cloud_drive_sync.providers.nextcloud  # noqa: F401

    for module in (_files, files, files_async):
        assert module.get_propfind_properties is nc_patch._get_propfind_properties

    before = list(_files.PROPFIND_PROPERTIES)
    for _ in range(25):
        files.get_propfind_properties({"files": {"locking": "1.0"}})
    assert _files.PROPFIND_PROPERTIES == before
