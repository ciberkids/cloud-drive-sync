# Proposal: Event Webhooks

A design proposal for outbound HTTP callbacks that notify a remote endpoint when sync events happen, configurable at three levels — **global**, **per account**, **per pair** — with lower levels overriding or extending higher ones.

Status: **proposal**. Nothing here is implemented. This document exists to be argued with before code is written.

**Recommendation in one line:** named webhook *targets* merged by name across the three levels, one JSON envelope carrying a `schema_version` and a stable per-pair UUID, five authorization modes (`none`, `basic`, `bearer`, `jwt`, `custom`) plus a composable HMAC body signature — with a stable pair id landed first, because the payload is a wire contract and today's pair identity cannot honour one.

---

## Contents

- [The ask](#the-ask)
- [The constraint that shapes everything: identity](#the-constraint-that-shapes-everything-identity)
- [Configuration model](#configuration-model)
- [Resolution algorithm](#resolution-algorithm)
- [Worked example](#worked-example)
- [Event taxonomy](#event-taxonomy)
- [The payload](#the-payload)
- [Authorization mechanisms](#authorization-mechanisms)
- [Where the secrets live](#where-the-secrets-live)
- [Delivery semantics](#delivery-semantics)
- [The hook point](#the-hook-point)
- [Security](#security)
- [Front-end surfaces: it is nine files, not one](#front-end-surfaces-it-is-nine-files-not-one)
- [Testing](#testing)
- [Phasing](#phasing)
- [Open questions](#open-questions)

---

## The ask

For each sync pair, be able to register a callback that informs a remote endpoint that an event has happened. Configurable at three levels — globally, per account, per pair — as a hierarchy where settings can be **overridden** at a lower level, or **set only** at a lower level. Support at least four authorization mechanisms: JWT, Basic, none, and a custom `Authorization` header.

Two things follow from "or set only on the lower level", and they drive the whole configuration model:

1. It is **not** plain scalar override. A pair must be able to introduce a callback that does not exist globally.
2. Therefore the unit of configuration cannot be "the webhook URL" — it has to be a **collection** of callbacks, and a collection needs a merge key. That key is a name.

---

## The constraint that shapes everything: identity

A webhook payload is an **external wire contract**. Whatever identifier it carries, every receiver keys its own state on it, permanently, on systems we do not control and cannot migrate.

### Pair identity is positional today, and that is disqualifying

Pairs are identified by their position in the config list — `f"pair_{i}"`, minted at `sync/engine.py:109` and `:118` and persisted under that name in **six** tables — `sync_state`, `change_tokens`, `conflicts`, `sync_log`, `pending_deletions` and `partial_transfers` (`database.py:47`, `:60`, `:68`, `:83`, `:90`, `:102`). **No pair id is stored in `config.toml` at all**; it is derived from the list index at load time. Remove pair 1 and every pair after it renumbers: `pair_2` silently starts describing a different folder.

[Roadmap item 9](ROADMAP#9--give-each-sync-pair-a-stable-id) records this. Note carefully what it says: the *harm* was fixed in v2.5.0 (history no longer transfers to the pair that takes a removed pair's place), but **the identity scheme is still positional**. Item 9 is not done.

Emitting `pair_2` in a payload would export that renumbering to third parties, where the failure is invisible to us and unfixable by us: the receiver's dashboard silently re-attributes one folder's events to another, and nothing anywhere logs an error.

**Three options were considered:**

| | Option | Verdict |
|---|---|---|
| a | Make roadmap item 9 a hard prerequisite — full stable-id migration of all four tables first | Correct but large; blocks this feature behind a database migration |
| b | Persist a UUID on each pair in `config.toml`, used in payloads, while `pair_N` stays the internal database key | **Recommended** |
| c | Emit a derived composite (`account_id` + `local_path` + `remote_folder_id`) and never emit a pair id | Stable until the user edits the pair's paths — exactly when they least expect their dashboard to fork |

**Recommendation: (b).** Smallest change that makes the payload honest, and it is deliberately the *same shape* item 9 already prescribes: "persist a per-pair uuid on `SyncPair`".

It is worth being exact about how much of item 9 this is, because it is a subset and should not be mistaken for the whole thing. Item 9's cost is not in minting the uuid — it is in **keying the engine and the database on it**: a config-dependent migration across all six `pair_id` tables, plus `Transfers.tsx`, `SyncStatus.tsx` and `ActivityLog.tsx`, each of which rebuilds `pair_${i}` from a list index to join against engine keys, plus a webui rebuild. That join is where a mistake shows wrong data silently instead of erroring.

This proposal needs **none** of that. The uuid is persisted in `config.toml` and used in payloads; `pair_N` remains the internal database and engine key, untouched. So phase 0 is a down-payment on item 9 — it lands the identifier and its derivation for legacy pairs, leaving the migration and the UI join for item 9 proper. When item 9 completes, this uuid becomes the canonical key and the positional scheme is deleted, with no change to the payload contract.

**Rule, stated once and non-negotiable: `pair_N` must never appear in a payload,** nor must the pair's index.

### Call the new field `uid`, because `id` is already taken and already means the index

`ui/src/lib/types.ts:93-106` already declares `id: string` on `SyncPair`, and it holds the **positional index as a string** — `types.ts:24` documents the sibling `DeleteFailsafeLimits.pairs` as "keyed by pair index string (`"0"`, `"1"`) as get_sync_pairs returns". Reusing `id` for a UUID would silently change the meaning of a field every front-end already reads.

So: **`uid` in the TOML pair table and in the payload's `scope.pair_id`**, while the existing API `id` keeps meaning the index for backward compatibility and gains `uid` alongside it. Ugly, but the alternative is redefining a live field.

### Minting the uid must not write during `Config.load`

`Config.load()` deliberately returns defaults **without creating a file** when none exists (`config.py:134-136`), and `daemon/tests/test_feature_first_run_token.py:217` pins that behaviour with a docstring explaining why: if `load()` ever wrote, every install would look like an upgrade and [authentication-on-by-default for new installs](ROADMAP#7--authentication-on-by-default-for-new-installs) would silently stop firing. `docs/ROADMAP.md:101` records the same.

So "mint the uid on load and save the config" — the obvious implementation — breaks a security feature. Instead:

- **New pairs** get `uid = uuid4()` at creation, in the `add_sync_pair` handler.
- **Existing pairs** with no `uid` get a **deterministic** one derived in memory at load: `uuid5(NAMESPACE, f"{provider}|{account_id}|{local_path}|{remote_folder_id}")`. Deterministic matters — a random uuid minted per load would give the receiver a different pair id after every restart.
- That derived value is persisted by the first daemon-side `save()`, turning the derivation into a one-time bridge rather than a permanent dependency on those four fields.

### Account identity is a tuple, not a string

Verified rather than assumed. `Account` (`config.py:31`) has **no** id field, and `SyncPair.account_id` (`config.py:57`) holds a **bare email address** — `daemon.py:257` assigns `pair.account_id = email`, and `cli.py:308` documents `--account` as "Account email".

An account's real identity is the pair **(provider, email)**. `engine.py:158-163` resolves it exactly that way, matching `a.email == pair.account_id and a.provider == provider_name` and only then falling back to email alone — the fallback exists because the same address can be registered on two providers ([#12](https://github.com/ciberkids/cloud-drive-sync/issues/12)). The client registry is keyed `f"{provider}:{email}"` (`engine.py:166`).

Consequences:

- Per-account webhook config is addressed by the composite key `provider:email`, matching the client registry. Email alone must be rejected when it matches more than one account.
- The resolver has to handle two cases the engine papers over. `engine.py:158-163` falls back to matching on email alone, and `account` may end up `None` — a pair whose `account_id` matches nothing still runs, and a pair with an empty `account_id` uses the default client. So: if no account matches, level 2 contributes nothing and resolution proceeds with global + pair (logged once at debug, not as an error — this is a legal state today). If the email-only fallback matches more than one account, the account level is skipped and a warning names the ambiguity, because silently picking one would apply another provider's webhook policy.
- The payload's `scope.account` is an **object** — `{"provider": "gdrive", "email": "…"}` — not a string. A receiver wanting one key can concatenate; one wanting to group by provider should not have to parse.

---

## Configuration model

Three levels, each holding a `webhooks` block. The unit is a **named target**.

```toml
# ── Level 1: global ───────────────────────────────────────────────
[webhooks]
enabled = true

# Applied to every target at this level or below, unless the target sets
# the field itself. Keeps timeouts and retry policy in one place.
[webhooks.defaults]
timeout_seconds = 15
max_attempts = 5
verify_tls = true
include_paths = true
max_files_per_event = 100

[[webhooks.targets]]
name = "ops-bus"                       # the merge key across all three levels
url = "https://ops.example.com/hooks/cds"
events = ["sync.completed", "sync.failed", "deletion.blocked"]
auth = { mode = "bearer", token_env = "CDS_OPS_TOKEN" }

[[webhooks.targets]]
name = "home-assistant"
url = "http://ha.lan:8123/api/webhook/cds"
events = ["sync.completed", "conflict.detected"]
auth = { mode = "none" }

# ── Level 2: per account ──────────────────────────────────────────
[[accounts]]
email = "work@example.com"
provider = "gdrive"

  [accounts.webhooks]
  # Field-level override of an inherited target: same name, one field changed.
  # url, events and auth are all still inherited from global.
  [[accounts.webhooks.targets]]
  name = "ops-bus"
  headers = { X-CDS-Tenant = "work" }

  # A target that exists only for this account. `define` is required because the
  # name is new — without it this would be a load-time error, which is what stops
  # a typo silently merging onto an inherited target.
  [[accounts.webhooks.targets]]
  define = true
  name = "compliance"
  url = "https://audit.example.com/ingest"
  events = ["deletion.blocked", "conflict.*", "account.auth_failed"]
  auth = { mode = "jwt", algorithm = "RS256", key_file = "…", issuer = "cds", audience = "audit" }

# ── Level 3: per pair ─────────────────────────────────────────────
[[sync.pairs]]
uid = "3f7a1c68-2d4e-4f0b-9a11-8c5e6b0d2a94"   # stable; minted at creation
local_path = "/home/me/Documents"
account_id = "work@example.com"
provider = "gdrive"

  [sync.pairs.webhooks]
  # Turn an inherited target off for this pair only.
  [[sync.pairs.webhooks.targets]]
  name = "home-assistant"
  enabled = false

  # Narrow an inherited target's event set for this pair only.
  [[sync.pairs.webhooks.targets]]
  name = "ops-bus"
  events = ["deletion.blocked"]

  # A target only this pair has.
  [[sync.pairs.webhooks.targets]]
  define = true
  name = "photo-indexer"
  url = "http://nas.lan:9000/reindex"
  events = ["file.uploaded", "file.deleted"]
  auth = { mode = "custom", header = "X-API-Key", value_env = "NAS_KEY" }
```

### Absent means inherit — and the sentinel must be `None`, not falsy

This follows precedent rather than inventing one. `SyncPair.conflict_strategy` is `""` for "inherit global" (`config.py:60`); `max_deletions_per_sync` and `deletion_window_seconds` are `int | None = None` for the same purpose (`config.py:63`, `:65`). `Config.save` **omits those keys entirely** when unset (`config.py:276-286`), so an inherited value never gets frozen into the file as a literal.

The asymmetry between those two sentinels is load-bearing, not stylistic. `config.py:61-62` records that `0` *disables* the delete guard for a pair — so a truthiness test would drop an explicit `0` and silently re-inherit the global `100`. The rule that generalises:

> A tri-state field whose falsy value is meaningful **must** be `bool | None` / `int | None` and tested with `is not None`. Only `""`-means-inherit may use truthiness.

Webhook config is full of exactly that shape: `enabled = false` and `verify_tls = false` and `include_paths = false` and `max_files_per_event = 0` are all meaningful falsy values. Every one of them is `X | None`, defaulting to `None`, saved only when `is not None`. Getting this wrong produces the worst class of bug this feature can have: a user disables a webhook, the setting is silently dropped as falsy, and the webhook keeps firing.

### `Config.save()` erases anything it does not enumerate

The single most load-bearing implementation fact. `load()` reads key-by-key with explicit `.get()` calls (`config.py:141-231`); `save()` rebuilds the dict from scratch (`config.py:245-316`). Neither iterates dataclass fields and neither preserves unrecognised TOML.

So a `[webhooks]` table that is not enumerated in **both** functions is dropped on load *and erased from the file* on the next save — and every IPC settings handler ends in `self._config.save()` (e.g. `handlers.py:647`, `:724`). A user who hand-edits webhook config and then changes an unrelated setting in the UI loses it silently.

Nesting is hand-marshalled one level deep on both sides (`SyncRules` at `config.py:173-179` and `:290-295` is the only precedent). Three levels of webhook config therefore means **six** hand-written marshal blocks — three in `load`, three in `save` — and they must agree. A round-trip test (`load` → `save` → `load`, assert equality including every tri-state at each level) is the cheapest defence and should be written before the marshalling.

Two smaller notes from the same file: there is no `version` key and no unknown-key warning on `config.toml`, so a typo in a target field is silent; and `save()` writes in place with no atomic replace (`config.py:330-331`), so an interrupted save truncates the config — pre-existing, but a three-level config makes the file bigger and the window wider.

---

## Resolution algorithm

For a pair *P* belonging to account *A*, the effective target list is:

1. Start with an **ordered map** `{name → target}` from `[webhooks].targets`. Definition order is preserved, so delivery order between targets is predictable.
2. Apply *A*'s targets in order. An existing name is **deep-merged** field by field (present fields overwrite, absent fields inherit). A new name is **appended**.
3. Apply *P*'s targets the same way.
4. Apply each level's `defaults` block, but only to fields the target itself never set — a target's own value always beats any level's `defaults`.
5. Drop every target whose effective `enabled` is `false`.
6. Drop everything if the effective `webhooks.enabled` is `false`. This is tri-state (`None` = inherit) at the account and pair levels, so `enabled = false` on an account is a kill switch for all its pairs, and a pair may set `enabled = true` to opt back in.
7. Reject at load time, with a logged error naming the offending level and the target dropped: any target that ends up without a `url` or with an empty `events`; any `auth` or `signature` table lacking its mandatory fields (see [below](#auth-needs-a-mode-and-step-7-needs-to-check-more-than-url)); and any target dropped at step 5 because `enabled = false` was *inherited* rather than set at the dropping level. A half-inherited target must not silently become a no-op.

There is already a precedent for a shared resolver, and it is worth following rather than reinventing: `failsafe.effective_limits(global_max, pair_max=None)` (`failsafe.py:124-135`) resolves exactly this global/pair inheritance, and its docstring states the tri-state rule in the same terms used above — "``None`` means inherit; ``0`` is a deliberate opt-out". It is called at `engine.py:638-641` and covered for all four cases (override, inherit, explicit `0`, negative) by `test_feature_delete_failsafe.py:142-157`.

The sharper point is what sits three lines below that call: `engine.py:644-648` resolves the sibling field `deletion_window_seconds` **ad hoc**, inline, instead of through a helper — as do `config.py:201` and `engine.py:206-207`. So the codebase has one good example and several ad-hoc ones. This feature should ship a single testable `resolve_targets(config, account, pair)` in the `effective_limits` spirit rather than adding more inline resolution, and the CLI and REST should expose its output directly so users can see what the daemon sees.

### List and map fields need explicit rules

This is where naive "deep merge" produces designs that cannot express what users want.

| Field | Kind | Rule | Why |
|---|---|---|---|
| `events` | list | **Replace** | A pair writing `events = ["deletion.blocked"]` unambiguously means *only* that. Append-only merge makes narrowing impossible, and narrowing is the common case. |
| `events_add` / `events_remove` | list | **Level-local operation — applied at that level's turn, then discarded** | See below. Treating them as ordinary inheritable fields breaks the `events` replace guarantee. |
| `headers` | table | **Per-key merge** | Lets an account add a tenant header without restating the global ones. |
| `headers_remove` | list | **Level-local operation**, same as the event deltas | TOML has no `null`, so removal needs its own list — but it must not be inherited. |
| `auth` | table | **Replace as a whole**; `mode` mandatory whenever present | Half-merging two modes (a `basic` username with a `bearer` token) yields a request that is neither. Auth is atomic. |
| `signature` | table | **Replace as a whole**; `secret`/`secret_env` mandatory whenever present | Same reason as `auth`. Half-merging would produce a signature computed with an inherited secret under a new header name. |
| `define` | bool | **Level-local declaration**, never inherited | Declares that this entry introduces a new name rather than overriding an inherited one. See [below](#name-collisions-are-silent-and-the-model-must-make-intent-explicit). |

`events` entries support one glob segment: `conflict.*` matches `conflict.detected` and `conflict.resolved`; `*` matches everything.

### Deltas are operations, not fields

The obvious implementation — make `events_add` an ordinary target field and let step 2/3 merge it — is wrong, and wrong in the direction that breaks the guarantee the table above makes.

If `events_add` inherits like any other field, then: an account sets `ops-bus.events_add = ["file.uploaded"]`; a pair sets `ops-bus.events = ["deletion.blocked"]` intending *only* that. The pair never wrote `events_add`, so it inherits the account's, and the effective set is `[deletion.blocked, file.uploaded]`. The narrowing the table calls "the common case" has silently failed.

The reverse is worse: a global `events_remove = ["sync.failed"]` is inherited by every level below, so a pair that explicitly writes `events = ["sync.failed"]` ends up with an empty set, gets dropped by step 7, and the log says "empty events" without naming the inherited `events_remove` that caused it.

So: during the merge, each level's `events_add`, `events_remove` and `headers_remove` are applied to the accumulator **at that level's turn and then discarded**. They never reach the merged target for a lower level to inherit. With that rule the pair's `events` replace happens after the account's `events_add` and wins, which is what the table promises. Both failing cases above become tests.

### `auth` needs a `mode`, and step 7 needs to check more than `url`

Atomic replace has a sharp edge worth naming. A pair that wants only a different token writes:

```toml
auth = { token_env = "OTHER_TOKEN" }
```

Atomic replace discards the inherited `mode = "bearer"`. If a `mode`-less `auth` table is then treated as a default, the most plausible default is `none` — a **silent downgrade to unauthenticated POSTs** against an endpoint that expects a bearer token. Either a 401 flood, or worse, delivery accepted without a credential.

Two rules follow:

1. **`mode` is mandatory whenever an `auth` table is present at any level.** A `mode`-less `auth` is a load-time rejection naming the level that wrote it — never a silent default.
2. **Step 7 validates per-mode completeness, not just `url` and `events`.** `basic` needs `username` + one of `password`/`password_env`; `bearer` needs one of `token`/`token_env`; `jwt` needs `algorithm` + exactly one of `key`/`key_env`/`key_file`; `custom` needs `header` + one of `value`/`value_env`; `signature` needs one of `secret`/`secret_env`. Without this, an `auth = { mode = "jwt", algorithm = "RS256" }` that lost its inherited `key_file` to atomic replace passes resolution and fails once per event at delivery, where the failure is indistinguishable from a dead endpoint.

For the "change just the token" case that atomic replace makes awkward, the `_env` indirection is usually the better answer anyway: keep the inherited `auth` block and point the lower level at a different environment variable by overriding nothing at all — one variable per scope in the unit file.

### Name collisions are silent, and the model must make intent explicit

A name is the merge key, which means a lower level cannot tell "override the inherited target" from "define my own" — and the failure mode is the one this document already calls fatal.

Take the global `home-assistant` target and suppose it carries `enabled = false`. A pair then writes what it believes is a brand-new target of its own:

```toml
[[sync.pairs.webhooks.targets]]
name = "home-assistant"
url = "http://nas.lan:8123/hook"
events = ["file.uploaded"]
```

Steps 2–3 merge it onto the disabled global entry. The pair set no `enabled`, so `enabled = false` is inherited, and step 5 drops it. Step 7 does not catch it — the target has both a `url` and non-empty `events`. The user *enabled* a webhook and it silently never fires. The pair author also has no way to discover the global namespace: names are global, collisions are silent, and a pair-scoped read shows only the pair's own block.

**Recommendation: `define = true`, required only when introducing a new name.** Overriding an inherited target — the common case — stays as terse as it is above, and *defining* one is explicit:

- `define = true` and the name **already exists** at a higher level → load-time error, naming both levels.
- `define` absent and the name **does not exist** at any higher level → load-time error, suggesting `define = true`.
- Anything else resolves as described.

That kills the silent case in both directions without putting ceremony on every entry. It is a two-line check and it turns the worst failure mode in the merge model — a webhook the user enabled that never fires — into a message at startup.

Two supporting requirements, worth having even with `define`: step 7 also logs an error whenever a target is dropped because `enabled = false` was **inherited** rather than set at the dropping level, and the resolver output labels such a target "disabled at global" so `--explain` shows the cause rather than an absence.

---

## Worked example

Resolving the configuration above for pair `3f7a1c68…` (`/home/me/Documents`, account `gdrive:work@example.com`):

| Step | `ops-bus` | `home-assistant` | `compliance` | `photo-indexer` |
|---|---|---|---|---|
| 1. global | url=ops, events=`[sync.completed, sync.failed, deletion.blocked]`, auth=bearer | url=ha, events=`[sync.completed, conflict.detected]`, auth=none | — | — |
| 2. account `gdrive:work@…` | + `headers={X-CDS-Tenant: work}` | *(untouched)* | **added**: url=audit, auth=jwt/RS256 | — |
| 3. pair | `events=[deletion.blocked]` *(replaced)* | `enabled=false` | *(untouched)* | **added**: url=nas, auth=custom |
| 4. defaults | timeout=15, attempts=5, verify_tls=true, max_files=100 | — | timeout=15, … | timeout=15, … |
| 5. drop disabled | kept | **dropped** | kept | kept |

**Effective for this pair: three targets.**

- `ops-bus` → `https://ops.example.com/hooks/cds`, only `deletion.blocked`, `Authorization: Bearer $CDS_OPS_TOKEN`, `X-CDS-Tenant: work`
- `compliance` → `https://audit.example.com/ingest`, `deletion.blocked` + both conflict events + `account.auth_failed`, minted RS256 JWT
- `photo-indexer` → `http://nas.lan:9000/reindex`, `file.uploaded` + `file.deleted`, `X-API-Key: $NAS_KEY`

A **second** pair on the same account with no `[sync.pairs.webhooks]` block resolves to: `ops-bus` (all three global events, with the tenant header), `home-assistant` (still enabled — the disable was pair-scoped), and `compliance`. That contrast is the point: the same account-level change reached both pairs, and the pair-level change reached exactly one.

This table is the acceptance test for the resolver. An implementation that cannot reproduce it has the merge rules wrong.

---

## Event taxonomy

The daemon already emits events; this feature exposes them rather than adding instrumentation. Two existing sources, both enumerated exhaustively below because the gaps matter as much as the coverage.

### What the notify callback actually emits — all eight call sites

| Internal name | Params | Site |
|---|---|---|
| `sync_complete` | `pair_id, uploaded, downloaded, mkdirs, deleted, errors, files{uploaded,downloaded,deleted,conflicted}` | `engine.py:482` |
| `status_changed` | `pair_id, status` | `engine.py:496` |
| `delete_blocked` | `pair_id, message` | `engine.py:714` |
| `activity_stopped` | `account_id, pairs, cancelled` | `engine.py:917` |
| `activity_resumed` | `account_id, pairs` | `engine.py:963` |
| `conflict_detected` | `id, path, local_md5, remote_md5` | `conflict.py:168` (live) |
| `conflict_detected` | same | `conflict.py:90` (**dead** — only reachable from `test_conflict_resolver.py`) |
| `transfer_progress` | `pair_id, path, direction, bytes, total, speed, speed_formatted` | `executor.py:270` |

Three things this list reveals, all of which change the design:

1. **`status_changed` only ever carries `"idle"`.** No other value is emitted anywhere. A `pair.status_changed` webhook would be a constant, so it is deferred rather than shipped as a stub — the interesting transitions (`syncing`, `error`, `paused`) are not instrumented yet.
2. **There is no `conflict_resolved` notification, and for a user-resolved conflict there is no log row either.** `_resolve_conflict` (`handlers.py:746-753`) only calls `set_user_resolution(...)` — which resolves a Future (`conflict.py:219-228`) — and returns. The one `conflict`-action resolution row is written for *auto*-resolution and explicitly not for the user path (`engine.py:352-361`, guarded by `if effective_strategy != "ask_user":`). So `conflict.resolved` is entirely new instrumentation, and adding it also closes a real observability gap.
3. **`delete_blocked` throws away almost everything it knows.** At the emission point (`engine.py:681-718`) the full `DeletionBreach` is in scope — `direction` (local/remote), `count`, `limit`, `tracked`, `sample` (up to 20 sorted paths), `recent`, `window_seconds`, plus derived `ratio` and `total_in_window` — and it is *already persisted* by `record_pending_deletions`. The notification sends `{pair_id, message}`, a prose string. The webhook payload must carry the structured fields; a receiver cannot act on a sentence.

### The activity log, and one trap in it

`add_log_entry` (`database.py:503`) is the only `INSERT INTO sync_log`, so every activity row goes through one place. Actions: `sync`, `conflict`, `delete_blocked`, `auth`, plus the `ActionType` values written verbatim by `executor.py:596` — `upload`, `download`, `delete_local`, `delete_remote`, `mkdir`, `move`.

**The trap:** statuses are `in_progress`, `success`, `error` — *and* `ok`. The engine writes `"success"`; the executor writes `"ok"` (`executor.py:109`) for the same concept. This is not cosmetic: `count_recent_deletions` (`database.py:625`) filters `status = 'ok'` exactly, and that query is the delete fail-safe's sliding window. Any webhook work that touches or normalises log statuses must leave `'ok'` alone on deletion rows, or it silently disables delete protection. Deriving webhook events from the log means matching both spellings.

### Public names are deliberately not the internal names

Internal notify names are UI-coupled and will be refactored; exporting them makes every internal rename a breaking change for third parties. The webhook layer owns a stable, dotted vocabulary and maps to it in one table in one file.

| Public event | Source | Volume | Default | Cost — what has to be built |
|---|---|---|---|---|
| `daemon.started` / `daemon.stopping` | daemon lifecycle | trivial | on | new emission |
| `sync.started` | pass start | low | on | new emission |
| `sync.completed` | `sync_complete` (`engine.py:482`) | low | **on** | **partly new** — see below |
| `sync.failed` | — | low | **on** | **entirely new** — see below |
| `conflict.detected` | `conflict.py:168` | low | **on** | free |
| `conflict.resolved` | resolve handler | low | **on** | new emission |
| `deletion.blocked` | `engine.py:714` | rare | **on** | free, but needs the full breach, not the prose |
| `pair.paused` / `pair.resumed` / `pair.added` / `pair.removed` | IPC handlers | trivial | on | new emission |
| `file.uploaded` / `.downloaded` / `.deleted` / `.moved` | `executor.py:596` | **high** | off | **needs a second hook** — see below |
| `transfer.progress` | `executor.py:270` | **very high** | off, rate-limited | free |
| `account.added` / `.removed` | IPC handlers | trivial | on | new emission |
| `account.auth_failed` | `daemon.py:542` | rare | **on** | needs the loop fix below |
| `activity.stopped` / `activity.resumed` | `engine.py:917`, `:963` | rare | on | free |
| `pair.status_changed` | `engine.py:496` | — | **deferred** | needs real statuses first |
| `webhook.test` | UI/CLI Test button | on demand | n/a | new |

`deletion.blocked` deserves the emphasis. It is the event where a human needs to be told *now*, on a channel they watch, and it is what justifies the whole feature to a headless NAS user. It is also, usefully, one of the cheapest: the emission already exists.

### The two flagship events are not free, and one barely exists

This is the correction that most changes the size of phase 1, so it is stated bluntly rather than buried.

**`sync_complete` is emitted in exactly one place: inside `_initial_sync`.** The continuous loops never emit it. `_local_change_loop` ends a successful batch with `ps.last_sync = datetime.now(UTC)` (`engine.py:602`) and nothing else; `_remote_poll_loop` does the same (`engine.py:763`). Verified by enumerating every invocation of the callback in the package — there are six, at `engine.py:482`, `:496`, `:714`, `:917`, `:963` and `executor.py:270`, and not one is in either loop.

So a naive implementation gives the user **one `sync.completed` per pair per daemon start, and silence for the rest of the process lifetime.** Somebody who wires up "tell me when a sync finishes" gets an event at boot and never hears from it again — and would reasonably conclude the feature is broken.

**`sync.failed` has no notification source at all.** `engine.py:513` is a `log.exception` plus a `sync_log` row; it never touches the callback. Both continuous loops are worse: their `except Exception` handlers (`engine.py:604`, `:764`) only `log.exception` — they do not even write a log row. So a pair whose continuous sync is failing every cycle is currently invisible to both the activity log and any bus built over the notify callback.

**The per-file events need a second hook point.** `executor.py:596` is inside `_log_action`, a pure `add_log_entry` call — it is on the database path, not the notify path. Reaching those events means either new call-site emissions in the executor or a hook inside `Database.add_log_entry` (`database.py:503`). The latter is tempting because it catches everything at once, but it sits on the single unguarded `aiosqlite` connection and on the query path the delete fail-safe depends on, so **new call-site emissions in the executor are the recommendation** and the `add_log_entry` hook is explicitly rejected.

Phase 1 therefore includes new emissions, not just a dispatcher: `sync.completed` at the end of both continuous loops (with a real `duration_seconds`), and `sync.failed` in `_initial_sync`'s `except` **and** both loops' `except` handlers. Fixing the loops' silent-failure gap is worth doing on its own merits, independent of webhooks.

### `account.auth_failed` has a prerequisite bug

`_log_auth_event` (`daemon.py:605`) schedules its database write via `asyncio.get_event_loop()` (`daemon.py:621`) with a bare `except RuntimeError: pass` (`daemon.py:625-626`). But `_do_auth` runs on a worker thread — `await asyncio.to_thread(self._auth_callback, …)` at `handlers.py:915` and `:1110`.

Verified on this project's own interpreter (CPython 3.12.13):

```
RuntimeError: There is no current event loop in thread 'asyncio_0'.
```

So **all four `_log_auth_event` rows are silently swallowed on the IPC and HTTP paths** (auth started, succeeded, failed, code-exchange succeeded). The daemon holds a usable loop reference at `daemon.py:184` (`self._loop`), so the fix is a one-line substitution for `asyncio.get_event_loop()`.

The activity log is not wholly blank for auth, which is why this has gone unnoticed: `_start_auth` writes its own rows from the async context at `handlers.py:919-922` and `:930-933`, and `_logout` at `:1278-1285`. Those survive. It is specifically the daemon-side lifecycle rows that vanish — including the failure one, which is the one `account.auth_failed` needs.

This is a pre-existing bug, not one this feature introduces — but `account.auth_failed` would inherit it and appear to be a webhook failure. It is listed as a phase-1 prerequisite, and it is worth its own issue regardless of whether webhooks ever ship.

Events that are not pair-scoped (`daemon.*`, `account.*`, `activity.*`) resolve their targets at the level that owns them — global for `daemon.*`, the account's merged view for `account.*` — and their `scope` block omits the pair fields rather than inventing a placeholder.

---

## The payload

One `POST`, `Content-Type: application/json`, UTF-8, one event per request. No batching in v1 — see [Open questions](#open-questions).

```json
{
  "schema_version": 1,
  "event": "sync.completed",
  "event_id": "9f2b0c14-7e83-4a51-b6d2-1c8f4e5a7b30",
  "occurred_at": "2026-08-20T12:34:56.789Z",

  "source": {
    "app": "cloud-drive-sync",
    "version": "2.4.5",
    "instance_id": "b71e4f9a-0c33-4d18-8f52-6a1b9e2d7c40"
  },

  "scope": {
    "pair_id": "3f7a1c68-2d4e-4f0b-9a11-8c5e6b0d2a94",
    "pair_label": "Documents → Drive/Docs",
    "account": { "provider": "gdrive", "email": "work@example.com" },
    "local_path": "/home/me/Documents",
    "remote_folder_id": "0B9aXqZ1kLmNoPqRs"
  },

  "delivery": {
    "target": "ops-bus",
    "attempt": 1,
    "sent_at": "2026-08-20T12:34:56.902Z"
  },

  "data": {
    "uploaded": 12,
    "downloaded": 3,
    "deleted": 0,
    "mkdirs": 1,
    "errors": 0,
    "duration_seconds": 8.4,
    "files": {
      "uploaded": ["notes/todo.md", "img/a.png"],
      "downloaded": ["report.docx"],
      "deleted": [],
      "conflicted": []
    },
    "files_truncated": false
  }
}
```

The envelope is identical for every event; only `data` changes. `deletion.blocked` — the event most likely to be wired to a pager — carries the structured breach rather than the prose string the internal notification uses:

```json
{
  "event": "deletion.blocked",
  "data": {
    "direction": "remote",
    "count": 4213,
    "limit": 100,
    "tracked": 5001,
    "ratio": 0.84,
    "window_seconds": 60,
    "total_in_window": 4213,
    "sample": ["Photos/2019/IMG_0001.jpg", "Photos/2019/IMG_0002.jpg"],
    "sample_truncated": true,
    "pair_paused": true,
    "resolution_required": true
  }
}
```

`ratio` is what makes this actionable: 4213 of 5001 tracked files is a wiped source, whereas 4213 of 4,000,000 is a folder cleanup. A receiver can page on the ratio and ignore the count.

### Field notes

**`schema_version`** — an integer, bumped only on a breaking change; additive fields do not bump it. The docs must state that receivers ignore unknown fields. That one sentence is what makes additive evolution possible.

**`event_id`** — a UUID generated once per event and **kept across retries**. Delivery is at-least-once, so this is the receiver's dedup key. Regenerating it per attempt is the most common way this field gets implemented wrong, and it makes the field worthless.

**`occurred_at`** vs **`delivery.sent_at`** — when it happened vs when this attempt left. Both RFC 3339, UTC, millisecond precision. A retry three minutes later must not look like it happened three minutes later.

**`source.instance_id`** — a UUID minted once per install, stored beside the config. Two daemons (laptop and NAS) syncing the same account are otherwise indistinguishable at the receiver, and telling them apart is exactly what a dashboard needs. Minting it must not happen inside `Config.load` — same constraint as the pair `uid`.

**`scope.pair_label`** — human-readable, for display, documented as **not** an identifier. It changes when the user renames things.

### The payload-size bomb, and the fix

`sync_complete` already carries full path lists — `files: {uploaded, downloaded, deleted, conflicted}` at `engine.py:489-494`. On the initial sync of a large library that is **tens of thousands of paths in a single POST**. The first real user hits this immediately, and the failure looks like a webhook bug rather than a payload-design bug.

Specified, not left to the implementer:

- `max_files_per_event` (default **100**, `0` = omit the lists). Each list truncated independently.
- `files_truncated: true` whenever any list was cut. The **counts** are always the true totals, so a receiver that only wants numbers is never misled.
- A hard ceiling on the serialised body (default **1 MiB**). A payload still over it after truncation is sent with all `files` lists dropped and `files_truncated: true`, plus one activity-log entry. Truncating always beats failing to deliver `sync.completed`.
- `transfer.progress` off by default and, when on, rate-limited to one event per pair per *n* seconds (default 5). Without that it emits per chunk and floods both the queue and the receiver.

### Paths are personal data

A webhook ships the user's local filenames to a third party. `/home/me/Documents/divorce/settlement-draft.docx` in a payload is a meaningful disclosure, and the destination may be a SaaS log aggregator.

- `include_paths` (default `true`, per target). When `false`, `files` lists are omitted and per-file events carry only `path_sha256`, an extension and a size — enough to correlate and count, not to read.
- `scope.local_path` obeys the same flag.
- This belongs in the UI next to the URL field, not only in the config reference. A user pointing a webhook at an external service should see the sentence before they save.

---

## Authorization mechanisms

Five modes. Four were requested; the fifth is the one actually missing from the request, and it composes with the others rather than replacing them.

### "JWT" is three different features

The request names one thing and could mean any of three:

1. A **static, pre-issued** JWT forwarded verbatim. Byte-for-byte identical to `bearer` — it adds a config field and nothing else.
2. A **daemon-minted** JWT, signed per request with configurable claims and a short expiry. The only variant that is genuinely a distinct mechanism, and the only one with replay resistance.
3. An **OAuth2 client-credentials** exchange — fetch a token from an authorization server, cache it, refresh on expiry.

**Recommendation: implement (2) as `mode = "jwt"`,** and document that (1) is served by `bearer`. (3) is deliberately out of scope: it is a token-lifecycle subsystem (endpoint config, caching, refresh races, clock skew, its own failure modes) bolted onto a feature whose job is to send one POST. It belongs in its own proposal if anyone asks.

### The dependency question, answered rather than assumed

Checked against `daemon/pyproject.toml` and CI:

- **`aiohttp>=3.9.0` is a hard dependency** (`pyproject.toml:22`). An async client *and* a test server, at zero cost. Use it.
- **`httpx` is only in the `nextcloud` extra and `dev`** (`pyproject.toml:28`, `:43`). Not present in a base install — do not reach for it.
- **`cryptography>=41` is a hard dependency**, already used for credential encryption.
- **`PyJWT` is *not* a declared dependency, and its actual provenance makes it a worse trap than simply being absent.** It is present in this venv transitively — but not from any provider extra. The distribution that requires it is **`mcp`**, which declares `pyjwt[crypto]>=2.10.1` unconditionally. And `mcp>=2,<3` is in the **`dev`** extra (`pyproject.toml:43`), which is exactly what CI installs.

  So PyJWT **is** importable in CI on all three runners, and **is not** importable in a production base install (`mcp` is not in `[project].dependencies`). That is the worst possible arrangement: a test exercising a PyJWT-based implementation would be **green in CI and crash with `ModuleNotFoundError` for any user who installed without the `mcp` extra** — the DEB, RPM, AppImage, Flatpak and standalone-daemon users among them. Depending on it accidentally, through a *dev* dependency of an unrelated optional feature, is worse than depending on it openly.

**Therefore JWT minting is implemented without a JWT library.** A JWT is `base64url(header).base64url(claims).base64url(signature)`:

- **HS256** — `hmac.new(key, msg, hashlib.sha256)`. Pure standard library.
- **RS256** — `cryptography`'s `padding.PKCS1v15()` with `hashes.SHA256()`. Already a hard dependency.

Roughly forty lines, no new dependency, identical behaviour in CI and in every packaging target (Flatpak, Docker, MSI, DEB, RPM). If a JWT library is later wanted for broader algorithm coverage, it should be added as a *declared* dependency by deliberate decision, not relied on by accident.

### The five modes

| Mode | Config | Header(s) sent | Dependency |
|---|---|---|---|
| `none` | — | none | — |
| `basic` | `username`, `password` \| `password_env` | `Authorization: Basic base64(u:p)` | stdlib |
| `bearer` | `token` \| `token_env` | `Authorization: Bearer <token>` | stdlib |
| `jwt` | `algorithm` (`HS256`/`RS256`), `key`/`key_env`/`key_file`, `issuer`, `audience`, `subject`, `ttl_seconds`, `extra_claims` | `Authorization: Bearer <minted JWT>` | stdlib / `cryptography` |
| `custom` | `header`, `value` \| `value_env` | `<header>: <value>` — any header name, including a non-standard `Authorization` scheme | stdlib |

The four single-key modes fit in an inline table:

```toml
[[webhooks.targets]]
name = "open"
url = "http://ha.lan:8123/api/webhook/cds"
events = ["sync.completed"]
auth = { mode = "none" }

[[webhooks.targets]]
name = "legacy-basic"
url = "https://old.example.com/hook"
events = ["sync.completed"]
auth = { mode = "basic", username = "cds", password_env = "CDS_HOOK_PW" }

[[webhooks.targets]]
name = "ops-bus"
url = "https://ops.example.com/hooks/cds"
events = ["deletion.blocked"]
auth = { mode = "bearer", token_env = "CDS_OPS_TOKEN" }

[[webhooks.targets]]
name = "photo-indexer"
url = "http://nas.lan:9000/reindex"
events = ["file.uploaded"]
auth = { mode = "custom", header = "X-API-Key", value_env = "NAS_KEY" }
```

`jwt` needs more keys than fit on one line, and **TOML 1.0 does not permit a newline inside an inline table** — `tomllib` rejects it, so the config reference must show the sub-table form rather than a wrapped inline table:

```toml
[[webhooks.targets]]
name = "compliance"
url = "https://audit.example.com/ingest"
events = ["deletion.blocked", "conflict.*"]

  [webhooks.targets.auth]
  mode = "jwt"
  algorithm = "HS256"
  key_env = "CDS_JWT_KEY"
  issuer = "cloud-drive-sync"
  audience = "ops.example.com"
  subject = "pair:3f7a1c68"
  ttl_seconds = 300
  extra_claims = { scope = "sync:events" }
```

Minted claims: `iss`, `aud`, `sub`, `iat`, `exp` (from `ttl_seconds`, default 300), `jti` set to the `event_id` — so the receiver can reject replays with the same machinery it uses for dedup — plus `extra_claims`. A **fresh token per attempt**: reusing one across a retry that lands after expiry is a self-inflicted 401.

`custom` intentionally allows `header = "Authorization"`, covering schemes we do not model (`Authorization: Token abc`, `Authorization: HMAC …`). Additional non-credential headers go in the target's `headers` table; `auth.mode = "custom"` is for the one that *is* the credential, so it can be redacted as one.

### The fifth mechanism: HMAC body signature

Every mode above authenticates the *sender*. None lets the receiver verify the *payload*. This is what GitHub, Stripe and Slack all ship, and it is the difference between "someone with the token posted this" and "this exact body came from the daemon, unaltered and not replayed".

```toml
[[webhooks.targets]]
name = "ops-bus"
url = "https://ops.example.com/hooks/cds"
events = ["sync.completed", "deletion.blocked"]
auth = { mode = "bearer", token_env = "CDS_OPS_TOKEN" }

  [webhooks.targets.signature]
  secret_env = "CDS_SIGNING_SECRET"
  algorithm = "sha256"
  header = "X-CDS-Signature"
  timestamp_header = "X-CDS-Timestamp"
```

Sends `X-CDS-Timestamp: <unix seconds>` and `X-CDS-Signature: sha256=<hex hmac>` over the exact bytes `f"{timestamp}.{body}"` — the timestamp is inside the signed material so a captured body cannot be replayed later, and the HMAC is computed over the **serialised bytes actually sent**, never over a re-serialisation of the dict.

It **composes** with any `auth.mode`, including `none` — which is the recommended pairing for a receiver that can verify signatures, since then no long-lived credential is transmitted at all.

An addition to the four requested mechanisms, not a substitute for any.

---

## Where the secrets live

A webhook bearer token or basic password is a credential. Three defensible routes; the choice should be explicit because **the repo currently contains two contradictory answers**.

| Route | Mechanism | For |
|---|---|---|
| **A** — plaintext in `config.toml` | Already `0600`, and already holds `http.token` | Simplicity; direct in-repo precedent |
| **B** — encrypted at rest | Fernet + machine-id key, as `auth/credentials.py` does | Strongest at-rest posture |
| **C** — environment indirection | Config stores `token_env = "NAME"`; the value is read from the process environment | Containers, Kubernetes secrets, systemd `EnvironmentFile`, CI |

**Recommendation: support C for every secret field, with A as the fallback.**

- **C sidesteps at-rest entirely** and costs almost nothing. It is what every container deployment already wants — the value never touches a config file, so a config backup carries no credential. Hence the `_env` twin on every field above: `token`/`token_env`, `password`/`password_env`, `value`/`value_env`, `key`/`key_env`/`key_file`.
- **A is honest about the existing threat model.** `HttpConfig.token` (`config.py:110`) — the credential for `/api/*` and the entire web UI, whose blast radius is spelled out at `config.py:318-324`: adding and removing cloud accounts, changing where data syncs, switching off delete protection — is stored as **plaintext TOML at 0600** (`config.py:251`, `:325-329`). A webhook token beside it does not lower the bar.
- **B is deferred, and the reason is mechanical rather than philosophical.** The existing helpers (`write_encrypted_json`, `_write_private`) are whole-file and bytes-only; they physically cannot encrypt one field inside a TOML document. "Encrypted webhook secret in config.toml" is not reachable without new crypto code. On top of that, [roadmap item 8](ROADMAP#8--encrypt-onedrive-box-and-nextcloud-credentials) documents a subtlety that would apply and is easy to get wrong: the salt must live *beside* the credential, not in the shared data directory, because config and data are separate volumes in a container — a shared salt lets ciphertext and key be restored apart, and salt creation *mints a new salt rather than failing*, turning a config-only restore into silent permanent loss. Deciding where `config.toml`'s salt lives is a real decision that should not ride along on a feature about HTTP callbacks.

One hard constraint inherited from [item 7](ROADMAP#7--authentication-on-by-default-for-new-installs): **no secret may be minted during `Config.load`.** `test_feature_first_run_token.py:217` pins that `load()` does not create a file, and a default signing key generated at load time would break it.

### The masking trap

This one bites predictably, so it is specified up front.

A read API that returns secrets is unacceptable — the web UI would put them in a browser and in every screenshot. But an API that returns `"***"` and a UI that saves back what it read will **overwrite the real secret with three asterisks**, and the user finds out when the webhook starts returning 401.

Rule: read paths return `"token": null` plus `"token_set": true`. Write paths treat an **absent** field as "leave unchanged" and an explicit `null` as "clear it". The UI renders an empty password box labelled *"unchanged — type to replace"*. A test asserts that a GET → PUT round-trip with no edits leaves the stored secret byte-identical.

---

## Delivery semantics

The governing constraint: **a webhook must never slow down or block a sync.** A hung endpoint is a normal condition, not an exception.

- **Emission does O(1) work per target and never serialises.** Construct one immutable raw event (name, params, `occurred_at`, `event_id`) and `put_nowait` a reference to it onto each matching target's queue. No `await` on the network from any sync code path, ever.
- **All expensive work happens in the worker**: target resolution, path truncation, `json.dumps`, the size-ceiling re-check, HMAC signing and JWT minting. This is not a micro-optimisation. Serialising on the emit side would put unbounded synchronous CPU on the event loop with no `await` point — for `sync_complete` on a large initial sync that is tens of thousands of paths, serialised *once per resolved target*, plus an RSA signature per attempt for `mode = "jwt"`. The daemon is single-loop: aiohttp, aiosqlite, the IPC server and every other pair's transfers all share it, so hundreds of milliseconds of uninterruptible CPU inside `await self._notify_callback(...)` at `engine.py:482` freezes all of them. Enqueue the raw event; let the worker pay.
- **One worker per `target_key`**, so per-target ordering holds. Targets are independent — a dead endpoint must not delay a healthy one.

### `name` is the merge key; `target_key` is the delivery key

These must not be the same thing, and conflating them is a real bug rather than a naming quibble.

`name` is only unique *within one pair's resolution*. Nothing in the model forbids pair A defining `photo-indexer → http://nas-a:9000` and pair B defining `photo-indexer → http://nas-b:9000` — both are legitimate "a target only this pair has" definitions. And the same name deliberately resolves to different `auth`, `headers` and `verify_tls` per pair; that is the entire point of the hierarchy. So "the target" is not one object.

If queue, worker, circuit-breaker and counter state are keyed on `name`, then `nas-b` going dark opens the breaker on `nas-a`'s deliveries, and two unrelated endpoints share one 1000-slot queue.

**Define `target_key` as `(defining_scope, name)`**, where `defining_scope` is the level that first introduced the name — `global`, `provider:email`, or a pair `uid`. All delivery state is keyed on `target_key`. `webhooks.status` and the status payload emit `target_key` alongside the display `name`, and a resolver test asserts that two pairs defining the same name with different URLs get two independent breakers.
- **Bounded queue** (default 1000 per `target_key`). When full, drop the **oldest** and increment a counter, because a monitoring receiver wants current state more than history. One rate-limited activity-log entry records the drop, so it is visible rather than silent. Note that queueing the *raw* event rather than a serialised body also bounds memory sensibly: N targets hold N references to one shared immutable event, not N independently truncated copies of a payload that may carry tens of thousands of paths. Worst case is one event object per distinct event, not per (event, target) pair.
- **A priority lane that is never dropped**, holding `deletion.blocked`, `sync.failed` and `account.auth_failed`. These are the events a human is waiting for; discarding one because a chatty `file.uploaded` stream filled the queue would be the worst possible behaviour. Small fixed capacity, and if *that* fills, the daemon logs loudly.
- **Timeouts**: connect 5 s, total `timeout_seconds` (default 15).
- **Success** is any `2xx`. The response body is ignored and never logged — it is attacker-influenced text.
- **Circuit breaker**: after *n* consecutive failures (default 10) a target goes `unhealthy`, drops to slow-probe (one attempt per 5 minutes), and surfaces in `status` and the UI. A dead endpoint should be visible, not an infinite background retry.
- **At-least-once, documented as such.** Receivers must be idempotent on `event_id`. Promising exactly-once would be a lie.
- **Ordering is per-target and best-effort.** A retried event can arrive after a later one; receivers should order on `occurred_at`, and the docs must say so.

### The existing retry helper does not fit, and neither does the throttle

Worth stating plainly because "reuse `util/retry.py`" is the obvious wrong answer.

`async_retry(max_retries, base_delay, max_delay, exceptions, jitter)` (`util/retry.py:16-22`) is applied **at function-definition time with static parameters** and dispatches on **exception type only**. It has no notion of a retryable HTTP status, no `Retry-After` handling, no total-elapsed cap, and no queue — a failed call is retried in place or raised. Per-target `max_attempts` would mean constructing a decorator dynamically per config object, which is worse than writing the loop.

Separately, **there is no request-rate limiter anywhere in the repo.** `util/throttle.py` is a *byte*-rate calculator for transfers (`BandwidthThrottle.sleep_duration`), not a token bucket, and the only concurrency bound is the per-pair `max_concurrent` semaphore in `SyncExecutor`.

So the delivery loop owns its own backoff, and it is specified rather than inherited:

- Exponential backoff with jitter, `max_attempts` default 5.
- Retry on connection errors, timeouts, `408`, `429`, `5xx`. Honour `Retry-After`.
- Do **not** retry other `4xx`. A `401` or `422` is a configuration error, and retrying it five times per event turns a typo into a flood.
- A total-elapsed cap per event, so an event cannot outlive its own relevance sitting in a retry chain.

`util/retry.py` may be reused verbatim for the *unhealthy-target slow probe*, where static parameters are fine.

### v1 does not survive a restart, and says so

The queue is in memory; events pending when the daemon stops are lost.

A deliberate scope decision. The alternative needs a new table, and the database facts make that a real project rather than a ride-along: `db/migrations/` is an **empty directory** — there is no migration framework, only an inline `if current_version < N:` ladder in `database.py:266-357` — and the `UPDATE schema_version` at `database.py:354` sits **outside** all four `try/except` blocks, so a step that fails still advances the version permanently and never runs again. Adding a table means `SCHEMA_VERSION` 5 → 6, a new `CREATE` in `SCHEMA_SQL`, and a new ladder rung, on a schema whose migration path already has that flaw. There is also no generic retention: `prune_sync_log` is hardcoded to `sync_log`, so a high-churn delivery-attempt table would grow unbounded until someone taught `maintain()` about it.

The v1 docs state the limitation plainly, and the design leaves the seam: the queue interface is where a `webhook_outbox` slots in later, with no change to the resolver, the payload or the auth layer.

For the case where loss matters most — `deletion.blocked` — the block itself is already persisted and survives restart by design ([item 1](ROADMAP#1--delete-fail-safe)), so a receiver that missed the webhook can still see the pending decision in the API. The information is not lost; only the push is.

One further note: the database is a **single unguarded `aiosqlite` connection** with no `asyncio.Lock` (`database.py:126-140`), committing per write. Anything the webhook layer writes shares that one serialised connection with the sync engine, which is another argument for keeping v1's delivery state in memory.

---

## The hook point

`SyncEngine.set_notify_callback` (`engine.py:100`) is the right fan-out point. Three facts about it will otherwise produce a broken implementation:

1. **It is a single slot**, not a list — `self._notify_callback = callback`. Assigning a webhook dispatcher to it *replaces* `ipc_server.notify_all` and silently kills the live UI.
2. **It is wired in three places** — `daemon.py:333`, `:519`, `:586` — all setting `self._ipc_server.notify_all`. A change touching one leaves two on the old behaviour, and the two later ones are on the post-authentication and post-code-exchange paths: the least likely to be exercised in a quick manual test.
3. **The callback is passed by value into `SyncExecutor`** (`engine.py:217` → `executor.py:41`), which stores it for the pair's lifetime — so replacing the slot after `start()` never reaches an already-constructed executor. Conflict notifications are *not* affected: `ConflictResolver` is constructed once at `engine.py:81` without a callback, and `engine.py:346` passes `notify_callback=self._notify_callback` as an argument on every `resolve(...)` call, read at call time. The executor is the stale-reference site; the resolver is not.

Also worth knowing: `engine.py:85` initialises `_notify_callback = None`, so anything emitted before wiring is dropped — which is why `daemon.started` needs care about ordering, since `set_notify_callback` at `daemon.py:333` runs before `engine.start()` at `daemon.py:344` on the normal path but *after* engine construction on the two auth paths.

**Proposal:** a small `EventBus` with `subscribe(callback)`, and `set_notify_callback` retained as a shim registering one subscriber. The three `daemon.py` sites keep working unchanged, executors receive the bus's `emit` (stable for the process lifetime), and the webhook dispatcher is simply a second subscriber.

### The bus must never propagate a subscriber exception — this is the sharpest hazard in the feature

A webhook misconfiguration must not be able to stop a folder syncing. Today it could, and the reason is a two-line asymmetry in the engine.

Three of the emission sites wrap the callback defensively — `delete_blocked` at `engine.py:712-717`, `activity_stopped` at `:915-920` and `activity_resumed` at `:961-966` all use `with contextlib.suppress(Exception)`. **The `sync_complete` and `status_changed` awaits at `engine.py:481-499` do not.** They sit inside `_initial_sync`'s `try`, whose `except Exception` at `:512-518` logs "Initial sync failed" — and what follows them is load-bearing:

```
:503  await self._db.upsert_change_token(ChangeToken(pair_id=pair_id, token=token))
:509  if not self._stop_event.is_set():
:510      await self._start_continuous(ps)
```

So **any** exception raised by a webhook subscriber — an uncaught `asyncio.QueueFull`, a `TypeError` from `json.dumps` on a non-serialisable value, a `KeyError` resolving targets for a pair whose `uid` is missing — skips the change-token upsert *and* skips `_start_continuous`. The pair never enters continuous sync, its change token is never persisted, the activity log records `Sync failed: <webhook exception>`, and the folder silently stops syncing until the daemon restarts. A monitoring feature would have taken out the thing it monitors.

Three requirements, all testable:

1. `EventBus.emit` wraps **every** subscriber in its own `try/except Exception`, logging one rate-limited line and continuing. An exception in one subscriber must not reach the emitter or the other subscribers.
2. Queue overflow is handled *inside* the dispatcher — catch `QueueFull`, evict, count — never raised. Note that drop-oldest is not something `asyncio.Queue.put_nowait` does for you.
3. Wrap `engine.py:481-499` in `contextlib.suppress(Exception)` to match the other three sites, or better, move the notify calls *after* the change-token upsert so no notification can precede persistence. Either way this is a phase-1 change to the engine, not an optional tidy-up.

The "non-blocking" test described below asserts on elapsed time against a hung receiver, which is the case the queue already solves — it would pass while this bug was present. The test that catches it registers a subscriber that raises and asserts the sync pass still completed, the change token was written, and continuous sync started.

### Interaction with emergency stop

Not currently specified, and it needs to be. [Emergency stop](ROADMAP#2--emergency-stop-button) halts activity per account and globally, and its whole premise is that a stopped account "must never look idle". Two defensible readings:

- **Webhooks keep firing.** A stop is itself newsworthy — `activity.stopped` is in the taxonomy — and a monitoring receiver should learn that syncing halted.
- **Webhooks stop with everything else.** "Stop everything immediately" plausibly includes outbound HTTP to third parties.

**Recommendation: lifecycle events (`activity.stopped`, `activity.resumed`, `daemon.*`, `account.*`) continue; data-plane events (`sync.*`, `file.*`, `transfer.progress`, `conflict.*`) are suppressed while stopped**, because there is no data plane to report on. `deletion.blocked` cannot occur while stopped. In-flight deliveries are allowed to finish rather than being aborted — the payload is already gone from the daemon's point of view, and aborting mid-request buys nothing.

Note that `notify_all` currently reaches only IPC stream clients (`ipc/server.py:85`); there is no SSE, websocket or long-poll on the HTTP server and no event surface on MCP. The bus does not change that, but it is the seam that would.

---

## Security

- **TLS verification on by default.** `verify_tls = false` exists for self-signed LAN endpoints, logs a warning at startup naming the target, and shows as a warning in the UI. Per target, never global-only.
- **Redirects are never followed** (`allow_redirects=False`). A `302` to `169.254.169.254` or `127.0.0.1` is the standard pivot for turning an outbound webhook into a request forger, and no legitimate receiver needs a redirect.
- **Webhook writes must require authentication to be enabled — the existing token is not a boundary this feature can lean on.** The tempting claim is that editing a target already requires the token that grants full daemon control. On the deployments this feature most targets, there is no token:

  `HttpConfig.token` defaults to `""` (`config.py:110`); `HttpServer` stores `auth_token or None` (`http/server.py:56`); and `_auth_middleware` returns `await handler(request)` **unconditionally** when the token is `None` (`http/server.py:72-73`), with `is_authorised` returning `True` for the same reason (`http/auth.py:77-78`). A token is minted only on a genuinely fresh install — `if not first_run or self._demo: return` (`daemon.py:83-85`) — because [item 7](ROADMAP#7--authentication-on-by-default-for-new-installs) deliberately left upgrades untouched so nobody is locked out of a bookmarked URL. Meanwhile `--http-host` defaults to `0.0.0.0` (`cli.py:41-42`) and the Docker image's `CMD` enables the HTTP port.

  So on any upgraded container or NAS install with no token set, `/api/*` is anonymously writable from the whole network. That is a known, accepted state for the existing endpoints, because everything they can do moves the *user's own data between the user's own accounts*. A webhook write is different in kind: it exfiltrates the user's live filename stream to an attacker-chosen host and turns the daemon into an HTTP request forger inside the LAN. It is not equivalent to the capabilities already exposed, so it must not inherit their posture.

  **Requirement:** `webhooks.set` and `webhooks.test` refuse with a distinct, documented error when no token is configured, on both the IPC and REST paths. Webhooks require authentication to be switched on. Read-only `webhooks.get`/`resolve` may follow the existing posture.
- **The config is otherwise the trust boundary.** Anyone who can write a webhook URL can already change where the user's data syncs, so with a token configured this is not a new privilege — but it is a new *capability*: the daemon will issue arbitrary HTTP requests to arbitrary hosts on its network. Private and link-local addresses are **allowed by default**, because the primary use case is exactly that (a NAS posting to Home Assistant on the same LAN) and a blocklist would break it.
  - The global level gains `webhooks.allow_private_addresses` (default `true`) as a hardening switch for shared deployments, settable **only** at the global level so a pair cannot re-enable it.
  - The startup log lists every configured target host, so an unexpected one is discoverable.
- **Redaction cannot be call-site-scoped, because the leak is in the exception objects.** Measured against this repo's own `aiohttp` 3.13.5, for a request carrying `?t=QUERYSECRET` and `Authorization: Bearer HEADERSECRET`:

  ```
  str(exc)  → 500, message='Internal Server Error', url='http://…/h?t=QUERYSECRET'
  repr(exc) → ClientResponseError(RequestInfo(url=URL('…?t=QUERYSECRET'), method='POST',
              headers=<CIMultiDictProxy('Authorization': 'Bearer HEADERSECRET', …)>…)
  ```

  `str()` leaks the query string; `repr()` leaks the **`Authorization` header value verbatim**. And three idioms already in this codebase stringify exceptions straight into places a secret must never reach: `detail=f"Sync failed: {exc}"` written into `sync_log` (`engine.py:515-518`), which `get_activity_log` returns and which is exposed as an **MCP read tool**; `log.debug("… (%s) …", exc)` in the repo's only outbound-aiohttp code (`providers/nextcloud/push.py:98-99`); and `JsonRpcResponse.fail(request.id, INTERNAL_ERROR, str(exc))` (`handlers.py:130-132`), which hands the text to every front-end — including the Test button's supposedly "redacted error".

  Worse, `setup_logging` attaches its filters to the `cloud_drive_sync` logger only (`util/logging.py:29-36`), so an unhandled exception in a delivery task is logged by asyncio's default handler on the `asyncio` logger, untruncated and beyond the reach of any call-site helper.

  **Requirements:** never format a client exception — no `%s`, no `%r`, no `{exc}`. Catch at the delivery boundary and build a fixed record from allowlisted fields only: `target_key`, scheme + host + port (never the query), status code, latency, attempt. Add a `logging.Filter` on the **root** logger that scrubs known secret values and any `?…`, so library and asyncio-level records are covered. Install `loop.set_exception_handler` for the webhook tasks. The redaction test asserts on `repr(exc)`, on the activity-log `detail` column, and on the JSON-RPC error string — not just on log text.
- **The payload carries no credentials.** No OAuth tokens, no config values, no `http.token`. `scope` is identifiers and paths only.
- **Response bodies are discarded.** Not logged, not surfaced, not stored — an endpoint returning 500 with an HTML page must not get that page into the daemon's log or a screenshot.

---

## Front-end surfaces: it is nine files, not one

The [emergency-stop requirement](ROADMAP#2--emergency-stop-button) set the precedent that a control must be reachable from every front-end. Webhooks follow it — but the real cost of "add a setting" in this repo is worth stating before anyone estimates this.

**There are six surfaces, not five.** Between `ipc.ts` and the daemon sits a **Tauri Rust bridge**: every `invoke()` hits a `#[tauri::command]` in `ui/src-tauri/src/commands.rs`, which must *also* be listed in the `generate_handler![…]` array at `ui/src-tauri/src/main.rs:37-76`. It is where the existing surfaces have already rotted, and both defects bound this design:

- `set_pair_conflict_strategy`, `repair` and `exchange_auth_code` are invoked by `ipc.ts` but have **no Rust command at all**, so those UI controls work in web mode and are dead in the desktop app. Skipping the Rust step fails silently in exactly one build. (`list_local_dirs` and `mkdir_local` are a different case and not a defect: the desktop build uses the native Tauri dialog plugin instead, and `FolderPicker.tsx:58-61` guards their absence with a visible message.)
- The Rust structs use plain `#[derive(Deserialize)]` with no `deny_unknown_fields`, so **unknown JSON fields are dropped**. `commands.rs` `SyncPair` has no `conflict_strategy` even though `handlers.py:597` returns it and `types.ts:105` declares it — which is why the per-pair conflict dropdown can never show a stored value on the desktop.

**A new per-pair webhook field that is not added to the Rust struct will simply be invisible in the desktop app, with no error anywhere.**

The full change set for one setting: `config.py` (load **and** save), `ipc/handlers.py` (handler + `_handlers` dict entry), `http/server.py` (route + wrapper), `mcp/catalog.py` (a tool or a `NEVER_EXPOSED` entry), `commands.rs`, `main.rs`, `ipc.ts`, `ipc-http.ts`, `ipc-demo.ts` — plus `types.ts`, `Settings.tsx`, `docs/Configuration.md`, `docs/API.md`, `docs/CLI.md`, a Bruno `.bru`, and `make build-webui`. Missing `ipc-demo.ts` breaks demo mode and therefore the screenshots; missing `make build-webui` ships a web UI that cannot reach the daemon.

### Per surface

- **CLI** — a new `@cli.group()` named `webhook`, with `list|show|add|update|remove|test` and `--scope global | account:<provider>:<email> | pair:<uid>`. Note this has **no precedent to copy**: all 18 of the CLI's RPC calls are reads or state actions, and there is no `set_*` method reachable from the command line at all — notifications, bandwidth, proxy, conflict strategy, sync rules and the delete limits are all UI-only today. The CLI settings pattern has to be invented here. `list` without a scope prints the **resolved** view per pair, because "which webhooks will actually fire for this folder" is the real question, and `--explain` prints the [worked example](#worked-example) table for one pair, which is the only realistic way to debug a three-level merge. One caveat on enforcement, so nobody relies on a gate that does not exist: `test_feature_cli_contracts.py:288` (`test_every_command_documented_in_the_quick_reference_exists`) is **one-directional** — it asserts `missing = {c for c in documented if c not in available}`, so it fails only when `CLI.md` names a command that no longer exists. Adding a `webhook` group and never touching `CLI.md` passes green. There is no code→docs gate in CI at all; the only real docs gate is the wiki-sync `PAGES`/`SIDEBAR` check.
- **IPC** — `webhooks.get`, `webhooks.set`, `webhooks.resolve`, `webhooks.test`, `webhooks.status`, added to the plain dict dispatch in `ipc/handlers.py` (44 entries today at `handlers.py:37-81`, no decorator registry).
- **REST** — `GET/PUT /api/webhooks` (global), `GET/PUT /api/pairs/{uid}/webhooks`, `GET /api/pairs/{uid}/webhooks/resolved`, `POST /api/webhooks/test`. For the account scope, **do not put the identifier in a path segment**: `provider:email` contains `@`, `.` and `:`, which means percent-encoding every client has to get right and a route pattern that is easy to mis-match. Use `GET/PUT /api/accounts/webhooks?provider=gdrive&email=work@example.com` instead, so the identifier travels as query parameters. Routes are one-line wrappers over the same IPC handler, behind the existing token check plus the auth-required guard above.
- **MCP** — and here the repo has already decided this, in a way that overrides the obvious answer. `NEVER_EXPOSED` (`mcp/catalog.py:32-48`) excludes `set_proxy` because it "would let an agent route all traffic through a host it chose" — which is precisely what `webhooks.set` would do to the event stream — and, more pointedly, excludes **`get_proxy`**, a *read* method, solely because "proxy URLs can embed credentials (http://user:pass@host)". A webhook URL is the same object, and this document notes elsewhere that a token in a query string is a common receiver design. So "read-only is fine" does not survive the precedent. The classification:
  - `webhooks.set`, `webhooks.test` → `NEVER_EXPOSED`, same reasoning as `set_proxy`.
  - `webhooks.get` → `NEVER_EXPOSED`. It returns stored config verbatim, including the free-form `headers` table, which the masking rules do not cover because the key names are arbitrary.
  - `resolve_webhooks` → exposed, but as a **stripped projection**: `target_key`, display name, scheme + host + port (never the path or query), the effective event list, the auth *mode name* only, and health counters. That is enough for an assistant to explain why a delivery is failing — the stated goal — and carries no credential.

  `mcp/catalog.py` does not force a new handler to be classified, so the exclusions must be added explicitly rather than achieved by absence.
- **UI** — `Settings.tsx` is one 1002-line component with seven `settings-section` blocks; a Webhooks section joins them. The closest model for the per-pair editor is the existing **Advanced Rules** panel (`Settings.tsx:518+`): a button toggles a `Set<string>` of open pair ids, opening it lazily fetches that pair's config, and a Save button posts the whole object back. Per-account config anchors on the `.sync-group-settings` block (`Settings.tsx:725-758`), which today holds exactly one control — note `PairGroup` (`Settings.tsx:357`) is a local *type alias* inside the `Settings` function, not a component, so there is no separate file to edit. Inherited targets render greyed out with their origin labelled ("from global", "from account work@example.com") plus a per-target enable toggle and an Override button — showing *where a value came from* is what makes a three-level hierarchy usable, and hiding it is what makes users set everything per pair instead.
- A **Test** button at every level, sending `webhook.test` and showing status code, latency and, on failure, the redacted error.

---

## Testing

`aiohttp` is a hard dependency, so a real local receiver costs no new test dependency and no mocking of the HTTP layer. **Do not reach for `aiohttp.test_utils`, though** — this repo has already considered and rejected it. `test_feature_http_auth.py:101-103` documents why: it would pull in `pytest-aiohttp`, "a new dependency that also brings its own asyncio handling and could conflict with this project's `asyncio_mode = auto`". That file drives a real socket against a running server by hand, and it is the precedent to copy.

For contrast, `test_feature_nextcloud_push.py` is *not* a local-server precedent — it is the opposite approach, monkeypatching `aiohttp.ClientSession` with a `FakeSession` (`:467`). Both patterns have a place here: the fake session for auth-header and retry-policy assertions where only the outgoing request matters, and a real socket for the end-to-end delivery, TLS and redirect tests where the point is that a real client did a real thing.

Conventions to follow: `test_feature_webhooks_*.py` naming (features are `test_feature_*`, regressions are `test_bug_*`), `asyncio_mode = "auto"`, and the near-empty `conftest.py` — its only fixture is `short_tmp`, so webhook tests bring their own.

**CI runs the suite on ubuntu, windows and macos** (`.github/workflows/ci.yml`), which has two consequences: tests must not assume a POSIX-only detail, and **PyJWT is absent on all three runners** because CI installs `[dev]` only. That is the constraint behind the no-JWT-library decision above, and a test that would only pass locally is worse than no test.

- **Resolution** — table-driven over the [worked example](#worked-example), asserting the effective list for both pairs. Plus the awkward cases: disable-at-account/re-enable-at-pair, a pair-only target, `events_remove` beating `events_add`, and a target inheriting no `url` being dropped with a log entry.
- **Config round-trip** — `load` → `save` → `load` at all three levels, asserting every tri-state survives. Specifically: `enabled = false`, `verify_tls = false`, `include_paths = false` and `max_files_per_event = 0` must all still be present and false/zero after the round-trip. This is the test for the falsy-sentinel trap, and it is the one most likely to catch a real bug.
- **Unknown-key erasure** — write a `[webhooks]` block, change an unrelated setting through a handler, assert the webhook config is still there.
- **Auth** — one test per mode against a capturing server, asserting exact header bytes. For `jwt`, verify the minted token validates against an independent check (`hmac` for HS256, `cryptography` for RS256) and assert `exp`, `jti == event_id`, and a fresh token per attempt.
- **Signature** — HMAC over the exact bytes sent; altering one body byte invalidates it.
- **Payload** — golden-file the envelope so a field rename is a visible diff rather than a silent contract break. Assert `pair_N` appears **nowhere** in any emitted payload: this is the regression test for the whole identity section.
- **Identity** — a legacy pair with no `uid` gets the *same* derived uid across two independent loads; `Config.load` still does not create a file (the existing pinned test must stay green).
- **Truncation** — 10 000 files in, `max_files_per_event = 100`; assert list lengths, `files_truncated: true`, and that the counts are still 10 000.
- **Delivery** — retry on 500 and on timeout, no retry on 401/422, `Retry-After` honoured, queue overflow drops oldest, priority events survive overflow, circuit breaker opens and recovers.
- **Redaction** — a token in a header, in a URL query string and in an error message; assert none reaches the log or the activity-log `detail`.
- **Masking round-trip** — GET then PUT with no edits leaves the secret byte-identical.
- **Subscriber isolation** — register a subscriber that raises (`QueueFull`, then `TypeError`), then assert the sync pass still completed, the change token was written, and `_start_continuous` ran. This is the test that catches the worst failure mode in the feature, and the elapsed-time test below would pass while that bug was present.
- **Non-blocking, measured as loop stall rather than wall clock** — emit a `sync.completed` carrying 50 000 paths against three resolved targets and assert that a concurrent 10 ms heartbeat task's maximum jitter stays under a bound. Asserting elapsed time against a *hung receiver* only tests the queue, which was never the risk; the risk is serialisation on the emit side, and only a stall measurement sees it.
- **Delivery-key independence** — two pairs defining the same target `name` with different URLs get two independent queues and circuit breakers; killing one endpoint must not open the other's breaker.
- **Delta scoping** — an account-level `events_add` must not survive a pair-level `events` replace; a global `events_remove` must not empty a pair's explicit `events`.
- **Auth completeness** — a `mode`-less `auth` table, and a `jwt` target whose `key_file` was lost to atomic replace, are both rejected at load time with the level named, not at delivery time.
- **Auth-required guard** — with no `http.token` configured, `webhooks.set` and `webhooks.test` are refused on both the IPC and REST paths.

One process note, and it applies to this suite more than most: **each test must be seen to fail before it is trusted.** A webhook suite that passes because no request was ever attempted is worse than no suite. Assert the clean baseline green, then break the thing deliberately and watch the assertion catch it.

`tests/api/` gains Bruno requests for each new REST endpoint, matching the numeric-pair-id conventions already there.

---

## Phasing

Each phase is independently shippable and independently useful.

| Phase | Content | Rationale |
|---|---|---|
| **0** | Pair `uid` (persisted in the TOML pair table, derived deterministically for legacy pairs, never written from `load()`); the `self._loop` fix in `_log_auth_event`; `contextlib.suppress` on the `sync_complete`/`status_changed` emissions at `engine.py:481-499` | Prerequisites, and all three are worth doing regardless of webhooks. Without the uid the payload cannot carry an honest identifier; without the loop fix `account.auth_failed` inherits a silent swallow; without the suppress, any subscriber exception can stop a pair syncing. |
| **1** | `EventBus` with per-subscriber exception isolation; **new emissions** for `sync.completed` and `sync.failed` in both continuous loops (they emit nothing today); global + pair levels; `none`/`basic`/`bearer`/`custom`; `conflict.detected` and `deletion.blocked` (with the full breach); queue keyed on `target_key`, retries, truncation, root-logger redaction; the auth-required guard on writes; CLI + REST | The smallest thing that solves the actual problem — telling a headless deployment that something needs attention. Note this is larger than a dispatcher: the two flagship events do not exist continuously yet. |
| **2** | Account level and the third tier of the merge; `--explain`; the UI with inherited-value provenance; the Tauri struct fields | The third level, once the mechanism is proven. Note the merge *rules* are not deferred with it: phase 1 already ships two levels, so `define`, delta scoping, per-mode `auth` validation and `target_key` all land in phase 1. |
| **3** | Minted `jwt`; HMAC signature; `conflict.resolved` and the lifecycle events; the opt-in high-volume events | The mechanisms with the most implementation risk, added once the delivery path is trusted. |
| **4** | Durable `webhook_outbox` table, redelivery on startup, retention in `maintain()` | Deferred deliberately: needs `SCHEMA_VERSION` 6, a ladder rung on a migration path whose version bump is not guarded by success, and its own pruning. |

### Documentation

`docs/` is the source of truth and auto-syncs to the wiki, so the docs work is part of the feature, not after it:

- **`docs/Configuration.md`** — the maintained config reference. A new `### `[webhooks]`` section with the 4-column `| Key | Type | Default | Description |` table, keys added to the `[[sync.pairs]]` and `[[accounts]]` tables, and commented blocks in the annotated example. Follow the established idiom for inheritance exactly: the Default column reads `(inherits global)`, and the example spells out the opt-out in comments the way `conflict_strategy` does at `Configuration.md:96-98`.
- **`docs/DAEMON.md`** — a new `##` section in the feature-behaviour band (lines 712-953), using `## Delete Protection` as the structural template, plus a **Webhooks** group in the REST API table at `### REST API reference`. Note `DAEMON.md` duplicates `Configuration.md` and has already drifted; treat `Configuration.md` as authoritative and do not copy `DAEMON.md`'s content as a template for correctness.
- **`docs/API.md`**, **`docs/CLI.md`**, **`docs/UI.md`**, **`docs/ARCHITECTURE.md`** (the event bus), **`README.md`** (feature mention).
- **Already done** as part of filing this proposal: the `ROADMAP.md` queue entry (item 10), and the `wiki-sync.yml` registration — `PAGES` gained `[Proposal-Event-Webhooks]` and it is listed under `SIDEBAR_SECTIONS`. Worth knowing why both were needed together: forgetting `PAGES` emits a warning only and the page goes silently missing from the wiki, while adding a sidebar entry without a `PAGES` key **fails the job**. That check is the one real docs gate in CI.

Correct-but-unfindable docs read as missing, which is why the `[webhooks]` block belongs in the annotated `config.toml` example and not only in a section of its own.

---

## Open questions

1. **Is `deletion.blocked` alone worth phase 1?** It is arguably the whole feature for a headless user. A much smaller version — one global URL, `bearer` or `none`, that one event — could ship in a fraction of the time. The three-level hierarchy is what was asked for; worth confirming it is wanted *now* rather than as phase 2.
2. **Batching.** One POST per event is simple and orders well, but a library scan producing 5 000 `file.uploaded` events makes 5 000 POSTs. Should a target opt into `batch = { max_events = 50, max_wait_seconds = 2 }`? It changes the payload shape (an envelope wrapping an array), which is why it is a question rather than a decision.
3. **Should `enabled = false` at the global level stop pair-level targets too?** As specified a pair can opt back in. The alternative — global off means off, period — is a better emergency switch and a worse inheritance model. There may be a case for both: an inheritable `enabled` plus a separate non-overridable global kill switch, matching how [emergency stop](ROADMAP#2--emergency-stop-button) already distinguishes account scope from global scope.
4. **Retention of delivery attempts.** Logging every attempt is honest and noisy; logging only failures is quiet and hides a target that succeeds slowly. Suggested: log failures and health transitions, keep per-target counters in `status` for the rest — which also avoids adding a high-churn table that nothing prunes.
5. **Does the account level pull its weight?** It is the level with the least obvious use case — most people have one account — and today the account tier holds exactly one setting in the entire product (`max_concurrent_transfers`). The counter-argument is that it is where a *provider*-shaped policy naturally lives, and it is cheap once named-target merging exists for pairs.
6. **mTLS** as a sixth mechanism (client certificate rather than a header). Not requested, genuinely wanted by some self-hosted setups, and mostly an `aiohttp` `ssl.SSLContext` plus two config fields.
7. **`defaults` and inherited targets.** Step 4 applies each level's `defaults` to fields the target never set — but it is not stated whether a *pair-level* `defaults` block should reach a target inherited from global. Arguments both ways: "these are my pair's timeouts" versus "I did not define that target". Currently unspecified.
8. **Priority-lane overflow.** The lane is "never dropped" with "small fixed capacity" — those cannot both hold. What happens when it fills is undefined; options are block the emitter (unacceptable), grow unbounded (unacceptable), or drop with a loud log and a status flag (probably right, but it makes "never dropped" false).
9. **Circuit breaker versus the queue.** While a target is `unhealthy` and slow-probing, do its events keep accumulating to the 1000-slot cap and then drop-oldest, or is the queue drained on breaker-open? Draining loses events; accumulating means a five-minute probe interval guarantees overflow for any busy target.
10. **Drop-oldest suits monitoring, not audit.** The `compliance` target in the worked example is an audit sink, where the oldest event is the one you least want to lose. A per-target `overflow = "drop_oldest" | "drop_newest"` may be warranted.
11. **Shutdown semantics.** Whether shutdown waits briefly for in-flight deliveries to drain, and for how long, versus dropping them immediately. Interacts with the admitted no-persistence limitation.
12. **`allow_private_addresses` as a global-only field.** Two reviewers noted independently that "settable only at the global level" is asserted but has no mechanism in the merge model, which has no concept of a non-overridable field. Either the model gains one, or the field lives outside `webhooks` entirely.
13. **`jti` and the replay window.** Setting `jti = event_id` is deliberate, but a retry reuses the `event_id` with a *fresh* token — so a strict receiver doing `jti` replay rejection will reject legitimate retries. The interaction between at-least-once delivery and `jti`-based replay defence needs stating.

### Raised in review and deliberately not folded in

Recorded so the next reviewer does not re-find them: the `include_paths` / `path_sha256` scheme leaks little for a small known corpus (a dictionary attack on filenames is cheap); the HMAC scheme needs an explicit canonicalisation statement for the receiver; `verify_tls = false` warning-at-startup is weaker than warning-per-delivery; and the per-file event volume interacts with `transfer.progress` rate limiting in ways only load testing will settle. None change the design's shape; all belong in the implementation's review.

---

## Scope note

Some of what this document records is broader than webhooks and would be worth acting on regardless: the unguarded `sync_complete` emission, the swallowed auth log rows, the continuous loops that report neither success nor failure, the one-directional CLI-contracts test, and the Tauri bridge's dropped fields and missing commands. They are here because they sit directly on this feature's path — but they are pre-existing, separable, and should not be used to price the feature.

---

## One naming collision to avoid

**Provider-native webhooks are a different feature from this one.** Google Drive push notifications and Nextcloud `notify_push` are *inbound* — the cloud telling us something changed. This proposal is *outbound*. The names collide, and the docs must keep them apart explicitly, because a user reading "webhooks" in a sync tool could reasonably expect either.
