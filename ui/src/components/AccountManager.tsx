import { useState } from "react";
import { useStatus } from "../lib/hooks";
import * as ipc from "../lib/ipc";

export function AccountManager() {
  const status = useStatus();
  const [authInProgress, setAuthInProgress] = useState(false);
  const [authMessage, setAuthMessage] = useState<string | null>(null);

  const handleLogin = async () => {
    setAuthInProgress(true);
    setAuthMessage(null);
    try {
      const result = await ipc.startAuth() as { status?: string; message?: string } | null;
      if (result && result.status === "ok") {
        setAuthMessage(result.message || "Authentication successful! Your Google Drive will start syncing.");
      } else if (result && result.status === "no_auth_callback") {
        setAuthMessage("No authentication handler available.");
      }
    } catch (e) {
      console.error("Auth failed:", e);
      setAuthMessage(`Authentication failed: ${e}`);
    } finally {
      setAuthInProgress(false);
    }
  };

  const handleLogout = async () => {
    try {
      await ipc.logout();
      setAuthMessage("Logged out.");
    } catch (e) {
      console.error("Logout failed:", e);
    }
  };

  return (
    <div className="account-manager">
      <h3>Google Account</h3>

      {status.connected ? (
        <div className="account-info">
          <div className="account-status">
            <span className="account-badge connected">Connected</span>
          </div>
          {authMessage && <p className="auth-message">{authMessage}</p>}
          <button onClick={handleLogout} className="btn btn-danger">
            Disconnect Account
          </button>
        </div>
      ) : (
        <div className="account-info">
          <p>Connect your Google account to start syncing.</p>
          {authMessage && <p className="auth-message">{authMessage}</p>}
          <button
            onClick={handleLogin}
            disabled={authInProgress}
            className="btn btn-primary"
          >
            {authInProgress ? "Waiting for browser..." : "Connect Google Account"}
          </button>
          {authInProgress && (
            <p className="auth-message">
              A browser window should open for Google sign-in. Complete the authorization there, then return here.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
