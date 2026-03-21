import { useState, useEffect, useCallback } from "react";
import { useStatus } from "../lib/hooks";
import * as ipc from "../lib/ipc";
import type { Account } from "../lib/types";

export function AccountManager() {
  const status = useStatus();
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [authInProgress, setAuthInProgress] = useState(false);
  const [authMessage, setAuthMessage] = useState<string | null>(null);

  const refreshAccounts = useCallback(async () => {
    try {
      const result = await ipc.listAccounts();
      setAccounts(result);
    } catch {
      // Daemon may not be connected
    }
  }, []);

  useEffect(() => {
    refreshAccounts();
  }, [refreshAccounts]);

  // Re-fetch when status changes (e.g. after auth)
  useEffect(() => {
    if (status.connected) {
      refreshAccounts();
    }
  }, [status.connected, refreshAccounts]);

  const handleAddAccount = async () => {
    setAuthInProgress(true);
    setAuthMessage(null);
    try {
      const result = (await ipc.addAccount()) as {
        status?: string;
        message?: string;
      } | null;
      if (result && result.status === "ok") {
        setAuthMessage("Account added successfully!");
        await refreshAccounts();
      } else if (result && result.status === "error") {
        setAuthMessage(`Failed: ${result.message}`);
      }
    } catch (e) {
      console.error("Add account failed:", e);
      setAuthMessage(`Failed to add account: ${e}`);
    } finally {
      setAuthInProgress(false);
    }
  };

  const handleRemoveAccount = async (email: string) => {
    try {
      await ipc.removeAccount(email);
      setAuthMessage(`Removed ${email}`);
      await refreshAccounts();
    } catch (e) {
      console.error("Remove account failed:", e);
    }
  };

  return (
    <div className="account-manager">
      <h3>Google Accounts</h3>

      {accounts.length > 0 ? (
        <div className="accounts-list">
          {accounts.map((acct) => (
            <div key={acct.email} className="account-item">
              <div className="account-item-info">
                <span className="account-email">{acct.email}</span>
                <span className={`account-badge ${acct.status}`}>
                  {acct.status === "connected" ? "Connected" : "Disconnected"}
                </span>
              </div>
              <button
                onClick={() => handleRemoveAccount(acct.email)}
                className="btn btn-danger btn-sm"
              >
                Remove
              </button>
            </div>
          ))}
        </div>
      ) : (
        <p>No accounts configured. Add a Google account to start syncing.</p>
      )}

      {authMessage && <p className="auth-message">{authMessage}</p>}

      <button
        onClick={handleAddAccount}
        disabled={authInProgress}
        className="btn btn-primary"
      >
        {authInProgress ? "Waiting for browser..." : "Add Google Account"}
      </button>
      {authInProgress && (
        <p className="auth-message">
          A browser window should open for Google sign-in. Complete the
          authorization there, then return here.
        </p>
      )}
    </div>
  );
}
