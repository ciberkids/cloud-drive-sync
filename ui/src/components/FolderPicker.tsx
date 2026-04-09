import { useState, useEffect } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { homeDir as getHomeDir } from "@tauri-apps/api/path";

interface FolderPickerProps {
  value: string;
  onChange: (path: string) => void;
  label?: string;
}

interface DirEntry {
  name: string;
  path: string;
}

export function FolderPicker({
  value,
  onChange,
  label = "Local folder",
}: FolderPickerProps) {
  const [home, setHome] = useState("/home");
  const hasNativeDialog = !!(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__;
  const [showBrowser, setShowBrowser] = useState(false);
  const [browserPath, setBrowserPath] = useState("");
  const [browserDirs, setBrowserDirs] = useState<DirEntry[]>([]);
  const [browserParent, setBrowserParent] = useState<string | null>(null);
  const [browserError, setBrowserError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    getHomeDir().then((dir) => setHome(dir)).catch(() => {});
  }, []);

  const handlePick = async () => {
    const selected = await open({
      directory: true,
      multiple: false,
      title: "Select sync folder",
      defaultPath: home,
    });
    if (selected && typeof selected === "string") {
      onChange(selected);
    }
  };

  const loadDir = async (path?: string) => {
    setLoading(true);
    setBrowserError(null);
    try {
      // Dynamic import to avoid bundling in Tauri mode
      const ipc = await import("../lib/ipc");
      const listLocalDirs = (ipc as Record<string, unknown>).listLocalDirs as
        | ((p?: string) => Promise<{ path: string; parent: string | null; dirs: DirEntry[]; error?: string }>)
        | undefined;
      if (!listLocalDirs) {
        setBrowserError("Directory browsing not available");
        setLoading(false);
        return;
      }
      const result = await listLocalDirs(path);
      setBrowserPath(result.path);
      setBrowserParent(result.parent);
      setBrowserDirs(result.dirs);
      if (result.error) setBrowserError(result.error);
    } catch (e) {
      setBrowserError(String(e));
    } finally {
      setLoading(false);
    }
  };

  const handleOpenBrowser = () => {
    setShowBrowser(true);
    loadDir(value || undefined);
  };

  const handleSelectDir = (path: string) => {
    onChange(path);
    setShowBrowser(false);
  };

  return (
    <div className="folder-picker">
      {label && <label className="field-label">{label}</label>}
      <div className="folder-picker-row">
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={`${home}/Google Drive`}
          className="input"
        />
        {hasNativeDialog ? (
          <button onClick={handlePick} className="btn btn-secondary" type="button">
            Browse
          </button>
        ) : (
          <button onClick={handleOpenBrowser} className="btn btn-secondary" type="button">
            Browse
          </button>
        )}
      </div>

      {showBrowser && (
        <div className="folder-browser">
          <div className="folder-browser-header">
            <span className="folder-browser-path" title={browserPath}>{browserPath}</span>
            <button
              className="btn btn-sm btn-primary"
              onClick={() => handleSelectDir(browserPath)}
              type="button"
            >
              Select this folder
            </button>
            <button
              className="btn btn-sm"
              onClick={() => setShowBrowser(false)}
              type="button"
            >
              Cancel
            </button>
          </div>
          {browserError && <div className="folder-browser-error">{browserError}</div>}
          <div className="folder-browser-list">
            {loading && <div className="folder-browser-loading">Loading...</div>}
            {browserParent && (
              <div
                className="folder-browser-item folder-browser-parent"
                onClick={() => loadDir(browserParent!)}
              >
                .. (parent)
              </div>
            )}
            {browserDirs.map((dir) => (
              <div
                key={dir.path}
                className="folder-browser-item"
                onClick={() => loadDir(dir.path)}
                onDoubleClick={() => handleSelectDir(dir.path)}
                title={`Click to enter, double-click to select: ${dir.path}`}
              >
                📁 {dir.name}
              </div>
            ))}
            {!loading && browserDirs.length === 0 && !browserError && (
              <div className="folder-browser-empty">No subdirectories</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
