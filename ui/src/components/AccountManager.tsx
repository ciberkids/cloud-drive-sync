import { useState } from "react";
import { useStatus } from "../lib/hooks";
import * as ipc from "../lib/ipc";

export function AccountManager() {
  const status = useStatus();
  const [authInProgress, setAuthInProgress] = useState(false);

  const handleLogin = async () => {
    setAuthInProgress(true);
    try {
      await ipc.startAuth();
    } catch (e) {
      console.error("Auth failed:", e);
    } finally {
      setAuthInProgress(false);
    }
  };

  const handleLogout = async () => {
    try {
      await ipc.logout();
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
          <button onClick={handleLogout} className="btn btn-danger">
            Disconnect Account
          </button>
        </div>
      ) : (
        <div className="account-info">
          <p>Connect your Google account to start syncing.</p>
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
