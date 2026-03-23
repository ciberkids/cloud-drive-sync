import { useState, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { useStatus } from "../lib/hooks";
import { useSyncPairs } from "../lib/hooks";
import * as ipc from "../lib/ipc";
import type { Account, SyncPair } from "../lib/types";

const PROVIDER_META: Record<
  string,
  { label: string; color: string; icon: string }
> = {
  gdrive: { label: "Google Drive", color: "#4285f4", icon: "\u2601" },
  dropbox: { label: "Dropbox", color: "#0061fe", icon: "\u25BC" },
  onedrive: { label: "OneDrive", color: "#0078d4", icon: "\u2601" },
  nextcloud: { label: "Nextcloud", color: "#0082c9", icon: "\u2601" },
  box: { label: "Box", color: "#0061d5", icon: "\u25A0" },
  proton: { label: "Proton Drive", color: "#6d4aff", icon: "\u25C6" },
};

function providerLabel(p?: string) {
  return PROVIDER_META[p || "gdrive"]?.label ?? p ?? "Google Drive";
}

function providerColor(p?: string) {
  return PROVIDER_META[p || "gdrive"]?.color ?? "#4285f4";
}

const SYNC_MODE_LABELS: Record<string, string> = {
  two_way: "Two-way",
  upload_only: "Upload only",
  download_only: "Download only",
};

export { PROVIDER_META, providerLabel, providerColor };

export function AccountManager() {
  const status = useStatus();
  const { pairs } = useSyncPairs();
  const navigate = useNavigate();
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [authInProgress, setAuthInProgress] = useState(false);
  const [authMessage, setAuthMessage] = useState<string | null>(null);
  const [selectedProvider, setSelectedProvider] = useState("gdrive");

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

  // Group pairs by account_id
  const pairsByAccount: Record<string, SyncPair[]> = {};
  for (const pair of pairs) {
    const key = pair.account_id || "";
    if (!pairsByAccount[key]) pairsByAccount[key] = [];
    pairsByAccount[key].push(pair);
  }

  return (
    <div className="account-manager">
      <h2>Cloud Accounts</h2>

      {accounts.length > 0 ? (
        <div className="account-cards">
          {accounts.map((acct) => {
            const provider = acct.provider || "gdrive";
            const color = providerColor(provider);
            const acctPairs = pairsByAccount[acct.email] || [];

            return (
              <div
                key={acct.email}
                className="account-card"
                style={{ borderLeftColor: color }}
              >
                <div className="account-card-header">
                  <div className="account-card-provider">
                    <span
                      className="provider-dot"
                      style={{ background: color }}
                    />
                    <span className="provider-name">
                      {providerLabel(provider)}
                    </span>
                  </div>
                  <span
                    className={`account-status-badge ${acct.status}`}
                  >
                    {acct.status === "connected"
                      ? "Connected"
                      : "Disconnected"}
                  </span>
                </div>

                <div className="account-card-email">{acct.email}</div>

                {acctPairs.length > 0 ? (
                  <div className="account-card-pairs">
                    <span className="account-card-pairs-label">
                      Syncing {acctPairs.length} folder
                      {acctPairs.length !== 1 ? "s" : ""}:
                    </span>
                    <div className="account-pair-list">
                      {acctPairs.map((pair) => (
                        <div
                          key={pair.id}
                          className="account-pair-row account-pair-row-link"
                          onClick={() => navigate("/settings")}
                          title="Go to sync folder settings"
                        >
                          <span className="account-pair-local">
                            {pair.local_path.replace(/^\/home\/[^/]+/, "~")}
                          </span>
                          <span className="account-pair-arrow">
                            {pair.sync_mode === "upload_only"
                              ? "\u2192"
                              : pair.sync_mode === "download_only"
                              ? "\u2190"
                              : "\u21C4"}
                          </span>
                          <span className="account-pair-remote">
                            {pair.remote_folder_id === "root"
                              ? "My Drive"
                              : pair.remote_folder_id || "/"}
                          </span>
                          <span className="account-pair-mode">
                            {SYNC_MODE_LABELS[pair.sync_mode] || pair.sync_mode}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : (
                  <div className="account-card-empty">
                    No sync folders configured for this account.
                  </div>
                )}

                <div className="account-card-actions">
                  <button
                    onClick={() => handleRemoveAccount(acct.email)}
                    className="btn btn-danger btn-sm"
                  >
                    Remove Account
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <div className="account-empty-state">
          <p>No accounts configured. Add a cloud account to start syncing.</p>
        </div>
      )}

      {authMessage && <p className="auth-message">{authMessage}</p>}

      <div className="account-add-section">
        <select
          value={selectedProvider}
          onChange={(e) => setSelectedProvider(e.target.value)}
          className="select provider-select"
        >
          {Object.entries(PROVIDER_META).map(([key, meta]) => (
            <option key={key} value={key} disabled={key === "proton"}>
              {meta.label}
              {key === "proton" ? " (coming soon)" : ""}
            </option>
          ))}
        </select>
        <button
          onClick={handleAddAccount}
          disabled={authInProgress || selectedProvider === "proton"}
          className="btn btn-primary"
        >
          {authInProgress ? "Waiting for browser..." : "Add Account"}
        </button>
      </div>

      {authInProgress && (
        <p className="auth-message">
          A browser window should open for sign-in. Complete the authorization
          there, then return here. If you close the browser, the request will
          time out after 2 minutes and you can try again.
        </p>
      )}
    </div>
  );
}
