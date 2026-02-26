import { useState } from "react";
import { useActivityLog } from "../lib/hooks";
import type { LogEntry } from "../lib/types";

const EVENT_ICONS: Record<string, string> = {
  upload: "\u2191",
  download: "\u2193",
  delete: "\u2716",
  conflict: "\u26A0",
  error: "\u2718",
};

type FilterType = "all" | LogEntry["event_type"];

export function ActivityLog() {
  const { entries, loading, loadMore } = useActivityLog(50);
  const [filter, setFilter] = useState<FilterType>("all");

  const filtered =
    filter === "all"
      ? entries
      : entries.filter((e) => e.event_type === filter);

  return (
    <div className="activity-log">
      <div className="activity-header">
        <h2>Activity</h2>
        <div className="activity-filters">
          {(["all", "upload", "download", "delete", "conflict", "error"] as const).map(
            (type) => (
              <button
                key={type}
                onClick={() => setFilter(type)}
                className={`filter-btn ${filter === type ? "active" : ""}`}
              >
                {type === "all" ? "All" : type.charAt(0).toUpperCase() + type.slice(1)}
              </button>
            )
          )}
        </div>
      </div>

      <div className="log-list">
        {filtered.map((entry) => (
          <div
            key={entry.id}
            className={`log-item log-${entry.event_type}`}
          >
            <span className="log-icon">
              {EVENT_ICONS[entry.event_type] || "\u2022"}
            </span>
            <div className="log-content">
              <span className="log-path">{entry.path}</span>
              <span className="log-details">{entry.details}</span>
            </div>
            <div className="log-meta">
              <span className="log-time">
                {new Date(entry.timestamp).toLocaleTimeString()}
              </span>
              <span className={`log-status log-status-${entry.status}`}>
                {entry.status}
              </span>
            </div>
          </div>
        ))}

        {filtered.length === 0 && (
          <p className="empty-message">No activity to show.</p>
        )}
      </div>

      {entries.length > 0 && (
        <button
          onClick={loadMore}
          disabled={loading}
          className="btn btn-secondary load-more"
        >
          {loading ? "Loading..." : "Load more"}
        </button>
      )}
    </div>
  );
}
