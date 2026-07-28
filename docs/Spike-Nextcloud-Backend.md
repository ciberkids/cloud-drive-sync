# Spike: Replacing the Nextcloud WebDAV Bridge

Findings for [#55](https://github.com/ciberkids/cloud-drive-sync/issues/55). Research and prototype assessment — not an implementation.

**Recommendation in one line:** adopt Nextcloud's `notify_push` app for change detection and keep the existing WebDAV client for transfers. Do **not** adopt rclone for this problem — it does not solve it.

---

## The question that mattered

The spike was framed around one question, because everything else depends on it:

> Does any option provide genuine change detection, or is tree-walking unavoidable?

The WebDAV bridge has produced four Nextcloud incidents ([#44](https://github.com/ciberkids/cloud-drive-sync/issues/44), [#47](https://github.com/ciberkids/cloud-drive-sync/issues/47), [#50](https://github.com/ciberkids/cloud-drive-sync/issues/50), and the upstream mutation bug [cloud-py-api/nc_py_api#453](https://github.com/cloud-py-api/nc_py_api/issues/453)). Those were individually fixable, but they share a root: **WebDAV has no delta API**, so `NextcloudChangePoller` walks the entire tree comparing ETags on every poll. `_build_etag_map` recurses into each directory, issuing **one PROPFIND per directory per poll cycle** — by default every 30 seconds. That is why per-property server cost was so damaging: it multiplies across the whole tree, every cycle, forever.

Answer: **tree-walking is avoidable, but not via rclone.**

---

## Finding 1 — rclone does not solve change detection

This was the leading candidate and it fails on the central question.

rclone has an internal `ChangeNotify` capability, exposed per backend. Checked against the source:

| Backend | `ChangeNotify` in source |
|---|---|
| `backend/drive/drive.go` (Google Drive) | present |
| `backend/webdav/webdav.go` (1681 lines) | **absent** |

So rclone against Nextcloud polls by listing, exactly as we do. It would move our tree-walk into a different process, not remove it. `--poll-interval` and `vfs/poll-interval` only apply to backends that implement `ChangeNotify`, and WebDAV is not one of them.

rclone's `rc` API also exposes no delta/changes endpoint — `operations/list`, `operations/stat`, `sync/*`, `vfs/refresh`. Nothing that answers "what changed since token X".

**What rclone would genuinely give us**, if we wanted it for other reasons: mature Nextcloud chunked-upload support (`nextcloud_chunk_size`, vendor detection, the `/dav/files/` URL handling), plus its own retry, bandwidth limiting and hashing. That is transfer robustness — a real but *different* problem from the one that caused our incidents.

**Deployment cost, measured:**

- rclone linux-amd64: **28 MB zipped**. Meaningful for the Docker image, and awkward inside the Flatpak sandbox.
- `librclone` (the FFI route) is **not published as a release artifact** for v1.74.4 — it would have to be built from source per platform, which complicates the standalone daemon builds on three OSes.
- Running `rclone rcd` means a second long-lived process to supervise, and its own docs warn that rc access "is equivalent to shell access as the user running rclone" — a second unauthenticated control surface, which is a concern we already have enough of.

---

## Finding 2 — `notify_push` is the actual answer

Nextcloud has a real push mechanism: the [`notify_push`](https://github.com/nextcloud/notify_push) app, which is what the official desktop client uses to avoid polling.

Protocol, from its `DEVELOPING.md`:

1. `GET /ocs/v2.php/cloud/capabilities` (authenticated) returns the WebSocket URL under the `notify_push` capability — so discovery doubles as a feature test.
2. Open the WebSocket, send username then password (or empty username + a token from the `pre_auth` endpoint).
3. Server replies `authenticated`.
4. Events arrive as text: `notify_file`, `notify_activity`, `notify_notification`, and — since v0.4 — `notify_file_id` with a JSON array of changed file IDs.

### Why this fits us unusually well

`notify_file_id` delivers **Nextcloud file IDs**, and we already request `oc:fileid` and store it as `RemoteChange.file_id`. The identifier the push server hands us is the one our data model is already keyed on. No mapping layer, no new identity concept.

Opt in with `listen notify_file_id`; the server falls back to coarse `notify_file` when it cannot determine which files changed.

### The two constraints that shape the design

**It is explicitly best-effort.** The README is unambiguous: "updates might happen without a notification being sent and a notification can be sent even if no update has actually happened." So it cannot *replace* polling — it can only make polling rare. The ETag walk has to stay as a periodic reconciliation pass, at a much longer interval.

**It is not always installed.** `notify_push` needs Redis, a push daemon process, and ideally a reverse proxy. Plenty of Nextcloud instances have none of that. The capabilities check makes this detectable at runtime, so the design is: use push when advertised, fall back to polling when it is not.

That combination is a feature, not a compromise — it means adoption is incremental and cannot regress anyone.

---

## Finding 3 — a leaner in-house client is worth doing regardless

Independently of push, the current client is heavier than it needs to be. #50 established that `oc:checksums` and `oc:share-types` were requested on every listing and **never parsed** by `nc-py-api` at all. That was 2 of 24 properties removed for free.

The remaining 22 are still more than we consume, and `nc-py-api` has already cost us one server outage through its own module-state bug. Dropping it for a small purpose-built PROPFIND client would remove a dependency that has proven unreliable on exactly this path, and let us request only what we read.

This does not need to block push work, and it does not fix change detection — but it shrinks the cost of every poll that still happens.

---

## Recommendation

| Priority | Action | Effect |
|---|---|---|
| 1 | Implement a `notify_push` change poller, selected at runtime via the capabilities check | Removes tree-walking on instances that support it |
| 2 | Keep ETag polling as a long-interval reconciliation pass | Covers the best-effort gap and unsupported servers |
| 3 | Consider replacing `nc-py-api` with a minimal PROPFIND client | Removes a proven-unreliable dependency; cheaper polls |
| — | **Do not** adopt rclone for change detection | It does not provide it |

Revisit rclone separately if chunked-upload robustness or large-file transfer reliability becomes the pressing problem. It is a good answer to that question and the wrong answer to this one.

### On the user-selectable requirement

The requirement was that the backend be user-selectable rather than swapped. The capabilities-driven design satisfies this more cleanly than a manual setting: the daemon detects push support and uses it, with an explicit override for anyone who wants to force polling. That means the setting exists for the case where automatic choice is wrong, rather than being a decision every user is forced to make about a mechanism they should not need to understand.

UI implication is correspondingly small: show which mechanism a pair is actually using, and offer a "force polling" escape hatch — rather than a backend selector that demands the user know what WebDAV is.
