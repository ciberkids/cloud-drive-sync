import { useState } from "react";
import { useSyncPairs } from "../lib/hooks";
import { FolderPicker } from "./FolderPicker";
import type { ConflictStrategy } from "../lib/types";
import * as ipc from "../lib/ipc";

export function Settings() {
  const { pairs, add, remove } = useSyncPairs();
  const [newLocalPath, setNewLocalPath] = useState("");
  const [newRemoteId, setNewRemoteId] = useState("root");
  const [conflictStrategy, setConflictStrategy] =
    useState<ConflictStrategy>("keep_both");
  const [saving, setSaving] = useState(false);

  const handleAddPair = async () => {
    if (!newLocalPath) return;
    try {
      await add(newLocalPath, newRemoteId);
      setNewLocalPath("");
      setNewRemoteId("root");
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
              <button
                onClick={() => handleRemovePair(pair.id)}
                className="btn btn-danger btn-sm"
              >
                Remove
              </button>
            </div>
          ))}
          {pairs.length === 0 && (
            <p className="empty-message">No sync folders configured.</p>
          )}
        </div>

        <div className="add-pair-form">
          <FolderPicker
            value={newLocalPath}
            onChange={setNewLocalPath}
            label="Local folder"
          />
          <div className="field">
            <label className="field-label">Remote folder ID</label>
            <input
              type="text"
              value={newRemoteId}
              onChange={(e) => setNewRemoteId(e.target.value)}
              className="input"
              placeholder="root"
            />
            <small className="field-hint">
              Use "root" for My Drive, or a specific folder ID
            </small>
          </div>
          <button
            onClick={handleAddPair}
            disabled={!newLocalPath}
            className="btn btn-primary"
          >
            Add Sync Folder
          </button>
        </div>
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
    </div>
  );
}
