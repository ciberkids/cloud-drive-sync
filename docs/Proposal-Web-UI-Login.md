# Proposal: Web UI User Accounts

A design proposal for a sign-in page on the **web UI** — one named account with a password, a session cookie, sign out, change password — replacing the shared-token paste box that stands in for a login screen today.

Status: **shipped**. The five open questions this document was written to settle were answered in review — recorded under [Decisions settled in review](#decisions-settled-in-review), including the two that went against the original recommendation and why that is fine. What implementation changed about the design is recorded under [What building it changed](#what-building-it-changed); everything else landed as described.

**Recommendation in one line:** the access token stays exactly what it is — a *machine* credential for `/api/*` — and a **single** named account is added beside it as the *human* credential for the browser, stored in the database, verified with stdlib scrypt behind a concurrency cap, carried by an in-memory session behind an opaque cookie, and bootstrapped by presenting the token that first run already prints.

---

## Contents

- [The ask](#the-ask)
- [The scope boundary comes free](#the-scope-boundary-comes-free)
- [The constraint that shapes everything: the token cannot break](#the-constraint-that-shapes-everything-the-token-cannot-break)
- [One account, not a user list](#one-account-not-a-user-list)
- [Bootstrap: the printed token creates the account](#bootstrap-the-printed-token-creates-the-account)
- [Where the account lives](#where-the-account-lives)
- [Password hashing](#password-hashing)
- [Sessions](#sessions)
- [Cookie flags](#cookie-flags)
- [CSRF, and what happens to the CORS argument](#csrf-and-what-happens-to-the-cors-argument)
- [Rate limiting without a lockout](#rate-limiting-without-a-lockout)
- [The HTTP surface](#the-http-surface)
- [The SPA: a gate above the router, not a route inside it](#the-spa-a-gate-above-the-router-not-a-route-inside-it)
- [The CLI, and the break-glass path](#the-cli-and-the-break-glass-path)
- [Pinned behaviour that changes](#pinned-behaviour-that-changes)
- [Out of scope, stated plainly](#out-of-scope-stated-plainly)
- [Phasing](#phasing)
- [What building it changed](#what-building-it-changed)
- [Release](#release)
- [Documentation to update](#documentation-to-update)
- [Decisions settled in review](#decisions-settled-in-review)

---

## The ask

A login page for the web UI, like any other self-hosted project has: a named account, a password, sign in, sign out, change password. Web UI only.

What exists today is not that. With `[http] token` set, the daemon serves a hand-written HTML form that asks for **the token itself** and stores it verbatim in a cookie (`http/server.py` `_LOGIN_PAGE`, `_login`). That is one shared secret with no identity behind it, which the documentation already admits: *"The token is a shared secret, not a user account. No roles, no per-user auditing."* ([Authentication](Daemon#authentication)). The ask is to make that sentence false in the part that matters.

---

## The scope boundary comes free

"Web UI only" needs no flag, no build switch, and no runtime check, because the desktop UI does not use the HTTP port at all. The Tauri front-end calls `invoke()`, the Rust side connects to a **Unix domain socket** (`ui/src-tauri/src/ipc_bridge.rs:78`), and the daemon's IPC server answers there. Nothing added to the aiohttp front-end is reachable from the desktop app.

Two consequences worth recording rather than rediscovering:

1. **The IPC socket stays unauthenticated.** Its boundary is filesystem permissions, and that is the pre-existing trust model for every local caller including the CLI. This proposal does not change it. Anyone who can open that socket already has the daemon.
2. **Therefore the CLI is the recovery path.** A locked-out user is fixed from a shell on the box, not from the browser. That is deliberate, and it is what lets us refuse every self-service reset flow (see [The CLI, and the break-glass path](#the-cli-and-the-break-glass-path)).

---

## The constraint that shapes everything: the token cannot break

The access token is not an internal detail. It is deployed, documented, printed, and depended on:

| Where it lives | Consequence |
|---|---|
| `CDS_HTTP_TOKEN` in systemd units and compose files ([Installation](Installation), [Docker](Docker)) | Changing its meaning changes running deployments on upgrade |
| `--http-token`, which takes precedence over the config file (`daemon.py:_resolve_http_token`) | Precedence order is pinned by tests and documented in a table |
| Generated on first run and **printed to stdout, never to the log** (`daemon.py`) | It is the one credential a headless operator is guaranteed to have seen |
| `Authorization: Bearer` in every `curl` example in the docs | Scripts exist that we cannot see |
| `tests/api/environments/local.bru` — which sets **no token at all** | The Bruno collection assumes an unauthenticated daemon |
| The MCP front-end's own separate token (`mcp/server.py:_with_auth`) | Shares `http/auth.py` primitives; must keep working untouched |
| `webhooks/api.py:159` `require_http_token` | Treats "a token is set" as a synonym for "authentication is configured" |

So the design is layered, not replaced:

> **The token is the machine credential. The account is the human credential. Both are accepted on `/api/*`; neither replaces the other.**

| Configured | `/api/*` accepts | Browser gets |
|---|---|---|
| No token, no account | Everything (unchanged), plus the startup exposure warning | The app, no sign-in |
| Token only | `Authorization: Bearer <token>`, or the token cookie | A token form — same credential as today, rendered by the SPA |
| An account (with or without a token) | Bearer token **or** a session cookie | Username and password |

Everything that follows falls out of that one line. Scripts, Bruno, MCP and `curl` keep working with no change. The browser never holds the token after bootstrap. And `require_http_token` becomes `require_authentication` — "a token is set **or** an account exists" — because otherwise creating an account would still leave webhook configuration refused with a message telling you to set a token you deliberately replaced.

---

## One account, not a user list

**Settled in review: single user. No multi-user, no roles.** That is not a deferral, it is the design — and it removes a surprising amount of surface:

- No `GET/POST/DELETE /api/auth/users`, no user list in the UI, no "refuse to delete the last user" edge case, no per-user session bookkeeping.
- The database table holds **at most one row**, enforced in the schema (`CHECK (id = 1)`) rather than by convention, so a second account is impossible rather than merely unsupported.
- Sign-out, change-password and revoke-everything all operate on "the account", which is the only one there is.

The username still exists and is still required at sign-in. A password-only form would be unusual enough to look broken, it gives the operator something to recognise, and it costs one column.

What this **is not** is a claim that multi-user is wrong forever. It is a claim that a single-owner sync daemon with no roles gains nothing from a user table it would only ever put one row in — and that "add a second row" is a smaller change later than "remove five endpoints and a management screen" would be.

---

## Bootstrap: the printed token creates the account

The account is created by presenting **the access token**. When authentication is required and no account exists yet, `/login` renders a *create your account* form asking for the token, a username, and a password twice.

This costs nothing to build because first run already does the hard part: it generates a token, persists it under `[http] token`, and prints it to stdout in a box that tells the operator to open the UI — deliberately not to the rotating log file, because a secret in a log outlives its usefulness (`daemon.py`, and [Authentication](Daemon#new-installs-get-a-token-automatically)).

**Why not a claim window** (the Portainer model — the first browser to arrive within N minutes becomes admin). It is new machinery that either locks out a slow operator or hands the instance to whoever reaches the port first. We already have a credential that proves you are the operator; a timer is strictly worse than proof.

**Why not CLI-only creation.** A browser user on a NAS should not have to open a shell to get a login. Both paths exist; the CLI one is documented as the headless and recovery route.

**The case that needs stating: an existing install with no token.** Those are already open, so an unauthenticated *setup* page would be a claim page — anyone who can reach the port gets the account, and the daemon would be handing out the credential it was supposed to start requiring. So: **creating the account through the browser requires a token.** With no token set, `user set` over the local socket is the only path, which makes enabling authentication on an old install a deliberate local act. That preserves the property [item 7](ROADMAP#7--authentication-on-by-default-for-new-installs) was careful about — upgrades are never locked out and never silently changed.

---

## Where the account lives

**Settled in review: a database table, not the config file.** `SCHEMA_VERSION` 5 → 6:

```sql
CREATE TABLE IF NOT EXISTS web_user (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    username            TEXT NOT NULL,
    password            TEXT NOT NULL,   -- scrypt$n=…$salt$hash
    created_at          TEXT NOT NULL,
    password_changed_at TEXT NOT NULL
);
```

The single-row `CHECK` is the schema carrying the [one-account decision](#one-account-not-a-user-list), so nothing downstream has to defend it.

This is the one place the review overrode the original recommendation (which was `config.toml`), and the objection that recommendation rested on has to be answered rather than dropped:

**The objection.** The database lives in the data directory and the config in the config directory — separate volumes in a container. A config-only restore, or `rm` of the database (which is a *supported* repair for a corrupt sync state), takes the account with it.

**Why it does not bite here.** Because the token is the bootstrap credential and it lives in `config.toml`. Lose the database and the daemon is back to "token set, no account" — which is a defined mode, not a broken one: the setup page renders, you paste the token that is still in your config file, and you re-create the account. The recovery path is the one that already exists, so the failure mode is *a minute of inconvenience*, not lockout. Under a multi-user design the same event would have destroyed a list nobody could reconstruct; under one account it destroys one row you can retype.

**What it buys.** Credentials stop being mixed into a hand-edited TOML file; a password change is a row update instead of a whole-config rewrite (and `Config.save()` erasing anything it does not enumerate has bitten this codebase before); and `created_at` / `password_changed_at` live where mutable per-record state belongs.

Two traps, both previously stepped on here:

1. **`--demo` shares the real config *and the real database*** (`daemon.py:_setup_demo`, and the comment there recording that a demo pair once overwrote a real pair's sync state). A demo run must never create or overwrite the account row — the same rule `_resolve_http_token` already applies to the token.
2. **`SCHEMA_VERSION` 6 is also claimed** by the webhook durable outbox in [item 10](ROADMAP#10--event-webhooks). Whichever lands first takes 6. And `db/database.py`'s own history includes a migration whose version bump was not guarded by success — it advanced past a column that was never added — so this migration bumps the version only after the `CREATE TABLE` has actually committed.

---

## Password hashing

`hashlib.scrypt`, with the parameters recorded inside the encoded value:

```
scrypt$n=16384,r=8,p=1$<salt-b64>$<hash-b64>
```

Self-describing, so the cost can be raised later and a hash with older parameters is transparently re-hashed on the next successful sign-in.

**Why not a new dependency.** `argon2-cffi` is the better primitive on paper and a C extension in practice, on a project that ships DEB, RPM, AppImage, Flatpak, DMG, MSI and a Docker image. scrypt is memory-hard and in the standard library.

**Why not PBKDF2, given `auth/credentials.py` already uses it.** That use is key derivation from a machine id, where the input is not guessable. A human password is, and PBKDF2 is cheap to attack in parallel on a GPU at any iteration count a NAS can afford. Memory hardness is the whole point here.

**Why `n=2**14` (≈16 MB) and not `2**15`.** `POST /api/auth/login` is unauthenticated, so every attempt is an **attacker-controlled allocation** on hardware that might be a 512 MB NAS. The KDF cost is only safe in combination with a cap, so both are part of the same decision:

- a semaphore of **4** concurrent verifications;
- a **bounded queue** beyond it — a flood gets `503`, not an out-of-memory kill;
- and the rate limiter below, which is what makes 16 MB an acceptable per-attempt price.

Getting this wrong turns a login form into a memory amplification DoS, which would be a worse bug than the one this feature fixes.

**Measured rather than assumed**, on a desktop CPU: ~34 ms and ~16 MB per operation, and `hashlib.scrypt` **releases the GIL** — four sequential verifications took 135 ms, four threaded took 43 ms. That second fact is why verification runs on a worker thread: 34 ms of in-loop CPU per attempt would stall every other request, and an unauthenticated endpoint is exactly where an attacker would aim that.

**Password rules**, following NIST 800-63B rather than folklore: minimum 10 characters, no maximum below 1024, **no** composition rules, no forced rotation. Rejected: a password equal to the username, or equal to the access token.

---

## Sessions

**Settled in review: in memory.** A dict from session-id digest to `(username, issued_at, last_seen)`, pruned lazily on lookup. No table, no migration, nothing on disk.

- A 32-byte URL-safe random id goes in the cookie; the process stores only its SHA-256 digest, so a heap dump is not a set of usable credentials.
- **Absolute expiry** 30 days, **idle expiry** 7 days, id **rotated on every sign-in**.
- Sign out drops the entry. Changing the password drops every entry.
- The cookie name is new (`cds_session`), so the existing `cds_token` cookie keeps its current meaning and no upgrade sees a cookie it will misread.

**The consequence, stated rather than discovered: restarting the daemon signs you out.** An upgrade, a `systemctl restart`, a container recreation — all of them mean signing in again. That is the accepted trade for not adding a second table, and it is honest about what a self-hosted daemon actually does: it restarts rarely, and a login page is not a hardship when it does.

The cookie still carries a 30-day `Max-Age`, which is deliberately *longer* than any session it points at. The server is the authority; an id it does not recognise is simply rejected and the gate renders the login view. A shorter cookie would buy nothing and would sign people out while their session was still valid.

**Why opaque and not a JWT.** Revocation. A stateless token cannot be withdrawn without a blocklist, which is the very state a JWT was supposed to avoid, plus key management the daemon does not otherwise need. With sessions in memory there is nothing to distribute — one process reads what it wrote.

---

## Cookie flags

**Settled in review: follow best practice**, which here means every flag that can be set unconditionally is, and the one that cannot is detected rather than assumed.

```
Set-Cookie: cds_session=<id>; HttpOnly; SameSite=Lax; Path=/; Max-Age=2592000[; Secure]
```

- **`HttpOnly`** always. No JavaScript reads it, so an XSS in the bundle cannot exfiltrate the session.
- **`SameSite=Lax`** rather than the current `Strict`. `Lax` still refuses to send the cookie on cross-site `POST`/`PUT`/`DELETE`, which is the CSRF case. What `Strict` additionally breaks is following a link *to* the UI from anywhere else — a bookmark opened from a chat app or an email arrives looking signed out, and the user re-enters a password for no security gain. *(Aside, not a design driver: `/api/accounts/oauth-callback` is a cross-site top-level GET from the provider, so `Strict` would not send the cookie there either. The web UI uses the paste-the-code path, so this is latent today; `Lax` happens to fix it.)*
- **`Secure` when — and only when — the connection is actually HTTPS.** The daemon serves plain HTTP, so setting it unconditionally would break every `http://nas:8080` deployment: the browser accepts the cookie and never sends it back, which presents as "login succeeds, then immediately asks again". So it is set when the request arrived over TLS, or when a **trusted** proxy says it did — `X-Forwarded-Proto: https`, honoured only if `[http] trust_proxy` is on. Trusting that header by default would let anyone who can reach the port assert it, so the operator has to say the proxy is there. Best practice is not "always set Secure", it is "never send a session cookie in the clear when you know the transport is TLS".
- **`Path=/`**, matching the single-origin SPA.

Considered and rejected: the **`__Host-` prefix**. Its guarantees are about domain scoping this deployment does not have, and it *requires* `Secure` — so the cookie name would have to differ between an HTTPS and a plain-HTTP deployment, which is a branch in every place the name appears in exchange for nothing here.

---

## CSRF, and what happens to the CORS argument

Cookie authentication introduces a class of attack the token never had: a request the browser sends *for* an attacker. Three layers, cheapest first:

1. **`SameSite=Lax`** — no cookie on any cross-site mutating request.
2. **`/api/*` mutations require `Content-Type: application/json`** and reject `application/x-www-form-urlencoded` and `multipart/form-data`. An HTML form cannot send JSON, which removes the no-JavaScript vector entirely. (Note the new login endpoint posts JSON — the current one is a form POST, and that goes away.)
3. **Origin check when a cookie is the credential**: on a mutating request authenticated by session, require `Origin`/`Referer` to match the request host. Bearer-token callers send neither and are unaffected, so `curl` and Bruno stay untouched.

And [Daemon](Daemon#remaining-specifics)'s CORS paragraph becomes wrong. It currently argues the `Access-Control-Allow-Origin: *` wildcard is acceptable *because* a cross-origin page cannot read the token. The conclusion survives for a better reason — we never send `Access-Control-Allow-Credentials`, and browsers refuse to combine credentials with a wildcard origin, so a cross-origin page still cannot use the session cookie — but the argument has to be rewritten rather than left standing.

---

## Rate limiting without a lockout

A password is guessable in a way a 32-byte token is not, so this is the part that has to exist rather than being nice to have.

Sliding window per username **and** per source address: after 5 failures, an exponential delay on the response (1s, 2s, 4s … capped at 30s), with concurrent attempts serialised so parallelism cannot buy free guesses.

**No hard lockout.** With a single account, a lockout is a total outage — an attacker who knows the one username could take the UI down at will. And a NAS behind CGNAT shares its address with strangers, so an address ban punishes the wrong people. Failed attempts log the username and source address, never the password; successful sign-ins log too.

---

## The HTTP surface

```
GET    /api/auth/session     → {"auth":"none"|"token"|"user",
                                "setup_available":bool,
                                "authenticated":bool,
                                "username":str|null}
POST   /api/auth/token       {token}                       → 204 + Set-Cookie
POST   /api/auth/setup       {token, username, password}   → 204 + Set-Cookie
POST   /api/auth/login       {username, password}          → 204 + Set-Cookie
POST   /api/auth/logout                                    → 204, cookie cleared
POST   /api/auth/password    {current, new}                → 204, sessions dropped
```

Six endpoints, and no user management — that is the [single-account decision](#one-account-not-a-user-list) paying for itself. `/api/auth/token` is the one this document originally missed; see [What building it changed](#what-building-it-changed).

`GET /api/auth/session` is unauthenticated and deliberately says nothing about the daemon's data — only what the sign-in screen needs in order to render. It does reveal whether an account exists, which is unavoidable and is what every login page in the world reveals.

`POST /api/auth/login` answers a single `401 {"error":"invalid_credentials"}` for both a wrong username and a wrong password. No enumeration.

The middleware allow-list (`server.py:_auth_middleware`) grows to: `/api/auth/session`, `/api/auth/setup`, `/api/auth/login`, `/assets/*`, and the SPA shell. Auth continues to run before CORS, so an unauthorised request never reaches a handler.

---

## The SPA: a gate above the router, not a route inside it

`NavBar` renders **outside** `<Routes>` and calls `useStatus()` at mount (`ui/src/App.tsx`). A `/login` route inside the router would therefore render the full authenticated chrome — sidebar, connection dot, delete-block banner — and poll `/api/status` into a 401 loop behind the sign-in form. So the shape is a gate, not a route:

```
<AuthGate>            ← resolves ipc.getAuthSession() once; renders a neutral
  <BrowserRouter>       splash while pending, then Setup / Login / Token / children
    …existing layout…
```

- **The gate has three sign-in views**, because token-only mode still needs a browser path: `setup` (create the account), `login` (username and password), and `token` (paste the access token — the same credential as today, and what keeps a token-only deployment working once the hand-written page is gone). `GET /api/auth/session` returns which one to render, and that is why `auth` is a three-valued string rather than a boolean.
- **`redirectIfUnauthorised` in `ipc-http.ts:26` gets softer.** Today a 401 does `window.location.href = "/login"`, which is a full page load that discards unsaved form state. Instead it raises a session-expired event, the gate swaps to the sign-in view, and the attempted route is remembered so signing in returns you where you were.
- **The transport seam keeps "web only" honest.** `AuthGate` calls one ipc function, `getAuthSession()`. The Tauri and demo transports return `{auth:"none"}` and the gate renders its children immediately — so the desktop build never gains a login, and `DEMO=1` screenshots are unaffected. That is a single function per transport rather than a conditional sprinkled through the app.
- **Sign out** lives in the sidebar footer beside the theme toggle, rendered only when `auth === "user"`. Change password sits in Settings.
- **The server-rendered `_LOGIN_PAGE` string is replaced, not merely deleted**, along with `GET /login` and `POST /login`. `/login` becomes a client route served by the SPA shell — so its removal and the SPA views land in the same release (see [Phasing](#phasing)).
- **Screenshots** (`CLAUDE.md` step 7) need a new one for the sign-in page, which means the demo build needs a way to render it — a `DEMO=1` route that shows the form without a daemon behind it.

---

## The CLI, and the break-glass path

`cloud-drive-sync auth` is already taken by OAuth account sign-in, so the group is `user`:

```bash
cloud-drive-sync user set <name>      # create or replace the account; prompts twice, hidden
cloud-drive-sync user show            # username and dates, never the hash
cloud-drive-sync user clear           # remove the account; back to token-only
```

Three commands, because there is one account. `set` is create-and-update in one verb precisely because there is nothing to disambiguate.

These go over the IPC socket like `account add`, which means the daemon must be running — and the socket is the recovery credential, per [the scope boundary](#the-scope-boundary-comes-free). With the daemon **stopped** the fallback is not hand-editing a file (the account is a database row now, and a scrypt hash cannot be typed): it is starting the daemon. Since the account row is what gates the browser and the token is untouched by all of this, a forgotten password is recovered by running `user set` — or, if even that is unavailable, by clearing the row and re-running setup with the token from `config.toml`.

`user set` on an install with no HTTP token turns browser authentication **on** for that deployment. The command says so before it writes.

---

## Pinned behaviour that changes

| Test | Fate |
|---|---|
| `test_no_token_configured_allows_everything` | **Unchanged.** No token and no account is still wide open, and upgrades still cannot be locked out |
| every test in `test_feature_first_run_token.py` | **Unchanged.** The account does not touch token generation, precedence, or the stdout print |
| `test_an_unauthenticated_ui_request_gets_the_login_page` | **Changes.** The shell is served `200` and the SPA routes to a sign-in view — an SPA cannot route on a body it was not given. The bundle was already served anonymously via `/assets`, so nothing new is exposed |
| `test_the_login_page_is_reachable_unauthenticated`, `test_the_login_page_does_not_leak_the_token` | **Rewritten** against `/api/auth/session` and the SPA route |
| `test_the_cookie_is_accepted_so_the_browser_ui_can_work` | **Kept, but narrowed** — see the note below |
| `require_http_token` (webhooks) | **Widened** to "token set **or** an account exists"; still refuses when neither |

**The credentials must not be interchangeable**, and the review of this document expected that to cost `auth.is_authorised()` its signature: it takes a single `expected` and OR-matches the header *or* the cookie, which is only correct while both carry the same shared token.

It did not, and the reason is worth keeping. The session cookie is a **different cookie name** (`cds_session`, not `cds_token`), so the property holds by construction: a session id offered as a bearer token is compared against the access token and fails, and the access token offered as a session id is looked up in the session store and is not there. The narrowing that *did* happen is in the front-end's `_identify()`, which stops consulting the token cookie once an account exists. `is_authorised` kept its shape, and `mcp/server.py` — which passes `authorization` only — was untouched.

New test files, following the house naming: `test_feature_web_login.py` (hashing, encoding, the rate limiter, session lifecycle) and `test_feature_web_login_http.py` (the five endpoints, cookie hardening, CSRF rules, the single-row guarantee).

---

## Out of scope, stated plainly

- **Multi-user and roles.** One account, no roles — [decided](#one-account-not-a-user-list), not deferred. [Daemon](Daemon#authentication)'s "shared secret, not a user account" line becomes "one named account, no roles" — the same honesty, updated.
- **TLS termination.** The daemon still serves plain HTTP; a password on an untrusted network is sniffable exactly as the token is today. What this feature does is stop *silently* sending a session cookie in the clear when it knows better — see [Cookie flags](#cookie-flags).
- **OIDC, LDAP, 2FA.** Not built. The `auth` mode string is where they would hook in.
- **An audit trail.** Sign-ins are log lines, not activity-log rows; the activity log is about sync events. With one account there is nothing to attribute anyway.
- **Self-service password reset.** There is no mail transport and no second factor, so a reset flow would be a bypass with extra steps. Recovery is the CLI, or the token.

---

## Phasing

Each phase is independently shippable and leaves the product working, mirroring how the webhook feature landed (model first, wiring second).

| Phase | Content |
|---|---|
| 0 | `http/auth.py` primitives: hash, verify, encode/decode, session store, the rate limiter, and the `is_authorised` split. Pure functions, no wiring, fully tested |
| 1 | `SCHEMA_VERSION` 6 and the `web_user` table; IPC methods; CLI `user set` / `show` / `clear` |
| 2 | HTTP endpoints, middleware allow-list, CSRF rules, cookie flags, concurrency cap, `require_authentication`. `_LOGIN_PAGE` still serves the browser |
| 3 | `AuthGate` and its three views, sign out, change password, `make build-webui`, screenshots — **and only now** is `_LOGIN_PAGE` removed, because deleting it in phase 2 would leave a token-only deployment with no way to sign in |
| 4 | Documentation sweep, and the release |

---

## What building it changed

Three things the design did not survive contact with, recorded because each was a real gap rather than a detail.

**1. The token view needed an endpoint.** The design deleted the server-rendered `POST /login` and gave the SPA a `token` view, without saying where that view posts. So `POST /api/auth/token` exists: it validates the token and sets the same `cds_token` cookie the old form did, refuses once an account exists, and is throttled like the other credential endpoints. Without it, upgrading a token-only deployment would have removed its only way to sign in — the exact failure the phasing was written to avoid.

**2. `is_authorised()` did not need a new signature.** Two cookie names turned out to be sufficient. See the note under [Pinned behaviour that changes](#pinned-behaviour-that-changes).

**3. A pre-existing bug in the web build, found by the gate.** `AuthGate` lives in `App.tsx`, and `App.tsx` imports `"./lib/ipc"` — a specifier the `WEB=1` alias map did not cover (it lists `"../lib/ipc"` and `"./ipc"`). So in the web and demo builds, everything `App.tsx` called directly got the **Tauri** transport, whose `invoke()` is shimmed to reject: the emergency stop button, the delete-block banner and the reconnect button have never worked in the web UI, failing silently into a `.catch()`. The gate would have rendered the app for everyone regardless of sign-in, which is how it surfaced. One alias line fixes all of it, and it is why this feature also un-breaks three controls that predate it.

Also worth recording: the first browser check of the built bundle showed the dashboard with a "Cannot reach daemon" banner rather than a sign-in form. That was symptom (3), not a broken gate — and it is the reason a real end-to-end run belongs in the loop rather than only the test suite.

---

## Release

**This ships as a version bump, not a ride-along.** It adds a user-facing surface, a schema migration and a new database table, so it wants its own tag and its own release notes — the same reasoning [item 7](ROADMAP#7--authentication-on-by-default-for-new-installs) used for the first-run token.

One thing that had to be settled at tag time rather than here: **`v2.4.5` was never tagged.** The pair-uid work and webhook phases 0–1 sat on `main` unreleased, while [ROADMAP](ROADMAP) and [Proposal: Event Webhooks](Proposal-Event-Webhooks) already described fixes "in v2.4.5" as though shipped — a version that did not exist.

Settled as **`v2.5.0` carrying all three items** (9, 10 and 11), with those stale references corrected to `v2.5.0`. A minor bump is the honest label for a release that adds features; cutting a retroactive `v2.4.5` from an earlier commit would have meant two release pipelines and a patch number carrying a feature. Worth recording as a habit rather than an incident: docs that name a version before the tag exists are a claim the repository cannot back.

Per `CLAUDE.md` step 10 the pipeline injects the version from the git tag into `pyproject.toml` and `tauri.conf.json`, so there is no manual bump to make — only a tag to choose.

---

## Documentation to update

Not optional and not small — this feature contradicts several existing paragraphs rather than just adding to them:

- **[Daemon](Daemon#authentication)** (`docs/DAEMON.md`) — the whole Authentication section: the "shared secret, not a user account" claim, the CORS argument, and a new subsection on the account
- **[Configuration](Configuration)** — `[http] trust_proxy`, and a note that the account is in the database, not this file
- **[CLI](CLI)** — the `user` group
- **[Installation](Installation)** — the systemd and Docker warning boxes, which currently offer only "set a token"
- **`README.md`** — the 🔑 first-run note
- **[Architecture](Architecture)** (`docs/ARCHITECTURE.md`) — line 56, which says the front-ends take "an optional shared token"; and the schema version
- **[UI](UI)** — the sign-in views, and that they are web-only
- **[API-Reference](API-Reference)** (`docs/API.md`) — the `/api/auth/*` endpoints
- **[ROADMAP](ROADMAP)** — item 11

---

## Decisions settled in review

Recorded with the reasoning, so they are not re-litigated — including the two that went against the original recommendation.

| # | Question | Decision |
|---|---|---|
| 1 | Account in `config.toml` or a database table? | **Database table** (recommendation was config). See [Where the account lives](#where-the-account-lives) for the restore/reset objection and why one account survives it |
| 2 | Sessions in the database or in memory? | **In memory** (recommendation was a table). Restarting signs you out; that is the accepted price of no second migration |
| 3 | Does the token stay a *browser* credential once an account exists? | **No.** The `token` view and the `cds_token` cookie are for the pre-account state only; afterwards the token is purely a machine credential |
| 4 | Roles? | **None — and one account only.** Not a deferral; see [One account, not a user list](#one-account-not-a-user-list) |
| 5 | The `Secure` cookie flag | **Best practice**: set when the transport is TLS, or when a trusted proxy says so via `X-Forwarded-Proto` behind `[http] trust_proxy`. Never unconditionally, which would break plain-HTTP deployments |

**The two overrides are coherent together, and that is worth saying.** The original recommendation put the durable thing (the account) in the config file and the ephemeral thing (sessions) in the database. Review inverted both, which lands on the more conventional arrangement: records in the database, ephemeral state in process memory, and the config file left for configuration. What the inversion costs is a schema migration and a sign-out on restart; what it buys is that no credential is hand-edited and no password change rewrites the whole config. With a single account the one real risk — losing the row — is recoverable from the token that is still in `config.toml`.

One smaller question, answered the same way it was recommended: `POST /api/auth/login` is rate-limited **per username and per source address**, because with one account a per-username-only limiter is indistinguishable from a global one.
