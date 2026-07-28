# Session state — resume here

Written 2026-07-28. Scratch file for picking the release back up; delete once v2.2.0 is out.

## Where we stopped

**The release was NOT tagged.** I checked CI before tagging and it was red, so nothing was published. `ghcr.io/ciberkids/cloud-drive-sync:latest` is still **v2.1.0**.

Last commit: `e3c9f94` — pins `mcp<2`, which is the fix for the failure that blocked the tag.

## Why CI was red

`mcp 2.0.0` was released today. Both extras declared `mcp>=1.28.0` with no upper bound, so CI resolved 2.0.0, which **removed `Server.list_tools`** — used by `mcp/server.py:99`. Every MCP transport test failed with `AttributeError`, on a docs-only commit with no code change responsible.

Same failure mode as the unpinned ruff earlier today: an unbounded dependency lets an upstream release decide when CI breaks, and the breakage attaches to whatever commit is next.

Fixed in `e3c9f94` by pinning `>=1.28.0,<2` in both extras. Verified in a clean venv that it resolves to 1.29.0 with `list_tools` present. Local gate is green: 825 tests (system python), 838 (venv), ruff/cargo/tsc clean.

## To resume — do these in order

```bash
cd /home/matteo/Documents/projects/personal/gdrive-sync

# 1. Confirm CI went green on e3c9f94 (the mcp pin). Per-job, not just the run.
gh run list --workflow=ci.yml --limit 1 --json headSha,status,conclusion
RUN=$(gh run list --workflow=ci.yml --limit 1 --json databaseId --jq '.[0].databaseId')
gh api repos/ciberkids/cloud-drive-sync/actions/runs/$RUN/jobs --paginate \
  --jq '.jobs[] | "\(.name) :: \(.conclusion)"'

# 2. Only if all 8 jobs are success AND the sha matches HEAD:
git tag -a v2.2.0 -m "v2.2.0 — see release notes"
git push origin v2.2.0

# 3. Watch the release pipeline (~25 min; Docker job finishes earlier than the
#    Tauri bundles, and Docker is what matters for container users)
gh api repos/ciberkids/cloud-drive-sync/actions/runs/<id>/jobs \
  --jq '.jobs[] | select(.name|test("Docker")) | .conclusion'

# 4. Verify the published image really has the fixes — do not trust the pipeline
#    alone. Note: docker is NOT installed on this machine, use podman.
podman manifest inspect ghcr.io/ciberkids/cloud-drive-sync:latest >/dev/null && echo ok
# Compare digests: :latest and :2.2.0 must match, and differ from :2.1.0
TOK=$(curl -s "https://ghcr.io/token?scope=repository:ciberkids/cloud-drive-sync:pull&service=ghcr.io" | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")
for t in latest 2.2.0 2.1.0; do
  curl -sI -H "Authorization: Bearer $TOK" \
    -H "Accept: application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.v2+json" \
    "https://ghcr.io/v2/ciberkids/cloud-drive-sync/manifests/$t" | grep -i docker-content-digest
done
# Then smoke-test inside the image (this is what caught the MCP gap last time)
podman run --rm --entrypoint python3 ghcr.io/ciberkids/cloud-drive-sync:2.2.0 -c "
import cloud_drive_sync, importlib.util as u
print('version:', cloud_drive_sync.__version__)
print('mcp present:', u.find_spec('mcp') is not None)
from cloud_drive_sync.sync import failsafe; print('failsafe:', failsafe.DEFAULT_MAX_DELETIONS, failsafe.DEFAULT_WINDOW_SECONDS)
from cloud_drive_sync.config import SyncConfig; print('stopped field:', hasattr(SyncConfig(), 'stopped'))
"

# 5. Write release notes (CLAUDE.md step 11) once the release object exists
curl -s "https://api.github.com/repos/ciberkids/cloud-drive-sync/releases/tags/v2.2.0" | jq .id
```

**The image tag has no `v`** — the workflow strips it, so it is `:2.2.0`, not `:v2.2.0`. `:v2.1.0` does not exist; this bit me before.

## Release notes content — draft

Version: **v2.2.0** (minor: four new features, no breaking API).

### ⚠️ Behaviour change to lead with

**Delete protection is now on by default.** A sync pass deleting more than **100 files per direction within 60 seconds** is refused, the pair pauses, and it waits for confirmation. Nothing is deleted until a human approves. Configurable in Settings → Delete Protection, per-pair under Advanced Rules; `0` disables it.

Users doing large legitimate cleanups will hit this. That is intended, but it should be the first thing they read.

### Features

- **Delete fail-safe** (#53) — three triggers: count within a time window, >50% of tracked files, each direction separately. Window counted from the activity log so it survives restart. Approval is one-shot and re-plans rather than replaying. UI banner, CLI (`deletions list|approve|reject`), REST, and read-only MCP visibility.
- **Emergency stop** (#54) — immediate stop/resume at account and app level, cancelling in-flight work. Sidebar control, `stop-activity`/`resume-activity`, REST, MCP. Persists across restart. Honest limit: a provider call already inside a worker thread cannot be cancelled, so at most one transfer per worker finishes writing.
- **MCP server** — AI assistants can inspect and manage sync. Off by default, read-only unless `--mcp-allow-writes`. `CDS_MCP_PORT=8081` in containers.
- **Database size gauge** — `daemon.database` in status plus the Status dashboard, so #49-style bloat is visible before it reaches GB scale.

### Fixes

- #50 — stopped requesting `oc:checksums`/`oc:share-types`, which the server computes and nc-py-api never parses (24 → 22 properties per listing)
- #51 — wiki pages and sidebar now generated from one mapping, with the job failing on drift
- #52 — explicit ruff rule set; fixed a `min_date` crash (tz-aware ISO input raised `TypeError` and aborted the sync pass) and 9 blocking file reads inside async functions
- `Config.save()` ignored a custom `--config` path and wrote to the default location, so settings changes silently went to a file the user was not using
- MCP: reported the SDK's version as the daemon's; origin checking could never match

### Docs

Headless web UI promoted to a first-class topic (it was buried under Docker Deployment); the unauthenticated exposure of the HTTP and MCP ports is now documented on every deployment page. New: feature queue, Nextcloud backend spike findings.

## Also open / not done

- **#56** — Nextcloud `notify_push` change detection. The spike concluded rclone is the wrong tool (its WebDAV backend has no `ChangeNotify`); `notify_push` delivers the file IDs we already store. Next feature after the release.
- **mcp 2.x migration** — pinned `<2` today. Worth an issue once the 2.0 API is understood; do it deliberately, not under release pressure.
- The `ASYNC230` fixes touch Box/Dropbox/OneDrive upload paths, which have **no test coverage** and are marked 🧪 in the README. Reasoned-equivalent, not measured. First place to look if an upload misbehaves after this release.
