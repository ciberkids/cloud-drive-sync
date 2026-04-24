import { useState, useMemo, useCallback } from "react";
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

  const [copiedId, setCopiedId] = useState<number | null>(null);

  const toggleExpanded = (id: number) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const copyEntry = useCallback((e: React.MouseEvent, entry: LogEntry, acctInfo: { email: string; provider: string } | null) => {
    e.stopPropagation();
    const lines = [
      `[${new Date(entry.timestamp).toLocaleString()}] ${entry.event_type.toUpperCase()}`,
      entry.path ? `Path:    ${entry.path}` : null,
      entry.details ? `Details: ${entry.details}` : null,
      acctInfo ? `Account: ${acctInfo.email} (${providerLabel(acctInfo.provider)})` : null,
      `Status:  ${entry.status}`,
    ].filter(Boolean).join("\n");
    navigator.clipboard.writeText(lines).then(() => {
      setCopiedId(entry.id);
      setTimeout(() => setCopiedId(null), 1500);
    });
  }, []);

  // Build a lookup: pair_id -> { account, provider, localPath }
  const pairAccountMap = useMemo(() => {
    const map: Record<string, { email: string; provider: string; localPath: string }> = {};
    for (let i = 0; i < pairs.length; i++) {
      const pair = pairs[i];
      const pairId = `pair_${i}`;
      const provider = pair.provider || "gdrive";
      const email = pair.account_id || "";
      const localPath = pair.local_path || "";
      map[pairId] = { email, provider, localPath };
      // Also map by the pair's string id
      map[pair.id] = { email, provider, localPath };
    }
    return map;
  }, [pairs]);

  const filtered =
    filter === "all"
      ? entries
      : filter === "error"
        ? entries.filter((e) => e.status === "error" || e.event_type === "error")
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

          const folderName = acctInfo?.localPath
            ? acctInfo.localPath.split("/").filter(Boolean).pop()
            : undefined;

          return (
            <div
              key={entry.id}
              className={`log-item log-${entry.event_type}${isExpanded ? " log-item-expanded" : ""}`}
              style={color && !isSystem ? { borderLeft: `3px solid ${color}` } : undefined}
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
                  <div className="log-account" title={`${label} — ${acctInfo.email}`}>
                    <span className="log-provider-pill" style={{ background: color }}>
                      {label}
                    </span>
                    <span className="log-account-text">
                      {acctInfo.email.split("@")[0]}
                      {folderName && <> · {folderName}</>}
                    </span>
                  </div>
                )}
                <span className="log-time">
                  {new Date(entry.timestamp).toLocaleTimeString()}
                </span>
                <span className={`log-status log-status-${entry.status}`}>
                  {entry.status}
                </span>
              </div>
              {isExpanded && (
                <div className="log-expanded" onClick={(e) => e.stopPropagation()}>
                  <div className="log-expanded-rows">
                    {entry.path && <div className="log-expanded-row"><span className="log-expanded-label">Path</span><span className="log-expanded-value">{entry.path}</span></div>}
                    {entry.details && <div className="log-expanded-row"><span className="log-expanded-label">Details</span><span className="log-expanded-value">{entry.details}</span></div>}
                    {acctInfo && !isSystem && <div className="log-expanded-row"><span className="log-expanded-label">Account</span><span className="log-expanded-value">{acctInfo.email} ({providerLabel(acctInfo.provider)})</span></div>}
                    <div className="log-expanded-row"><span className="log-expanded-label">Time</span><span className="log-expanded-value">{new Date(entry.timestamp).toLocaleString()}</span></div>
                  </div>
                  <button
                    className="btn btn-sm log-copy-btn"
                    onClick={(e) => copyEntry(e, entry, acctInfo)}
                    type="button"
                  >
                    {copiedId === entry.id ? "Copied!" : "Copy to clipboard"}
                  </button>
                </div>
              )}
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
