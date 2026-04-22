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

  const [authUrl, setAuthUrl] = useState<string | null>(null);
  const [authCode, setAuthCode] = useState("");
  const [authProvider, setAuthProvider] = useState("gdrive");

  // Credential-form state for non-OAuth providers (e.g. Nextcloud)
  const [ncServerUrl, setNcServerUrl] = useState("");
  const [ncUsername, setNcUsername] = useState("");
  const [ncAppPassword, setNcAppPassword] = useState("");

  const CREDENTIAL_PROVIDERS = new Set(["nextcloud"]);

  const handleAddAccount = async () => {
    setAuthInProgress(true);
    setAuthMessage(null);
    setAuthUrl(null);
    setAuthCode("");

    // For credential-based providers, validate form fields before sending
    if (CREDENTIAL_PROVIDERS.has(selectedProvider)) {
      if (!ncServerUrl.trim() || !ncUsername.trim() || !ncAppPassword.trim()) {
        setAuthMessage("Please fill in all fields: server URL, username, and app password.");
        setAuthInProgress(false);
        return;
      }
    }

    const extra = CREDENTIAL_PROVIDERS.has(selectedProvider)
      ? { server_url: ncServerUrl.trim().replace(/\/$/, ""), username: ncUsername.trim(), app_password: ncAppPassword.trim() }
      : undefined;

    try {
      const result = (await ipc.addAccount(selectedProvider, extra)) as {
        status?: string;
        message?: string;
        auth_url?: string;
        provider?: string;
      } | null;
      if (result && result.status === "auth_url" && result.auth_url) {
        // Two-step flow: show auth URL and code input
        setAuthUrl(result.auth_url);
        setAuthProvider(result.provider || selectedProvider);
        setAuthInProgress(false);
        return;
      }
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

  const handleSubmitAuthCode = async () => {
    if (!authCode.trim()) return;
    setAuthInProgress(true);
    setAuthMessage(null);
    try {
      const exchangeFn = (ipc as Record<string, unknown>).exchangeAuthCode as
        | ((provider: string, code: string) => Promise<{ status?: string; email?: string; message?: string }>)
        | undefined;
      if (exchangeFn) {
        const result = await exchangeFn(authProvider, authCode.trim());
        if (result && result.status === "ok") {
          setAuthMessage(`Account added: ${result.email || authProvider}`);
          setAuthUrl(null);
          setAuthCode("");
          await refreshAccounts();
        } else {
          setAuthMessage(`Failed: ${result?.message || "Unknown error"}`);
        }
      } else {
        setAuthMessage("Code exchange not supported in this mode");
      }
    } catch (e) {
      setAuthMessage(`Failed: ${e}`);
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

        {!CREDENTIAL_PROVIDERS.has(selectedProvider) && (
          <button
            onClick={handleAddAccount}
            disabled={authInProgress || selectedProvider === "proton"}
            className="btn btn-primary"
          >
            {authInProgress ? "Waiting for browser..." : "Add Account"}
          </button>
        )}
      </div>

      {CREDENTIAL_PROVIDERS.has(selectedProvider) && (
        <div className="nextcloud-creds-form">
          <input
            type="url"
            className="input"
            placeholder="Server URL (e.g. https://cloud.example.com)"
            value={ncServerUrl}
            onChange={(e) => setNcServerUrl(e.target.value)}
            disabled={authInProgress}
          />
          <input
            type="text"
            className="input"
            placeholder="Username"
            value={ncUsername}
            onChange={(e) => setNcUsername(e.target.value)}
            disabled={authInProgress}
            autoComplete="username"
          />
          <input
            type="password"
            className="input"
            placeholder="App password (Settings → Security → Devices & sessions)"
            value={ncAppPassword}
            onChange={(e) => setNcAppPassword(e.target.value)}
            disabled={authInProgress}
            autoComplete="new-password"
          />
          <button
            onClick={handleAddAccount}
            disabled={authInProgress}
            className="btn btn-primary"
          >
            {authInProgress ? "Connecting…" : "Connect"}
          </button>
        </div>
      )}

      {authInProgress && !authUrl && !CREDENTIAL_PROVIDERS.has(selectedProvider) && (
        <p className="auth-message">
          A browser window should open for sign-in. Complete the authorization
          there, then return here. If you close the browser, the request will
          time out after 2 minutes and you can try again.
        </p>
      )}

      {authUrl && (
        <div className="auth-message" style={{ lineHeight: 1.8 }}>
          <strong>Step 1:</strong>{" "}
          <a href={authUrl} target="_blank" rel="noopener noreferrer" className="btn btn-primary" style={{ textDecoration: "none", display: "inline-block", margin: "8px 0" }}>
            Sign in with {providerLabel(authProvider)}
          </a>
          <br />
          <strong>Step 2:</strong> After clicking "Allow", your browser will show a page that{" "}
          <strong>can't load</strong> — that's expected.
          <br />
          <span style={{ color: "var(--text-secondary)", fontSize: "12px" }}>
            The address bar will look like:{" "}
            <code style={{ background: "var(--bg-primary)", padding: "2px 6px", borderRadius: "4px" }}>
              http://localhost/?code=4/0AfJohX...
            </code>
          </span>
          <br />
          <strong>Step 3:</strong> Copy the <strong>entire URL</strong> from the address bar and paste it here:
          <div style={{ display: "flex", gap: "8px", marginTop: "8px" }}>
            <input
              type="text"
              className="input"
              placeholder="http://localhost/?code=4/0AfJohX..."
              value={authCode}
              onChange={(e) => setAuthCode(e.target.value)}
              style={{ flex: 1, fontFamily: "monospace", fontSize: "12px" }}
            />
            <button
              className="btn btn-primary"
              onClick={handleSubmitAuthCode}
              disabled={!authCode.trim() || authInProgress}
            >
              Complete Setup
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
