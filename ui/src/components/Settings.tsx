import { useState, useEffect } from "react";
import { useSyncPairs, useStatus } from "../lib/hooks";
import { RemoteFolderBrowser } from "./RemoteFolderBrowser";
import type { Account, ConflictStrategy, SyncMode } from "../lib/types";
import * as ipc from "../lib/ipc";
import { homeDir as getHomeDir } from "@tauri-apps/api/path";

export function Settings() {
  const { pairs, add, remove, refresh } = useSyncPairs();
  const status = useStatus();
  const [homeDir, setHomeDir] = useState("~");

  useEffect(() => {
    getHomeDir().then((dir) => setHomeDir(dir)).catch(() => {});
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

  const handleIgnoreHiddenChange = async (pairId: string, ignoreHidden: boolean) => {
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

  const existingRemoteIds = new Set(pairs.map((p) => p.remote_folder_id));
  const configPath = `${homeDir}/.config/gdrive-sync/config.toml`;
  const dataPath = `${homeDir}/.local/share/gdrive-sync/`;

  return (
    <div className="settings">
      <h2>Settings</h2>

      <section className="settings-section">
        <h3>Sync Folders</h3>
        <div className="sync-pairs-list">
          {pairs.map((pair) => (
            <div key={pair.id} className="sync-pair-item">
              <div className="sync-pair-info">
                <span className="sync-pair-path">{pair.local_path}</span>
                <span className="sync-pair-remote">
                  Remote: {pair.remote_folder_id === "root" ? "My Drive" : pair.remote_folder_id}
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
                {accounts.length > 0 && (
                  <select
                    value={pair.account_id || ""}
                    className="select account-select"
                    disabled
                    title="Account is set when adding a sync pair"
                  >
                    <option value="">Default account</option>
                    {accounts.map((acct) => (
                      <option key={acct.email} value={acct.email}>
                        {acct.email}
                      </option>
                    ))}
                  </select>
                )}
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
                  onClick={() => toggleIgnorePanel(pair.id, pair.ignore_patterns || [])}
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
                        setIgnoreText((prev) => ({ ...prev, [pair.id]: e.target.value }))
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
          ))}
          {pairs.length === 0 && (
            <p className="empty-message">No sync folders configured.</p>
          )}
        </div>
      </section>

      <section className="settings-section">
        <h3>Remote Folders</h3>
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
