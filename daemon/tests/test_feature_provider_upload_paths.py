"""Tests for the Box, Dropbox and OneDrive upload paths.

These are the paths the ASYNC230 async-IO work rewrote: blocking reads that used
to happen on the event loop were moved into ``asyncio.to_thread`` or switched to
``aiofiles``. The change was argued to be behaviour-preserving and never measured,
and the providers carry no upload coverage at all — so "the bytes still arrive in
the right order" rested entirely on reading the diff.

What makes that worth testing rather than trusting is the failure mode. Every one
of these paths splits a file into chunks and reassembles it server-side. Get the
offset arithmetic wrong by one chunk and the upload still *succeeds*: no
exception, no log line, a file in the cloud that is quietly corrupt. So the
assertions here are on the reassembled bytes, not on the shape of the calls.

The SDKs are optional extras and absent from CI, so the fakes stand in for them.
Each fake is **bounded** — it refuses more chunks than the file could possibly
need. Without that, the truncation tests below would spin forever inside pytest
instead of failing, which is the same bug in a different process.

These were checked by mutation rather than by inspection: nine deliberate breaks
were introduced into the three loops. Seven fail here. The two that do not are
equivalent mutants, recorded so nobody mistakes them for gaps and contorts a test
chasing them:

* **Dropbox** ``>=`` → ``>`` in the last-chunk test. The loop then falls out on the
  following zero-length read instead, and commits with an empty chunk — one extra
  round trip, identical bytes. ``test_dropbox_never_sends_an_empty_chunk`` catches
  the wasted request, which is the only observable difference.
* **OneDrive** ``offset += len(chunk)`` → ``offset += chunk_size``. These differ
  only on a short read, and a short read means end-of-file, after which the loop
  ends regardless — via the 201 that Graph returns for the final chunk. Reachable
  only from a server that keeps answering 202 after the last byte.
"""

from __future__ import annotations

import asyncio
import hashlib
import random
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

# Chunk counts are bounded at this multiple of the expected count. Generous
# enough never to fire on correct code, small enough to fail fast on a runaway.
_RUNAWAY_LIMIT = 8


class RunawayUpload(AssertionError):
    """Raised by the fakes when a chunk loop does not terminate."""


def _write(path, size: int) -> bytes:
    """Write ``size`` position-dependent bytes.

    Position-dependent matters: with uniform filler, a chunk uploaded twice or in
    the wrong order still reassembles to the right bytes and the test passes.

    Seeded rather than random so a failure reproduces, and ``randbytes`` rather
    than a comprehension because the OneDrive cases need tens of megabytes —
    generating those a byte at a time in Python dominated the suite runtime.
    """
    data = random.Random(size).randbytes(size)
    path.write_bytes(data)
    return data


# ── Dropbox: upload sessions ────────────────────────────────────────────


class FakeWriteMode:
    overwrite = "overwrite"
    add = "add"


class FakeUploadSessionCursor:
    def __init__(self, session_id: str, offset: int) -> None:
        self.session_id = session_id
        self.offset = offset


class FakeCommitInfo:
    def __init__(self, path: str, mode: Any) -> None:
        self.path = path
        self.mode = mode


class FakeFolderMetadata:
    """Only needs to exist: ``_metadata_to_dict`` isinstance-checks against it."""


class FakeFileMetadata:
    """A real class, not a namespace — ``_metadata_to_dict`` takes the file branch
    on an isinstance check, and a namespace silently takes the folder branch."""

    def __init__(self, path: str, size: int) -> None:
        import datetime

        self.path_lower = path.lower()
        self.path_display = path
        self.name = path.rsplit("/", 1)[-1]
        self.size = size
        self.content_hash = "deadbeef"
        self.server_modified = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)


def _build_fake_dropbox_sdk() -> dict[str, ModuleType]:
    """The ``dropbox`` surface the session-upload path touches."""
    files = ModuleType("dropbox.files")
    files.WriteMode = FakeWriteMode
    files.UploadSessionCursor = FakeUploadSessionCursor
    files.CommitInfo = FakeCommitInfo
    files.FolderMetadata = FakeFolderMetadata
    files.FileMetadata = FakeFileMetadata

    root = ModuleType("dropbox")
    root.files = files
    return {"dropbox": root, "dropbox.files": files}


class FakeDbx:
    """Records every chunk a session upload sends, in order."""

    def __init__(self, max_chunks: int) -> None:
        self.chunks: list[bytes] = []
        self.offsets: list[int] = []
        self.committed: Any = None
        self._max = max_chunks

    def _record(self, chunk: bytes, offset: int) -> None:
        self.chunks.append(chunk)
        self.offsets.append(offset)
        if len(self.chunks) > self._max:
            raise RunawayUpload(
                f"the upload loop sent {len(self.chunks)} chunks; it is not terminating"
            )

    def files_upload_session_start(self, chunk):
        self._record(chunk, 0)
        return SimpleNamespace(session_id="sess-1")

    def files_upload_session_append_v2(self, chunk, cursor):
        self._record(chunk, cursor.offset)
        return None

    def files_upload_session_finish(self, chunk, cursor, commit):
        self._record(chunk, cursor.offset)
        self.committed = commit
        return FakeFileMetadata(commit.path, self.uploaded_size)

    @property
    def uploaded_size(self) -> int:
        return sum(len(c) for c in self.chunks)

    @property
    def reassembled(self) -> bytes:
        return b"".join(self.chunks)


@pytest.fixture
def dropbox_ops(monkeypatch):
    """A ``DropboxFileOps`` wired to a fake SDK, with the thresholds shrunk.

    Both constants have to move. The real session gate is 150 MB, so lowering only
    the chunk size leaves every test file on the single-request path and the
    session code — the part with the arithmetic — never runs.
    """
    for name, module in _build_fake_dropbox_sdk().items():
        monkeypatch.setitem(sys.modules, name, module)

    from cloud_drive_sync.providers.dropbox import operations as ops_mod

    def _make(*, chunk_size: int, session_threshold: int, max_chunks: int):
        monkeypatch.setattr(ops_mod, "_UPLOAD_CHUNK_SIZE", chunk_size)
        monkeypatch.setattr(ops_mod, "_SESSION_THRESHOLD", session_threshold)
        dbx = FakeDbx(max_chunks)
        client = SimpleNamespace(dbx=dbx)
        return ops_mod.DropboxFileOps(client), dbx

    return _make


CHUNK = 64


@pytest.mark.parametrize(
    "size",
    [
        CHUNK + 1,          # smallest multi-chunk file
        2 * CHUNK - 1,      # one byte short of a boundary
        2 * CHUNK,          # exactly on a boundary
        2 * CHUNK + 1,      # one byte past
        3 * CHUNK,
        3 * CHUNK + 7,
        5 * CHUNK - 1,
    ],
    ids=lambda s: f"{s}b",
)
async def test_dropbox_session_upload_reassembles_the_file_exactly(
    dropbox_ops, tmp_path, size
):
    """The assertion that matters: the bytes the server would store are the bytes
    on disk. Sizes straddle chunk boundaries, where off-by-one lives."""
    ops, dbx = dropbox_ops(chunk_size=CHUNK, session_threshold=CHUNK, max_chunks=_RUNAWAY_LIMIT * 5)
    path = tmp_path / "f.bin"
    original = _write(path, size)

    await ops.upload_file(path, "/dest")

    assert dbx.reassembled == original, (
        f"{size} bytes in, {dbx.uploaded_size} bytes out — the file would be corrupt"
    )


async def test_dropbox_session_offsets_are_contiguous(dropbox_ops, tmp_path):
    """Each chunk must be declared at the offset where the previous one ended.

    Reassembly alone would not catch a wrong cursor: Dropbox honours the offset, so
    a correct byte stream sent at wrong offsets still corrupts the stored file.
    """
    ops, dbx = dropbox_ops(chunk_size=CHUNK, session_threshold=CHUNK, max_chunks=40)
    path = tmp_path / "f.bin"
    _write(path, 4 * CHUNK + 5)

    await ops.upload_file(path, "/dest")

    expected, running = [], 0
    for chunk in dbx.chunks:
        expected.append(running)
        running += len(chunk)
    assert dbx.offsets == expected


@pytest.mark.parametrize("size", [2 * CHUNK, 3 * CHUNK], ids=["two-chunks", "three-chunks"])
async def test_dropbox_never_sends_an_empty_chunk(dropbox_ops, tmp_path, size):
    """On an exact chunk boundary the last read must be recognised as the last.

    The loop breaks early when the chunk it just read reaches ``file_size``, so a
    file that is an exact multiple of the chunk size finishes with real bytes. Get
    that comparison wrong by one and it instead falls out on the following
    zero-length read and commits an empty chunk — still correct bytes, so
    reassembly stays green, but a wasted round trip on every boundary-sized file
    and a sign the boundary arithmetic has drifted.
    """
    ops, dbx = dropbox_ops(chunk_size=CHUNK, session_threshold=CHUNK, max_chunks=40)
    path = tmp_path / "f.bin"
    _write(path, size)

    await ops.upload_file(path, "/dest")

    assert all(dbx.chunks), f"an empty chunk was sent: {[len(c) for c in dbx.chunks]}"
    assert len(dbx.chunks) == size // CHUNK


async def test_dropbox_uploads_the_size_it_measured_when_the_file_grows(
    dropbox_ops, tmp_path, monkeypatch
):
    """Pins current behaviour for a file that grows mid-upload.

    ``file_size`` is measured once with ``stat()`` before the loop, and the
    last-chunk test compares against it, so bytes appended after that point are not
    uploaded and no error is raised. That is defensible — the cloud copy matches
    what was measured, and the next scan sees a newer mtime and uploads again — but
    it is a silent truncation either way, so it is asserted rather than left to be
    discovered.

    A stale ``stat`` stands in for the race: on disk the file is three chunks, but
    the size the upload was told is two.
    """
    ops, dbx = dropbox_ops(chunk_size=CHUNK, session_threshold=CHUNK, max_chunks=40)
    path = tmp_path / "f.bin"
    _write(path, 3 * CHUNK)

    real_stat = type(path).stat
    monkeypatch.setattr(
        type(path),
        "stat",
        lambda self, *a, **kw: (
            SimpleNamespace(st_size=2 * CHUNK)
            if self == path
            else real_stat(self, *a, **kw)
        ),
    )

    await ops.upload_file(path, "/dest")

    assert dbx.uploaded_size == 2 * CHUNK, (
        "behaviour changed: the upload now follows the file past its measured size"
    )


async def test_dropbox_small_file_skips_the_session_path(dropbox_ops, tmp_path):
    """Below the threshold it must stay a single request — sessions cost round
    trips, and this is the branch the to_thread change touched."""
    ops, dbx = dropbox_ops(chunk_size=CHUNK, session_threshold=10 * CHUNK, max_chunks=4)
    path = tmp_path / "f.bin"
    original = _write(path, 3 * CHUNK)

    captured: dict[str, Any] = {}

    async def _run(func, content, dest, **kw):
        captured["content"] = content
        captured["dest"] = dest
        return FakeFileMetadata(dest, len(content))

    ops._client._run = _run
    ops._client.dbx.files_upload = lambda *a, **k: None

    await ops.upload_file(path, "/dest")

    assert captured["content"] == original
    assert dbx.chunks == [], "the session path ran for a file below the threshold"


async def test_dropbox_reads_the_file_off_the_event_loop(dropbox_ops, tmp_path):
    """The point of the ASYNC230 change. A blocking read in the coroutine would
    stall every other sync pair; assert the loop stays responsive during upload."""
    ops, _ = dropbox_ops(chunk_size=CHUNK, session_threshold=CHUNK, max_chunks=40)
    path = tmp_path / "f.bin"
    _write(path, 4 * CHUNK)

    ticks = 0

    async def _ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0)
            ticks += 1

    ticker = asyncio.create_task(_ticker())
    try:
        await ops.upload_file(path, "/dest")
    finally:
        ticker.cancel()

    assert ticks > 0, "the event loop never got control back during the upload"


async def test_dropbox_commits_to_the_requested_path(dropbox_ops, tmp_path):
    ops, dbx = dropbox_ops(chunk_size=CHUNK, session_threshold=CHUNK, max_chunks=40)
    path = tmp_path / "f.bin"
    _write(path, 2 * CHUNK + 3)

    await ops.upload_file(path, "/parent", remote_name="renamed.bin")

    assert dbx.committed.path == "/parent/renamed.bin"


# ── Box: chunked upload sessions ────────────────────────────────────────


class FakeChunkedUploads:
    """Box's ``chunked_uploads`` manager, recording parts and bounded."""

    def __init__(self, part_size: int, max_parts: int) -> None:
        self.part_size = part_size
        self.parts: list[bytes] = []
        self.ranges: list[str] = []
        self.digest: str | None = None
        self.committed_parts: list | None = None
        self._max = max_parts

    def create_file_upload_session(self, attrs):
        return SimpleNamespace(id="sess", part_size=self.part_size)

    def create_file_upload_session_for_existing_file(self, file_id, file_size=0):
        return SimpleNamespace(id="sess", part_size=self.part_size)

    def upload_file_part(self, session_id, chunk, content_range=None):
        self.parts.append(chunk)
        self.ranges.append(content_range)
        if len(self.parts) > self._max:
            raise RunawayUpload(
                f"upload_file_part called {len(self.parts)} times; the loop is not "
                "terminating"
            )
        return SimpleNamespace(part={"part_id": f"p{len(self.parts)}"})

    def create_file_upload_session_commit(self, session_id, parts, digest=None):
        self.digest = digest
        self.committed_parts = list(parts.parts)
        return SimpleNamespace(entries=[
            SimpleNamespace(
                id="box-1", name="f.bin", size=self.uploaded_size, type="file",
                etag="1", modified_at=None, parent=None, sha_1=None,
            )
        ])

    @property
    def uploaded_size(self) -> int:
        return sum(len(p) for p in self.parts)

    @property
    def reassembled(self) -> bytes:
        return b"".join(self.parts)


def _build_fake_box_sdk() -> dict[str, ModuleType]:
    """The ``box_sdk_gen`` modules the chunked-upload path imports.

    ``_upload_chunks`` imports ``CreateFileUploadSessionCommitParts`` at commit
    time, so without this the loop under test runs correctly and then dies on the
    import — which looks like a bug in the loop.
    """
    root = ModuleType("box_sdk_gen")
    managers = ModuleType("box_sdk_gen.managers")
    chunked = ModuleType("box_sdk_gen.managers.chunked_uploads")

    class CreateFileUploadSessionAttributes:
        def __init__(self, folder_id=None, file_name=None, file_size=None) -> None:
            self.folder_id = folder_id
            self.file_name = file_name
            self.file_size = file_size

    class CreateFileUploadSessionCommitParts:
        def __init__(self, parts=None) -> None:
            self.parts = parts or []

    chunked.CreateFileUploadSessionAttributes = CreateFileUploadSessionAttributes
    chunked.CreateFileUploadSessionCommitParts = CreateFileUploadSessionCommitParts
    managers.chunked_uploads = chunked
    root.managers = managers

    return {
        "box_sdk_gen": root,
        "box_sdk_gen.managers": managers,
        "box_sdk_gen.managers.chunked_uploads": chunked,
    }


@pytest.fixture
def box_client(monkeypatch):
    """A ``BoxClient`` with only the chunked-upload surface faked.

    ``_upload_chunks`` is driven directly rather than through ``upload_file``:
    ``part_size`` comes off the session object, which the fake owns, so there is
    no 50 MB threshold to work around and no constant to patch.
    """
    for name, module in _build_fake_box_sdk().items():
        monkeypatch.setitem(sys.modules, name, module)

    from cloud_drive_sync.providers.box.client import BoxClient

    def _make(*, part_size: int, max_parts: int = _RUNAWAY_LIMIT * 5):
        client = BoxClient.__new__(BoxClient)
        managers = FakeChunkedUploads(part_size, max_parts)
        client._client = SimpleNamespace(chunked_uploads=managers)
        return client, managers

    return _make


PART = 48


@pytest.mark.parametrize(
    "size",
    [PART - 1, PART, PART + 1, 2 * PART, 2 * PART + 1, 3 * PART - 1, 4 * PART + 9],
    ids=lambda s: f"{s}b",
)
async def test_box_chunked_upload_reassembles_the_file_exactly(box_client, tmp_path, size):
    client, managers = box_client(part_size=PART)
    path = tmp_path / "f.bin"
    original = _write(path, size)
    session = SimpleNamespace(id="sess", part_size=PART)

    await client._upload_chunks(session, str(path), size)

    assert managers.reassembled == original


async def test_box_content_ranges_cover_the_file_without_gaps(box_client, tmp_path):
    """Box reassembles by declared range, so the ranges are as load-bearing as the
    bytes. An inverted or overlapping range corrupts silently."""
    client, managers = box_client(part_size=PART)
    path = tmp_path / "f.bin"
    size = 3 * PART + 11
    _write(path, size)
    session = SimpleNamespace(id="sess", part_size=PART)

    await client._upload_chunks(session, str(path), size)

    expected, offset = [], 0
    for part in managers.parts:
        expected.append(f"bytes {offset}-{offset + len(part) - 1}/{size}")
        offset += len(part)
    assert managers.ranges == expected
    assert offset == size


async def test_box_sends_a_digest_of_what_it_actually_uploaded(box_client, tmp_path):
    """The commit digest is computed incrementally across chunks. If it were taken
    over anything but the uploaded bytes, Box would reject a correct upload — or
    accept a corrupt one."""
    import base64

    client, managers = box_client(part_size=PART)
    path = tmp_path / "f.bin"
    size = 2 * PART + 5
    original = _write(path, size)
    session = SimpleNamespace(id="sess", part_size=PART)

    await client._upload_chunks(session, str(path), size)

    expected = base64.b64encode(hashlib.sha1(original).digest()).decode()
    assert managers.digest == f"sha={expected}"


async def test_box_commits_every_part_it_uploaded(box_client, tmp_path):
    """The commit lists the parts by identifier, separately from uploading them.

    Reassembly cannot see this: dropping a part from the commit list leaves every
    byte correctly uploaded and only the manifest wrong. Box then assembles the
    file from the parts it was told about — so a missing entry means a file short
    by one part, or a rejected commit, with the upload loop entirely innocent.
    """
    client, managers = box_client(part_size=PART)
    path = tmp_path / "f.bin"
    size = 3 * PART + 4
    _write(path, size)
    session = SimpleNamespace(id="sess", part_size=PART)

    await client._upload_chunks(session, str(path), size)

    assert len(managers.parts) == 4, "expected four parts for this size"
    assert managers.committed_parts == [
        {"part_id": f"p{i}"} for i in range(1, len(managers.parts) + 1)
    ], "the commit manifest does not list every uploaded part, in order"


async def test_box_upload_stops_when_the_file_shrinks(box_client, tmp_path):
    """A truncated file used to hang the upload forever.

    ``offset`` advances by ``len(chunk)``, so a zero-length read left it unchanged
    while ``offset < file_size`` stayed true — empty parts uploaded in a tight loop,
    each with an inverted ``bytes N-{N-1}`` range, never reaching the commit. This
    is reachable in normal use: the size is measured with ``stat()`` before the
    loop, and a file being written can be truncated in between.
    """
    client, managers = box_client(part_size=PART, max_parts=_RUNAWAY_LIMIT)
    path = tmp_path / "f.bin"
    _write(path, PART)  # on disk: one part
    session = SimpleNamespace(id="sess", part_size=PART)

    # Declared size says four parts, so the loop asks for bytes that are not there.
    with pytest.raises(OSError, match="shrank during upload"):
        await client._upload_chunks(session, str(path), 4 * PART)

    assert managers.digest is None, "a short file was committed as if complete"
    assert not any(p == b"" for p in managers.parts), "empty parts were uploaded"


async def test_box_reads_the_file_off_the_event_loop(box_client, tmp_path):
    client, _ = box_client(part_size=PART)
    path = tmp_path / "f.bin"
    size = 4 * PART
    _write(path, size)
    session = SimpleNamespace(id="sess", part_size=PART)

    ticks = 0

    async def _ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0)
            ticks += 1

    ticker = asyncio.create_task(_ticker())
    try:
        await client._upload_chunks(session, str(path), size)
    finally:
        ticker.cancel()

    assert ticks > 0


# ── OneDrive: upload sessions ───────────────────────────────────────────


class FakePutResponse:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._payload


class FakeAsyncClient:
    """Stands in for ``httpx.AsyncClient`` inside ``_session_upload``.

    The client is constructed inside the method, so there is no transport to
    inject — the class itself is replaced.
    """

    recorder: Any = None

    def __init__(self, *a, **kw) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def put(self, url, content=None, headers=None):
        return FakeAsyncClient.recorder.record(content, headers)


class PutRecorder:
    def __init__(self, total: int, max_puts: int) -> None:
        self.chunks: list[bytes] = []
        self.ranges: list[str] = []
        self.total = total
        self._max = max_puts

    def record(self, content, headers) -> FakePutResponse:
        self.chunks.append(content)
        self.ranges.append(headers["Content-Range"])
        if len(self.chunks) > self._max:
            raise RunawayUpload(
                f"{len(self.chunks)} PUTs sent; the chunk loop is not terminating"
            )
        if sum(len(c) for c in self.chunks) >= self.total:
            return FakePutResponse(201, {"id": "od-1", "name": "f.bin", "size": self.total})
        return FakePutResponse(202)

    @property
    def reassembled(self) -> bytes:
        return b"".join(self.chunks)


#: The OneDrive chunk size is a local in ``_session_upload``, not a module
#: constant, so it cannot be patched. Rather than reshape production code to suit
#: a test, the multi-chunk cases use a real file larger than 10 MB.
_OD_CHUNK = 10 * 1024 * 1024


@pytest.fixture
def onedrive_client(monkeypatch):
    from cloud_drive_sync.providers.onedrive.client import OneDriveClient

    def _make(*, total: int, max_puts: int = _RUNAWAY_LIMIT):
        client = OneDriveClient.__new__(OneDriveClient)

        async def _graph_post(path, json=None):
            return {"uploadUrl": "https://upload.example/session"}

        async def _get_token():
            return "token"

        client._graph_post = _graph_post
        client._get_token = _get_token

        recorder = PutRecorder(total, max_puts)
        FakeAsyncClient.recorder = recorder

        fake_httpx = ModuleType("httpx")
        fake_httpx.AsyncClient = FakeAsyncClient
        monkeypatch.setitem(sys.modules, "httpx", fake_httpx)
        return client, recorder

    return _make


@pytest.mark.parametrize(
    "size",
    [_OD_CHUNK + 1, _OD_CHUNK + 4096, 2 * _OD_CHUNK],
    ids=["one-byte-over", "small-tail", "exact-two-chunks"],
)
async def test_onedrive_session_upload_reassembles_the_file_exactly(
    onedrive_client, tmp_path, size
):
    """Files above 10 MB, because the chunk size is a local and cannot be shrunk.
    A few tens of MB through tmp_path is cheap; leaving the loop untested is not.
    """
    client, recorder = onedrive_client(total=size)
    path = tmp_path / "f.bin"
    original = _write(path, size)

    await client._session_upload(str(path), file_size=size, name="f.bin", parent_id="root")

    assert recorder.reassembled == original
    assert len(recorder.chunks) >= 2, "the multi-chunk loop did not run"


async def test_onedrive_content_ranges_are_contiguous_and_complete(
    onedrive_client, tmp_path
):
    size = _OD_CHUNK + 1234
    client, recorder = onedrive_client(total=size)
    path = tmp_path / "f.bin"
    _write(path, size)

    await client._session_upload(str(path), file_size=size, name="f.bin", parent_id="root")

    expected, offset = [], 0
    for chunk in recorder.chunks:
        expected.append(f"bytes {offset}-{offset + len(chunk) - 1}/{size}")
        offset += len(chunk)
    assert recorder.ranges == expected
    assert offset == size


async def test_onedrive_upload_stops_when_the_file_shrinks(onedrive_client, tmp_path):
    """Same hazard as Box. Graph would reject the malformed range, but only after
    the loop had already sent it — and the error would name the range, not the
    truncation that caused it."""
    declared = 3 * _OD_CHUNK
    client, recorder = onedrive_client(total=declared)
    path = tmp_path / "f.bin"
    _write(path, 4096)  # far smaller than declared

    with pytest.raises(OSError, match="shrank during upload"):
        await client._session_upload(
            str(path), file_size=declared, name="f.bin", parent_id="root"
        )

    assert not any(c == b"" for c in recorder.chunks), "empty chunks were PUT"


async def test_onedrive_small_file_uses_a_single_put(monkeypatch, tmp_path):
    """The simple-upload branch, where the read moved into ``to_thread``."""
    from cloud_drive_sync.providers.onedrive.client import OneDriveClient

    client = OneDriveClient.__new__(OneDriveClient)
    captured: dict[str, Any] = {}

    async def _request(method, url, data=None, headers=None):
        captured["method"] = method
        captured["data"] = data
        return {"id": "od-1", "name": "f.bin", "size": len(data)}

    client._request = _request

    path = tmp_path / "f.bin"
    original = _write(path, 1024)

    await client._simple_upload(str(path), name="f.bin", parent_id="root")

    assert captured["method"] == "PUT"
    assert captured["data"] == original


async def test_onedrive_reads_the_file_off_the_event_loop(onedrive_client, tmp_path):
    """A 10 MB read on the loop is the exact stall the aiofiles change removed."""
    size = _OD_CHUNK + 2048
    client, _ = onedrive_client(total=size)
    path = tmp_path / "f.bin"
    _write(path, size)

    ticks = 0

    async def _ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0)
            ticks += 1

    ticker = asyncio.create_task(_ticker())
    try:
        await client._session_upload(
            str(path), file_size=size, name="f.bin", parent_id="root"
        )
    finally:
        ticker.cancel()

    assert ticks > 0


# ── The guard on the harness ────────────────────────────────────────────


async def test_the_fakes_would_catch_a_runaway_loop(box_client, tmp_path):
    """If the bounds did not fire, the truncation tests above would hang pytest
    rather than fail — the same non-termination bug, relocated. So prove the bound
    fires, by driving past it deliberately."""
    client, managers = box_client(part_size=1, max_parts=3)
    path = tmp_path / "f.bin"
    _write(path, 100)
    session = SimpleNamespace(id="sess", part_size=1)

    with pytest.raises(RunawayUpload):
        await client._upload_chunks(session, str(path), 100)
