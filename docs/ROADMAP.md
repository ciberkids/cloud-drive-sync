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
| 6 | Lock down stored OAuth tokens (`0600`) | Security | — | ✅ Done — v2.4.1 (Google), v2.4.2 (every provider) |
| 7 | Authentication on by default for new installs | Security | — | ✅ Done — shipped in v2.4.3; upgrades untouched |
| 8 | Encrypt OneDrive, Box and Nextcloud credentials | Security | [#57](https://github.com/ciberkids/cloud-drive-sync/issues/57) | ✅ Done — shipped in v2.4.3 |
| 9 | Give each sync pair a stable id | Data safety | — | ⚖️ Harm fixed in v2.4.5 (history follows its pair); the identity scheme is still positional |
| 10 | [Event webhooks](#10--event-webhooks) | Feature | — | 📋 Proposed — see [the proposal](Proposal-Event-Webhooks) |

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

**Shipped in v2.4.3** as described. See [Authentication](Daemon#authentication). One thing worth recording: the whole feature rests on `Config.load` not creating a file when none exists. If that ever changes, every install looks like an upgrade and this silently stops working — no error, just deployments open again — so there is a test pinning that behaviour.

---

## 8 — Encrypt OneDrive, Box and Nextcloud credentials

Those three stored credentials as plaintext JSON while the README claimed encryption at rest; Nextcloud's was an app password, the credential itself. **Shipped in v2.4.3** ([#57](https://github.com/ciberkids/cloud-drive-sync/issues/57)).

The non-obvious part, recorded so it is not "tidied up" later: their salt is written **beside each credential file** rather than in the shared data directory. They live under the config directory while the shared salt lives under data — separate volumes in a container — so a shared salt would let the ciphertext and its only key be restored apart, and salt creation mints a new salt rather than failing. That would turn a config-only restore into silent, permanent loss of every account. See [Authentication](Architecture#authentication).

## 9 — Give each sync pair a stable id

Pairs are identified by their position in the config list: pair *N* is `pair_N`, in the engine, in `sync_state`, in `pending_deletions` and in the change tokens. Removing or reordering a pair therefore renumbers every pair after it, and stored rows keyed by the old number start describing a different folder.

**Five findings from the 2026-08-01 audit are symptoms of this**, all fixed locally rather than at the root:

- A refused deletion block outlived its pair, so approving it granted a delete-protection bypass to a folder the user was never asked about. *(Mitigated: removal discards blocks at or after the removal point, and approving verifies the sample paths belong to the pair.)*
- `pair remove` left the pair registered in the running engine, so a removed pair kept running until restart. *(Still open — the stored state is now correct, but the live engine is not told.)*
- Demo mode inserted its pair at index 0 of the real config, so it inherited the first real pair's identity and overwrote its sync state. *(Fixed in v2.4.5 — it appends.)*
- `pause 0` / `sync 0` matched nothing because the CLI prints `0` and the engine keys `pair_0`. *(Mitigated: ids are normalised at the handler.)*

### What v2.4.5 fixed

The *harm* is gone. Removing a pair now shifts the surviving pairs' rows down across all six `pair_id` tables in one transaction, so each pair keeps its own sync state, change token, conflicts and refused deletion batch. Demo mode appends its pair instead of inserting at index 0, which had renumbered every real pair. Those were the only two things that mutated pair order.

That replaced an earlier mitigation which *discarded* deletion blocks at and after the removal point — fail-safe but lossy, since a survivor's own block went with the stale one.

### What is still open, and the cost of finishing it

The identity scheme is still positional, so the invariant is maintained by remembering to renumber rather than by construction. A future reorder feature, or a hand-edited `config.toml`, would reintroduce the drift.

**Shape:** persist a per-pair uuid on `SyncPair`, key the engine and the database on it, and migrate all six tables across.

**The cost is larger than it looks, and most of it is not in the daemon.** The engine's `pair_N` ids are exposed to the UI — through `get_status` keys, activity-log rows and pair counts — and `Transfers.tsx`, `SyncStatus.tsx` and `ActivityLog.tsx` each rebuild `pair_${i}` from a list index to join against them. Switching to uuids therefore means a config-dependent database migration **plus** three UI components **plus** a webui rebuild, and the UI join is where a mistake shows wrong data silently instead of erroring.

**And the migration cannot be more correct than the data it inherits.** The mapping `pair_N -> pairs[N].id` is only right if the config order at migration time matches the order when the rows were written. Anyone who removed a pair on a version before v2.4.5 already has mis-assigned rows, and nothing records the old order — so the migration freezes whatever state exists and prevents future drift. It cannot repair past drift, and it should not try: a heuristic matching stored paths against `local_path` would be guessing about the user's files.

## 10 — Event webhooks

**Problem.** The daemon knows things the user needs to act on and has no way to tell them. A refused deletion batch pauses a pair and waits for a human ([item 1](#1--delete-fail-safe)); a conflict waits for a decision; an expired credential stops an account syncing. All of it surfaces only if someone opens the UI or runs `activity`. On the deployment where this matters most — a headless NAS or container, running unattended for weeks — nobody is looking. The delete fail-safe is the sharpest case: it exists precisely to stop silent data loss, and today it announces itself into a log file.

**Feature.** Per-pair outbound HTTP callbacks. Configurable at three levels — global, per account, per pair — as a hierarchy where a lower level can override an inherited setting or introduce a callback that exists nowhere above it. Several authorization mechanisms, because the receiving end is someone else's system: none, Basic, bearer, a daemon-minted JWT, and an arbitrary custom header.

**Requirements**

- Three configuration levels with a defined merge, not just override: a pair must be able to add its own callback, narrow an inherited one's event set, and switch an inherited one off.
- A stable identifier in the payload. Positional `pair_N` must never leave the process — see [item 9](#9--give-each-sync-pair-a-stable-id).
- Delivery must never block or slow a sync pass. A hung endpoint is a normal condition.
- At-least-once, with a per-event id so receivers can deduplicate. Bounded queue; `deletion.blocked` must not be droppable.
- Secrets never in a log, an error message, a status payload, or a read API response.
- Reachable from every front-end: CLI, REST, MCP (read-only), UI.

**Open questions**

- Is `deletion.blocked` alone worth the first release? It is most of the value, at a fraction of the scope.
- Does the account level pull its weight, given it holds exactly one setting today?
- Batching, for the case where a library scan produces thousands of per-file events.

> **Full design:** [Proposal: Event Webhooks](Proposal-Event-Webhooks) — payload schema, the three-level merge algorithm with a worked example, all five authorization mechanisms, and the two prerequisites it depends on.

---

## Adding to the queue

Append to the table with the next number, add a section, and open a matching issue. Keep the safety-first ordering: if a new item prevents data loss, it belongs above items that do not.
