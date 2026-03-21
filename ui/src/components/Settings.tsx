import { useState, useEffect } from "react";
import { useSyncPairs, useStatus } from "../lib/hooks";
import { RemoteFolderBrowser } from "./RemoteFolderBrowser";
import { providerLabel, providerColor } from "./AccountManager";
import type { Account, ConflictStrategy, SyncMode } from "../lib/types";
import * as ipc from "../lib/ipc";
import { homeDir as getHomeDir } from "@tauri-apps/api/path";

export function Settings() {
  const { pairs, add, remove, refresh } = useSyncPairs();
  const status = useStatus();
  const [homeDir, setHomeDir] = useState("~");

  useEffect(() => {
    getHomeDir()
      .then((dir) => setHomeDir(dir))
      .catch(() => {});
  }, []);
  const [accounts, setAccounts] = useState<Account[]>([]);

  useEffect(() => {
    ipc.listAccounts().then(setAccounts).catch(() => {});
  }, []);

  const [conflictStrategy, setConflictStrategy] =
    useState<ConflictStrategy>("keep_both");
  const [saving, setSaving] = useState(false);

  const handleAddPair = async (remoteFolderId: string, localPath: string) => {
    try {
      await add(localPath, remoteFolderId);
    } catch (e) {
      console.error("Failed to add sync pair:", e);
    }
  };

  const handleRemovePair = async (id: string) => {
    try {
      await remove(id);
    } catch (e) {
      console.error("Failed to remove sync pair:", e);
    }
  };

  const handleIgnoreHiddenChange = async (
    pairId: string,
    ignoreHidden: boolean
  ) => {
    try {
      await ipc.setIgnoreHidden(pairId, ignoreHidden);
      refresh();
    } catch (e) {
      console.error("Failed to set ignore_hidden:", e);
    }
  };

  const handleSyncModeChange = async (pairId: string, mode: SyncMode) => {
    try {
      await ipc.setSyncMode(pairId, mode);
      refresh();
    } catch (e) {
      console.error("Failed to set sync mode:", e);
    }
  };

  const [expandedIgnore, setExpandedIgnore] = useState<Set<string>>(new Set());
  const [ignoreText, setIgnoreText] = useState<Record<string, string>>({});

  const toggleIgnorePanel = (pairId: string, currentPatterns: string[]) => {
    setExpandedIgnore((prev) => {
      const next = new Set(prev);
      if (next.has(pairId)) {
        next.delete(pairId);
      } else {
        next.add(pairId);
        setIgnoreText((t) => ({
          ...t,
          [pairId]: currentPatterns.join("\n"),
        }));
      }
      return next;
    });
  };

  const handleSaveIgnorePatterns = async (pairId: string) => {
    const text = ignoreText[pairId] || "";
    const patterns = text
      .split("\n")
      .map((l) => l.trim())
      .filter((l) => l && !l.startsWith("#"));
    try {
      await ipc.setIgnorePatterns(pairId, patterns);
      refresh();
    } catch (e) {
      console.error("Failed to set ignore patterns:", e);
    }
  };

  const handleStrategyChange = async (strategy: ConflictStrategy) => {
    setSaving(true);
    try {
      await ipc.setConflictStrategy(strategy);
      setConflictStrategy(strategy);
    } catch (e) {
      console.error("Failed to set strategy:", e);
    } finally {
      setSaving(false);
    }
  };

  const [notifPrefs, setNotifPrefs] = useState({
    notify_sync_complete: true,
    notify_conflicts: true,
    notify_errors: true,
  });

  useEffect(() => {
    ipc.getNotificationPrefs().then(setNotifPrefs).catch(() => {});
  }, []);

  const handleNotifChange = async (key: string, value: boolean) => {
    try {
      const updated = await ipc.setNotificationPrefs({ [key]: value });
      setNotifPrefs(updated);
    } catch (e) {
      console.error("Failed to set notification prefs:", e);
    }
  };

  // Group pairs by account
  const accountMap = new Map<string, Account>();
  for (const acct of accounts) {
    accountMap.set(acct.email, acct);
  }

  type PairGroup = {
    accountId: string;
    account: Account | null;
    provider: string;
    pairs: typeof pairs;
  };

  const groups: PairGroup[] = [];
  const groupMap = new Map<string, PairGroup>();

  for (const pair of pairs) {
    const key = pair.account_id || "";
    if (!groupMap.has(key)) {
      const acct = accountMap.get(key) || null;
      const provider = pair.provider || acct?.provider || "gdrive";
      const group: PairGroup = {
        accountId: key,
        account: acct,
        provider,
        pairs: [],
      };
      groupMap.set(key, group);
      groups.push(group);
    }
    groupMap.get(key)!.pairs.push(pair);
  }

  const existingRemoteIds = new Set(pairs.map((p) => p.remote_folder_id));
  const configPath = `${homeDir}/.config/cloud-drive-sync/config.toml`;
  const dataPath = `${homeDir}/.local/share/cloud-drive-sync/`;

  const renderPairCard = (pair: (typeof pairs)[0]) => (
    <div key={pair.id} className="sync-pair-item">
      <div className="sync-pair-info">
        <span className="sync-pair-path">{pair.local_path}</span>
        <span className="sync-pair-remote">
          Remote:{" "}
          {pair.remote_folder_id === "root"
            ? "My Drive"
            : pair.remote_folder_id || "/"}
        </span>
      </div>
      <div className="sync-pair-actions">
        <select
          value={pair.sync_mode}
          onChange={(e) =>
            handleSyncModeChange(pair.id, e.target.value as SyncMode)
          }
          className="select sync-mode-select"
        >
          <option value="two_way">Two-way</option>
          <option value="upload_only">Upload only</option>
          <option value="download_only">Download only</option>
        </select>
        <label className="toggle-switch">
          <input
            type="checkbox"
            checked={pair.ignore_hidden ?? true}
            onChange={(e) =>
              handleIgnoreHiddenChange(pair.id, e.target.checked)
            }
          />
          <span className="toggle-slider" />
          <span className="toggle-label">Hide dotfiles</span>
        </label>
        <button
          onClick={() => handleRemovePair(pair.id)}
          className="btn btn-danger btn-sm"
        >
          Remove
        </button>
      </div>
      <div className="sync-pair-ignore">
        <button
          className="btn btn-sm"
          onClick={() =>
            toggleIgnorePanel(pair.id, pair.ignore_patterns || [])
          }
          type="button"
        >
          {expandedIgnore.has(pair.id) ? "Hide" : "Ignore Patterns"}
        </button>
        {expandedIgnore.has(pair.id) && (
          <div className="ignore-patterns-editor">
            <textarea
              className="ignore-patterns-textarea"
              placeholder="One pattern per line (e.g. *.log, node_modules, build/)"
              value={ignoreText[pair.id] || ""}
              onChange={(e) =>
                setIgnoreText((prev) => ({
                  ...prev,
                  [pair.id]: e.target.value,
                }))
              }
              rows={5}
            />
            <button
              className="btn btn-primary btn-sm"
              onClick={() => handleSaveIgnorePatterns(pair.id)}
              type="button"
            >
              Save Patterns
            </button>
          </div>
        )}
      </div>
    </div>
  );

  return (
    <div className="settings">
      <h2>Settings</h2>

      <section className="settings-section">
        <h3>Sync Folders</h3>

        {groups.length > 0 ? (
          <div className="sync-groups">
            {groups.map((group) => {
              const color = providerColor(group.provider);
              const label = providerLabel(group.provider);
              const email = group.accountId || "Default account";

              return (
                <div key={group.accountId} className="sync-group">
                  <div
                    className="sync-group-header"
                    style={{ borderLeftColor: color }}
                  >
                    <span
                      className="provider-dot"
                      style={{ background: color }}
                    />
                    <span className="sync-group-provider">{label}</span>
                    <span className="sync-group-email">{email}</span>
                    {group.account && (
                      <span
                        className={`account-status-dot ${group.account.status}`}
                        title={
                          group.account.status === "connected"
                            ? "Connected"
                            : "Disconnected"
                        }
                      />
                    )}
                  </div>
                  <div className="sync-group-pairs">
                    {group.pairs.map(renderPairCard)}
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <p className="empty-message">No sync folders configured.</p>
        )}
      </section>

      <section className="settings-section">
        <h3>Add Sync Folder</h3>
        <RemoteFolderBrowser
          authenticated={status.connected}
          onAddPair={handleAddPair}
          existingRemoteIds={existingRemoteIds}
        />
      </section>

      <section className="settings-section">
        <h3>Conflict Resolution</h3>
        <div className="field">
          <label className="field-label">Strategy</label>
          <select
            value={conflictStrategy}
            onChange={(e) =>
              handleStrategyChange(e.target.value as ConflictStrategy)
            }
            disabled={saving}
            className="select"
          >
            <option value="keep_both">
              Keep both (rename conflicting file)
            </option>
            <option value="newest_wins">Newest wins (overwrite older)</option>
            <option value="ask_user">Ask me (show dialog)</option>
          </select>
        </div>
      </section>

      <section className="settings-section">
        <h3>Notifications</h3>
        <div className="notification-prefs">
          <label className="toggle-switch">
            <input
              type="checkbox"
              checked={notifPrefs.notify_sync_complete}
              onChange={(e) =>
                handleNotifChange("notify_sync_complete", e.target.checked)
              }
            />
            <span className="toggle-slider" />
            <span className="toggle-label">Sync complete</span>
          </label>
          <label className="toggle-switch">
            <input
              type="checkbox"
              checked={notifPrefs.notify_conflicts}
              onChange={(e) =>
                handleNotifChange("notify_conflicts", e.target.checked)
              }
            />
            <span className="toggle-slider" />
            <span className="toggle-label">Conflict detected</span>
          </label>
          <label className="toggle-switch">
            <input
              type="checkbox"
              checked={notifPrefs.notify_errors}
              onChange={(e) =>
                handleNotifChange("notify_errors", e.target.checked)
              }
            />
            <span className="toggle-slider" />
            <span className="toggle-label">Sync errors</span>
          </label>
        </div>
      </section>

      <section className="settings-section">
        <h3>Storage</h3>
        <div className="config-paths">
          <div className="config-path-item">
            <span className="config-path-label">Configuration</span>
            <code className="config-path-value">{configPath}</code>
          </div>
          <div className="config-path-item">
            <span className="config-path-label">Data &amp; database</span>
            <code className="config-path-value">{dataPath}</code>
          </div>
        </div>
      </section>
    </div>
  );
}
