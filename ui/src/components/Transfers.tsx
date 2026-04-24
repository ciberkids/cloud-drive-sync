import { useStatus, useSyncPairs } from "../lib/hooks";
import { providerColor, providerLabel } from "./AccountManager";

export function Transfers() {
  const status = useStatus(1000);
  const { pairs } = useSyncPairs();
  const transfers = status.live_transfers ?? [];

  const pairMap = Object.fromEntries(
    pairs.map((p) => [p.id, p])
  );

  return (
    <div className="transfers-page">
      <h2>Transfers</h2>

      {!status.daemon_reachable && (
        <p className="empty-message">Daemon is not running. Start the daemon to see active transfers.</p>
      )}

      {status.connected && transfers.length === 0 && (
        <div className="transfers-empty">
          <span className="transfers-empty-icon">{"\u2714"}</span>
          <p>No active transfers</p>
          <p className="transfers-empty-hint">
            Transfers will appear here when files are being uploaded or downloaded.
          </p>
        </div>
      )}

      {transfers.length > 0 && (
        <>
          <div className="transfers-summary">
            <span className="transfers-count">
              {transfers.length} active transfer{transfers.length !== 1 ? "s" : ""}
            </span>
          </div>

          <div className="transfers-list">
            {transfers.map((t) => {
              const pct = t.total > 0 ? Math.round((t.bytes / t.total) * 100) : 0;
              const fileName = t.path.split("/").pop() || t.path;
              const dirPart = t.path.includes("/")
                ? t.path.slice(0, t.path.lastIndexOf("/"))
                : null;
              const badgeLabel = directionLabel(t.direction);
              const hasProgress = t.direction === "upload" || t.direction === "download";
              const pair = pairMap[t.pair_id];
              const pColor = pair ? providerColor(pair.provider) : undefined;
              const pLabel = pair ? providerLabel(pair.provider) : undefined;
              const pEmail = pair?.account_id;
              const pFolder = pair?.local_path
                ? pair.local_path.split("/").filter(Boolean).pop()
                : undefined;

              return (
                <div
                  key={`${t.pair_id}-${t.path}`}
                  className="transfer-card"
                  style={pColor ? { borderLeft: `3px solid ${pColor}` } : undefined}
                >
                  <div className="transfer-card-header">
                    <span className={`transfer-badge transfer-badge-${t.direction}`}>
                      {badgeLabel}
                    </span>
                    {t.speed_formatted && (
                      <span className="transfer-card-speed">{t.speed_formatted}</span>
                    )}
                  </div>

                  <div className="transfer-card-file">
                    <span className="transfer-card-name" title={t.path}>{fileName}</span>
                    {dirPart && (
                      <span className="transfer-card-dir" title={dirPart}>{dirPart}/</span>
                    )}
                  </div>

                  {hasProgress && (
                    <div className="transfer-card-progress">
                      <div className="transfer-card-bar">
                        <div
                          className="transfer-card-bar-fill"
                          style={{ width: t.total > 0 ? `${pct}%` : undefined }}
                        />
                      </div>
                      <div className="transfer-card-stats">
                        {t.total > 0 ? (
                          <>
                            <span>{formatBytes(t.bytes)} / {formatBytes(t.total)}</span>
                            <span>{pct}%</span>
                          </>
                        ) : (
                          <span>{formatBytes(t.bytes)}</span>
                        )}
                      </div>
                    </div>
                  )}

                  {pair && (
                    <div className="transfer-card-account">
                      <span className="transfer-account-pill" style={{ background: pColor }}>
                        {pLabel}
                      </span>
                      {pEmail && <span className="transfer-account-email">{pEmail}</span>}
                      {pFolder && (
                        <span className="transfer-account-folder" title={pair.local_path}>
                          📁 {pFolder}
                        </span>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

function directionLabel(direction: string): string {
  switch (direction) {
    case "upload": return "\u2191 Upload";
    case "download": return "\u2193 Download";
    case "mkdir": return "\uD83D\uDCC1 Creating folder";
    case "delete_local": return "\uD83D\uDDD1 Deleting local";
    case "delete_remote": return "\uD83D\uDDD1 Deleting remote";
    default: return direction;
  }
}

function formatBytes(bytes: number): string {
  if (bytes >= 1_000_000_000) return `${(bytes / 1_000_000_000).toFixed(1)} GB`;
  if (bytes >= 1_000_000) return `${(bytes / 1_000_000).toFixed(1)} MB`;
  if (bytes >= 1_000) return `${(bytes / 1_000).toFixed(1)} KB`;
  return `${bytes} B`;
}
