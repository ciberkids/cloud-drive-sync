import { useState, useCallback, useEffect } from "react";
import * as ipc from "../lib/ipc";
import { FolderPicker } from "./FolderPicker";

interface BreadcrumbEntry {
  id: string;
  name: string;
}

interface RemoteFolderBrowserProps {
  authenticated: boolean;
  onAddPair: (remoteFolderId: string, localPath: string) => void;
  existingRemoteIds: Set<string>;
  accountId?: string;
}

export function RemoteFolderBrowser({
  authenticated,
  onAddPair,
  existingRemoteIds,
  accountId: _accountId,
}: RemoteFolderBrowserProps) {
  const [folders, setFolders] = useState<Array<{ id: string; name: string }>>(
    []
  );
  const [sharedDrives, setSharedDrives] = useState<Array<{ id: string; name: string }>>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [breadcrumbs, setBreadcrumbs] = useState<BreadcrumbEntry[]>([
    { id: "root", name: "My Drive" },
  ]);

  // Inline sync form state
  const [syncTarget, setSyncTarget] = useState<{
    id: string;
    name: string;
  } | null>(null);
  const [localPath, setLocalPath] = useState("");

  const loadFolders = useCallback(async (parentId: string) => {
    setLoading(true);
    setError(null);
    try {
      const result = await ipc.listRemoteFolders(parentId);
      if (result.error) {
        setError(result.error);
        setFolders([]);
        setSharedDrives([]);
      } else {
        setFolders(result.folders);
        setSharedDrives(result.shared_drives || []);
      }
    } catch (e) {
      setError(String(e));
      setFolders([]);
      setSharedDrives([]);
    } finally {
      setLoading(false);
    }
  }, []);

  // Auto-load root folders when authenticated
  useEffect(() => {
    if (authenticated) {
      loadFolders("root");
    }
  }, [authenticated, loadFolders]);

  const handleNavigateSharedDrive = async (drive: { id: string; name: string }) => {
    setBreadcrumbs([{ id: "root", name: "My Drive" }, { id: drive.id, name: drive.name }]);
    setSyncTarget(null);
    await loadFolders(drive.id);
  };

  const handleNavigate = async (folder: { id: string; name: string }) => {
    setBreadcrumbs((prev) => [...prev, folder]);
    setSyncTarget(null);
    await loadFolders(folder.id);
  };

  const handleBreadcrumb = async (index: number) => {
    const entry = breadcrumbs[index];
    setBreadcrumbs((prev) => prev.slice(0, index + 1));
    setSyncTarget(null);
    await loadFolders(entry.id);
  };

  const handleSyncClick = (folder: { id: string; name: string }) => {
    setSyncTarget(folder);
    setLocalPath("");
  };

  const handleSyncCurrentFolder = () => {
    const current = breadcrumbs[breadcrumbs.length - 1];
    setSyncTarget(current);
    setLocalPath("");
  };

  const handleAdd = () => {
    if (!syncTarget || !localPath) return;
    onAddPair(syncTarget.id, localPath);
    setSyncTarget(null);
    setLocalPath("");
  };

  const handleCancel = () => {
    setSyncTarget(null);
    setLocalPath("");
  };

  const handleRefresh = () => {
    const current = breadcrumbs[breadcrumbs.length - 1];
    loadFolders(current.id);
  };

  const currentFolderId = breadcrumbs[breadcrumbs.length - 1].id;
  const currentFolderSynced = existingRemoteIds.has(currentFolderId);

  return (
    <div
      className={`remote-browser ${!authenticated ? "remote-browser-disabled" : ""}`}
    >
      {!authenticated && (
        <div className="remote-browser-message">
          Connect your Google account to browse remote folders.
        </div>
      )}

      <div className="remote-browser-toolbar">
        <div className="remote-picker-breadcrumbs">
          {breadcrumbs.map((entry, i) => (
            <span key={entry.id + i}>
              {i > 0 && <span className="remote-picker-sep">/</span>}
              <button
                className="remote-picker-crumb"
                onClick={() => handleBreadcrumb(i)}
                type="button"
              >
                {entry.name}
              </button>
            </span>
          ))}
        </div>
        <div className="remote-browser-toolbar-actions">
          <button
            className="btn btn-sm"
            onClick={handleRefresh}
            disabled={loading}
            type="button"
            title="Refresh"
          >
            Refresh
          </button>
          <button
            className="btn btn-primary btn-sm"
            onClick={handleSyncCurrentFolder}
            disabled={currentFolderSynced}
            type="button"
          >
            {currentFolderSynced ? "Synced" : "Sync this folder"}
          </button>
        </div>
      </div>

      <div className="remote-browser-list">
        {loading && (
          <div className="remote-picker-loading">Loading...</div>
        )}
        {error && <div className="remote-picker-error">{error}</div>}
        {!loading && !error && folders.length === 0 && (
          <div className="remote-picker-empty">No subfolders</div>
        )}
        {!loading &&
          folders.map((folder) => {
            const synced = existingRemoteIds.has(folder.id);
            return (
              <div key={folder.id} className="remote-browser-folder-row">
                <button
                  className="remote-picker-folder"
                  onClick={() => handleNavigate(folder)}
                  type="button"
                >
                  <span className="remote-picker-folder-icon">📁</span>
                  <span className="remote-picker-folder-name">
                    {folder.name}
                  </span>
                </button>
                <button
                  className="btn btn-sm"
                  onClick={() => handleSyncClick(folder)}
                  disabled={synced}
                  type="button"
                >
                  {synced ? "Synced" : "+"}
                </button>
              </div>
            );
          })}
        {!loading && !error && sharedDrives.length > 0 && breadcrumbs.length === 1 && (
          <>
            <div className="remote-browser-section-header">Shared Drives</div>
            {sharedDrives.map((drive) => {
              const synced = existingRemoteIds.has(drive.id);
              return (
                <div key={drive.id} className="remote-browser-folder-row">
                  <button
                    className="remote-picker-folder"
                    onClick={() => handleNavigateSharedDrive(drive)}
                    type="button"
                  >
                    <span className="remote-picker-folder-icon">{"\uD83C\uDFE2"}</span>
                    <span className="remote-picker-folder-name">
                      {drive.name}
                    </span>
                  </button>
                  <button
                    className="btn btn-sm"
                    onClick={() => handleSyncClick(drive)}
                    disabled={synced}
                    type="button"
                  >
                    {synced ? "Synced" : "+"}
                  </button>
                </div>
              );
            })}
          </>
        )}
      </div>

      {syncTarget && (
        <div className="remote-browser-sync-form">
          <span className="remote-browser-sync-label">
            Sync <strong>{syncTarget.name || "My Drive"}</strong> to:
          </span>
          <FolderPicker
            value={localPath}
            onChange={setLocalPath}
            label="Local destination"
          />
          <div className="remote-browser-sync-actions">
            <button
              className="btn btn-primary btn-sm"
              onClick={handleAdd}
              disabled={!localPath}
              type="button"
            >
              Add
            </button>
            <button
              className="btn btn-sm"
              onClick={handleCancel}
              type="button"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
