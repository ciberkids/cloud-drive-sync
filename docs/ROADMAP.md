# Feature Queue

The ordered list of what gets built next. This file is the queue; each item also has a GitHub issue for discussion and progress. Items are worked top to bottom — the order is deliberate, not arbitrary.

**Ordering principle:** anything that prevents irreversible data loss outranks everything else, because a sync client's worst failure mode is destroying the copy the user thought was safe. Convenience and research come after.

| # | Item | Kind | Issue | Status |
|---|------|------|-------|--------|
| 1 | [Delete fail-safe](#1--delete-fail-safe) | Data safety | [#53](https://github.com/ciberkids/cloud-drive-sync/issues/53) | ✅ Done |
| 2 | [Emergency stop button](#2--emergency-stop-button) | Data safety / control | [#54](https://github.com/ciberkids/cloud-drive-sync/issues/54) | ✅ Done |
| 3 | [Nextcloud backend research spike](#3--nextcloud-backend-research-spike) | Spike | [#55](https://github.com/ciberkids/cloud-drive-sync/issues/55) | ✅ Done — see [findings](Spike-Nextcloud-Backend) |
| 4 | Nextcloud push-based change detection | Feature | [#56](https://github.com/ciberkids/cloud-drive-sync/issues/56) | ✅ Done — shipped in v2.3.0, validated against a live `notify_push` server |
| 5 | Token authentication for the HTTP and MCP ports | Security | — | ✅ Done — shipped in v2.4.0, opt-in |
| 6 | Lock down stored OAuth tokens (`0600`) | Security | — | ✅ Done — shipped in v2.4.1 |
| 7 | Authentication on by default for new installs | Security | — | 🔜 Next — generate a token on first run; keep upgrades untouched |

---

## 1 — Delete fail-safe

**Problem.** Sync is symmetric, so a local catastrophe propagates. If the filesystem is wiped — a bad `rm -rf`, an unmounted drive whose mountpoint is still a synced path, a failed disk, a restored-from-empty container volume — the daemon sees thousands of deletions as legitimate user intent and faithfully deletes the cloud copy too. The backup becomes a mirror of the disaster, and the user finds out afterwards.

**Feature.** A cap on how many deletions a single sync pass may perform. Exceed it and the daemon refuses the batch, stops that pair, and surfaces a prominent prompt requiring explicit confirmation before anything is deleted. The limit is configurable in the UI.

**Requirements**

- Configurable maximum deletions per sync pass, settable in the UI (not config-file-only), with a safe non-zero default.
- On breach: **no deletions execute**, the pair pauses, and the user is asked to confirm or reject. Fail closed — a daemon that cannot ask must not delete.
- The prompt states what would be deleted and how many, so the decision is informed.
- Applies to remote deletions and local deletions independently; a wiped remote must not be able to empty the local copy either.
- Survives restart: a pending decision must not be silently resolved by restarting the daemon.
- Scope: per-pair, with a global default.

**Open questions**

- Absolute count, percentage of tracked files, or both? A count is predictable; a percentage scales with library size. Probably both, whichever trips first.
- Should the mount-point case be detected directly? A synced path that has become an empty directory is almost never a real mass delete, and could be refused outright regardless of the limit.
- Interaction with the existing trash support: deleting to trash is recoverable, so the threshold could be higher when trash is available and the provider retains it.

---

## 2 — Emergency stop button

**Problem.** There is currently no way to make the daemon stop *now*. `pause_sync` exists, but it takes effect at the next loop iteration and does not interrupt transfers already in flight. When a user realises something is wrong — the wrong folder is syncing, deletions are propagating, a provider is misbehaving — the useful action is "stop everything immediately", and the honest answer today is "kill the process".

**Feature.** A stop/resume control at two levels: per account and application-wide. Pressing it halts all activity immediately, and pressing it again resumes.

**Requirements**

- Two scopes: **per account** (that account's pairs only) and **global** (everything).
- Immediate means immediate: in-flight uploads, downloads and deletions are cancelled or aborted, not allowed to finish. This is the hard part and the reason this is a feature rather than a UI change.
- The same control resumes. State is visible at a glance — a stopped account must never look idle.
- Persists across daemon restart: if the user stopped syncing, restarting must not quietly resume it.
- Reachable from every front-end, not just the UI: CLI, REST, and MCP.
- Partial work must leave no corruption — an aborted transfer resumes or restarts cleanly rather than leaving a truncated file.

**Open questions**

- Cancellation granularity: cancelling the asyncio tasks is straightforward, but provider SDKs vary in how interruptible their calls are. Some may need the connection dropped.
- Should a global stop also stop the change pollers, or keep detecting changes and just queue them? Queuing risks a flood on resume; not queuing risks missing changes.

---

## 3 — Nextcloud backend research spike

**Problem.** The WebDAV bridge is not viable. This session alone produced three Nextcloud-specific incidents from it: a runaway PROPFIND property list that DoS'd the user's server ([#47](https://github.com/ciberkids/cloud-drive-sync/issues/47)), expensive properties requested on every listing ([#50](https://github.com/ciberkids/cloud-drive-sync/issues/50), following [#44](https://github.com/ciberkids/cloud-drive-sync/issues/44)), and an upstream library that mutates its own module state and is unmaintained on this point. Underneath that, WebDAV gives no delta/changes API, so change detection walks the whole tree comparing ETags — which is why the property cost matters so much in the first place.

**Spike.** Research alternatives, prototype the most promising, and report back with a recommendation. Timeboxed investigation, not an implementation commitment.

> **Outcome:** rclone does *not* solve change detection — its WebDAV backend has no `ChangeNotify`. Nextcloud's `notify_push` app does, and delivers the very file IDs we already store. Full findings: [Spike: Nextcloud Backend](Spike-Nextcloud-Backend). Implementation tracked as #56.

**Candidates**

- **rclone** — mature, actively maintained, handles Nextcloud/WebDAV quirks that took this project multiple incidents to find. Documented approaches exist for driving it programmatically (`rclone rcd` exposing an RPC API, or the `librclone` shared library). Brings its own bandwidth control, retry and chunking. Costs an external binary or FFI dependency, and a second notion of sync state to reconcile with ours.
- **Nextcloud OCS / native APIs** — richer than raw WebDAV for some operations; needs checking whether anything gives real change notification.
- **A leaner in-house WebDAV client** — drop `nc-py-api`, request the minimal property set, own the behaviour end to end. Least new dependency, most maintenance.

**Outcome should answer**

- Does any option provide genuine change detection, or is tree-walking unavoidable?
- What is the per-operation server cost compared to today?
- Deployment impact: an extra binary is awkward for Flatpak and the Docker image.
- How does sync state stay consistent if an external tool owns part of the transfer?

**Note the requirement is user-selectable.** The chosen backend must be an option rather than a replacement — existing installs keep working, and the user picks whichever performs better for their server. That implies a backend selector in provider setup and per-pair configuration, so the UI changes alongside.

---

## 7 — Authentication on by default for new installs

Token auth shipped in v2.4.0 as **opt-in**, so a deployment is unprotected until someone sets a token. That was chosen so upgrades would not lock people out of a bookmarked `http://nas:8080`, and it remains the right call for existing installs — but it means a fresh install is wide open by default, with only a startup warning to say so.

**Shape:** on first run, when no config file exists yet, generate a token, persist it, and print it prominently. Upgrades are untouched, because a config file already exists.

**Why this and not the alternative.** Refusing to bind to a non-loopback address without a token fails closed, which sounds stronger, but it would stop every existing Docker deployment from starting on upgrade — worse than the problem it solves.

**The part that needs care** is how a headless user finds the token. It has to be obvious in `docker logs`, and getting it wrong locks someone out of their own daemon, so this wants its own release rather than a ride-along on a patch.

## Adding to the queue

Append to the table with the next number, add a section, and open a matching issue. Keep the safety-first ordering: if a new item prevents data loss, it belongs above items that do not.
