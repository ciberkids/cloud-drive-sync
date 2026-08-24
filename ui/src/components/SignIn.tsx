/**
 * The web UI's sign-in screen.
 *
 * Three views, because there are three ways in — and which one shows is the
 * daemon's answer to `GET /api/auth/session`, never a guess made here:
 *
 *   "user"  → username and password. An account exists.
 *   "token" → paste the access token. The pre-account state: a deployment that
 *             has a token in its compose file and no account yet, which is a
 *             supported steady state rather than a half-finished setup.
 *   setup   → create the account, proving you are the operator with that same
 *             token. Offered from the token view rather than forced, so nobody
 *             is pushed into an account they did not ask for.
 *
 * The desktop app never renders any of this: it reaches the daemon over a Unix
 * socket, so its transport reports "none" and AuthGate goes straight to the app.
 */

import { useState } from "react";
import * as ipc from "../lib/ipc";
import type { AuthSession } from "../lib/types";

interface Props {
  session: AuthSession;
  /** Called after a successful sign-in so the app can re-resolve and render. */
  onSignedIn: () => void;
}

export function SignIn({ session, onSignedIn }: Props) {
  const [mode, setMode] = useState<"signin" | "setup">("signin");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const creating = mode === "setup";

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    // Checked here as well as on the daemon, purely so the user hears about a
    // mistyped confirmation without a round trip. The daemon is the authority.
    if (creating && password !== confirm) {
      setError("Those passwords do not match.");
      return;
    }

    setBusy(true);
    try {
      if (creating) {
        await ipc.createAccount(token, username, password);
      } else if (session.auth === "user") {
        await ipc.signIn(username, password);
      } else {
        await ipc.signInWithToken(token);
      }
      onSignedIn();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };

  return (
    <div className="signin-screen">
      <form className="signin-card" onSubmit={submit}>
        <h1>Cloud Drive Sync</h1>
        <p className="signin-lead">
          {creating
            ? "Create the account for this daemon."
            : session.auth === "user"
            ? "Sign in to continue."
            : "This daemon requires its access token."}
        </p>

        {(creating || session.auth === "token") && (
          <label className="signin-field">
            <span>Access token</span>
            <input
              type="password"
              value={token}
              autoFocus
              autoComplete="off"
              onChange={(e) => setToken(e.target.value)}
              placeholder={creating ? "Proves you are the operator" : ""}
            />
          </label>
        )}

        {(creating || session.auth === "user") && (
          <>
            <label className="signin-field">
              <span>Username</span>
              <input
                type="text"
                value={username}
                autoFocus={session.auth === "user"}
                autoComplete="username"
                onChange={(e) => setUsername(e.target.value)}
              />
            </label>
            <label className="signin-field">
              <span>Password</span>
              <input
                type="password"
                value={password}
                autoComplete={creating ? "new-password" : "current-password"}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>
          </>
        )}

        {creating && (
          <label className="signin-field">
            <span>Confirm password</span>
            <input
              type="password"
              value={confirm}
              autoComplete="new-password"
              onChange={(e) => setConfirm(e.target.value)}
            />
          </label>
        )}

        {error && (
          <p className="signin-error" role="alert">
            {error}
          </p>
        )}

        <button className="btn btn-primary signin-submit" type="submit" disabled={busy}>
          {busy ? "Working…" : creating ? "Create account" : "Sign in"}
        </button>

        {session.setup_available && (
          <button
            type="button"
            className="signin-switch"
            onClick={() => {
              setMode(creating ? "signin" : "setup");
              setError(null);
            }}
          >
            {creating
              ? "Use the access token instead"
              : "Create an account with a username and password"}
          </button>
        )}

        {creating && (
          <p className="signin-hint">
            Your token was printed when the daemon first started, and is stored in
            the config file under <code>[http] token</code>.
          </p>
        )}
      </form>
    </div>
  );
}

/**
 * Change password, for the Settings page.
 *
 * Every session is invalidated on success, including this one — the daemon
 * immediately issues a fresh cookie to the caller, so the person who changed it
 * stays signed in and everyone else does not.
 */
export function ChangePassword({ username }: { username: string | null }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setDone(false);
    if (next !== confirm) {
      setError("Those passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      await ipc.changePassword(current, next);
      setDone(true);
      setCurrent("");
      setNext("");
      setConfirm("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="change-password" onSubmit={submit}>
      <p className="setting-description">
        Signed in as <strong>{username || "—"}</strong>. Changing the password signs
        out every other browser.
      </p>
      <label className="signin-field">
        <span>Current password</span>
        <input
          type="password"
          value={current}
          autoComplete="current-password"
          onChange={(e) => setCurrent(e.target.value)}
        />
      </label>
      <label className="signin-field">
        <span>New password</span>
        <input
          type="password"
          value={next}
          autoComplete="new-password"
          onChange={(e) => setNext(e.target.value)}
        />
      </label>
      <label className="signin-field">
        <span>Confirm new password</span>
        <input
          type="password"
          value={confirm}
          autoComplete="new-password"
          onChange={(e) => setConfirm(e.target.value)}
        />
      </label>
      {error && (
        <p className="signin-error" role="alert">
          {error}
        </p>
      )}
      {done && <p className="signin-ok">Password changed.</p>}
      <button className="btn btn-primary" type="submit" disabled={busy}>
        {busy ? "Working…" : "Change password"}
      </button>
    </form>
  );
}
