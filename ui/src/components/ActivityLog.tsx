import { useState, useMemo } from "react";
import { useActivityLog, useSyncPairs } from "../lib/hooks";
import { providerColor, providerLabel } from "./AccountManager";
import type { LogEntry } from "../lib/types";

const EVENT_ICONS: Record<string, string> = {
  upload: "↑",
  download: "↓",
  delete: "✖",
  conflict: "⚠",
  error: "✘",
  auth: "⚙",
  sync: "↻",
};

type FilterType = "all" | LogEntry["event_type"];

export function ActivityLog() {
  const { entries, loading, loadMore } = useActivityLog(50);
  const { pairs } = useSyncPairs();
  const [filter, setFilter] = useState<FilterType>("all");
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());

  const toggleExpanded = (id: number) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  // Build a lookup: pair_id -> { account, provider }
  const pairAccountMap = useMemo(() => {
    const map: Record<string, { email: string; provider: string }> = {};
    for (let i = 0; i < pairs.length; i++) {
      const pair = pairs[i];
      const pairId = `pair_${i}`;
      const provider = pair.provider || "gdrive";
      const email = pair.account_id || "";
      map[pairId] = { email, provider };
      // Also map by the pair's string id
      map[pair.id] = { email, provider };
    }
    return map;
  }, [pairs]);

  const filtered =
    filter === "all"
      ? entries
      : entries.filter((e) => e.event_type === filter);

  return (
    <div className="activity-log">
      <div className="activity-header">
        <h2>Activity</h2>
        <div className="activity-filters">
          {(["all", "upload", "download", "delete", "sync", "conflict", "error", "auth"] as const).map(
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
        {filtered.map((entry) => {
          const acctInfo = entry.pair_id ? pairAccountMap[entry.pair_id] : null;
          const color = acctInfo ? providerColor(acctInfo.provider) : undefined;
          const label = acctInfo ? providerLabel(acctInfo.provider) : undefined;
          const isSystem = entry.pair_id === "_system";
          const isExpanded = expandedIds.has(entry.id);

          return (
            <div
              key={entry.id}
              className={`log-item log-${entry.event_type}${isExpanded ? " log-item-expanded" : ""}`}
              onClick={() => toggleExpanded(entry.id)}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  toggleExpanded(entry.id);
                }
              }}
            >
              <span className="log-icon">
                {EVENT_ICONS[entry.event_type] || "•"}
              </span>
              <span className="log-expand-indicator" aria-hidden="true">
                {isExpanded ? "▼" : "▶"}
              </span>
              <div className="log-content">
                <span className="log-path">{entry.path || entry.details}</span>
                {entry.path && <span className="log-details">{entry.details}</span>}
              </div>
              <div className="log-meta">
                {acctInfo && !isSystem && (
                  <span className="log-account" title={`${label} — ${acctInfo.email}`}>
                    <span
                      className="log-provider-dot"
                      style={{ background: color }}
                    />
                    {acctInfo.email.split("@")[0]}
                  </span>
                )}
                <span className="log-time">
                  {new Date(entry.timestamp).toLocaleTimeString()}
                </span>
                <span className={`log-status log-status-${entry.status}`}>
                  {entry.status}
                </span>
              </div>
            </div>
          );
        })}

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
