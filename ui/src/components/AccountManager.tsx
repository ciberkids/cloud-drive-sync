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
      const result = await ipc.startAuth() as { status?: string } | null;
      if (result && result.status === "ok") {
        setAuthMessage("Authentication successful. If running in demo mode, no real Google account is needed — sync works with the local mock Drive.");
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
            {authInProgress ? "Authenticating..." : "Connect Google Account"}
          </button>
        </div>
      )}
    </div>
  );
}
