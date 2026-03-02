import { useState, useCallback } from "react";
import * as ipc from "../lib/ipc";

interface BreadcrumbEntry {
  id: string;
  name: string;
}

interface RemoteFolderPickerProps {
  value: string;
  onChange: (folderId: string) => void;
  authenticated: boolean;
}

export function RemoteFolderPicker({
  value,
  onChange,
  authenticated,
}: RemoteFolderPickerProps) {
  const [open, setOpen] = useState(false);
  const [folders, setFolders] = useState<Array<{ id: string; name: string }>>(
    []
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [breadcrumbs, setBreadcrumbs] = useState<BreadcrumbEntry[]>([
    { id: "root", name: "My Drive" },
  ]);

  const loadFolders = useCallback(async (parentId: string) => {
    setLoading(true);
    setError(null);
    try {
      const result = await ipc.listRemoteFolders(parentId);
      if (result.error) {
        setError(result.error);
        setFolders([]);
      } else {
        setFolders(result.folders);
      }
    } catch (e) {
      setError(String(e));
      setFolders([]);
    } finally {
      setLoading(false);
    }
  }, []);

  const handleOpen = async () => {
    setOpen(true);
    setBreadcrumbs([{ id: "root", name: "My Drive" }]);
    await loadFolders("root");
  };

  const handleClose = () => {
    setOpen(false);
    setFolders([]);
    setError(null);
  };

  const handleNavigate = async (folder: { id: string; name: string }) => {
    setBreadcrumbs((prev) => [...prev, folder]);
    await loadFolders(folder.id);
  };

  const handleBreadcrumb = async (index: number) => {
    const entry = breadcrumbs[index];
    setBreadcrumbs((prev) => prev.slice(0, index + 1));
    await loadFolders(entry.id);
  };

  const handleSelect = () => {
    const current = breadcrumbs[breadcrumbs.length - 1];
    onChange(current.id);
    handleClose();
  };

  const displayValue =
    value === "root" || value === "" ? "My Drive (root)" : value;

  if (!authenticated) {
    return (
      <div className="field">
        <label className="field-label">Remote folder</label>
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="input"
          placeholder="root"
        />
        <small className="field-hint">
          Connect your Google account to browse folders
        </small>
      </div>
    );
  }

  return (
    <div className="field">
      <label className="field-label">Remote folder</label>
      <div className="remote-picker-row">
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="input"
          placeholder="root"
          readOnly={open}
        />
        <button
          onClick={open ? handleClose : handleOpen}
          className="btn btn-secondary"
          type="button"
        >
          {open ? "Cancel" : "Browse Drive"}
        </button>
      </div>

      {open && (
        <div className="remote-picker-panel">
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

          <div className="remote-picker-list">
            {loading && (
              <div className="remote-picker-loading">Loading...</div>
            )}
            {error && <div className="remote-picker-error">{error}</div>}
            {!loading && !error && folders.length === 0 && (
              <div className="remote-picker-empty">No subfolders</div>
            )}
            {!loading &&
              folders.map((folder) => (
                <button
                  key={folder.id}
                  className="remote-picker-folder"
                  onClick={() => handleNavigate(folder)}
                  type="button"
                >
                  <span className="remote-picker-folder-icon">📁</span>
                  <span className="remote-picker-folder-name">
                    {folder.name}
                  </span>
                </button>
              ))}
          </div>

          <div className="remote-picker-actions">
            <span className="remote-picker-selected">{displayValue}</span>
            <button
              className="btn btn-primary btn-sm"
              onClick={handleSelect}
              type="button"
            >
              Select
            </button>
          </div>
        </div>
      )}

      {!open && (
        <small className="field-hint">
          {displayValue}
        </small>
      )}
    </div>
  );
}
