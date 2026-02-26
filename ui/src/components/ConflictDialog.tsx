import { useConflicts } from "../lib/hooks";
import type { ConflictResolution } from "../lib/types";
import * as ipc from "../lib/ipc";

export function ConflictDialog() {
  const { conflicts, refresh } = useConflicts();

  const handleResolve = async (
    conflictId: string,
    resolution: ConflictResolution
  ) => {
    try {
      await ipc.resolveConflict(conflictId, resolution);
      await refresh();
    } catch (e) {
      console.error("Failed to resolve conflict:", e);
    }
  };

  const handleResolveAll = async (resolution: ConflictResolution) => {
    for (const conflict of conflicts) {
      try {
        await ipc.resolveConflict(conflict.id, resolution);
      } catch (e) {
        console.error(`Failed to resolve conflict ${conflict.id}:`, e);
      }
    }
    await refresh();
  };

  const formatSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  if (conflicts.length === 0) {
    return (
      <div className="conflicts">
        <h2>Conflicts</h2>
        <p className="empty-message">No unresolved conflicts.</p>
      </div>
    );
  }

  return (
    <div className="conflicts">
      <div className="conflicts-header">
        <h2>Conflicts ({conflicts.length})</h2>
        <div className="batch-actions">
          <button
            onClick={() => handleResolveAll("keep_local")}
            className="btn btn-sm btn-secondary"
          >
            Keep all local
          </button>
          <button
            onClick={() => handleResolveAll("keep_remote")}
            className="btn btn-sm btn-secondary"
          >
            Keep all remote
          </button>
          <button
            onClick={() => handleResolveAll("keep_both")}
            className="btn btn-sm btn-secondary"
          >
            Keep all both
          </button>
        </div>
      </div>

      <div className="conflict-list">
        {conflicts.map((conflict) => (
          <div key={conflict.id} className="conflict-item">
            <div className="conflict-path">{conflict.path}</div>
            <div className="conflict-details">
              <div className="conflict-side">
                <strong>Local</strong>
                <span>{formatSize(conflict.local_size)}</span>
                <span>{new Date(conflict.local_mtime).toLocaleString()}</span>
              </div>
              <span className="conflict-vs">vs</span>
              <div className="conflict-side">
                <strong>Remote</strong>
                <span>{formatSize(conflict.remote_size)}</span>
                <span>{new Date(conflict.remote_mtime).toLocaleString()}</span>
              </div>
            </div>
            <div className="conflict-actions">
              <button
                onClick={() => handleResolve(conflict.id, "keep_local")}
                className="btn btn-sm"
              >
                Keep local
              </button>
              <button
                onClick={() => handleResolve(conflict.id, "keep_remote")}
                className="btn btn-sm"
              >
                Keep remote
              </button>
              <button
                onClick={() => handleResolve(conflict.id, "keep_both")}
                className="btn btn-sm"
              >
                Keep both
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
