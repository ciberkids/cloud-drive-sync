import { useStatus, useSyncPairs } from "../lib/hooks";
import { providerColor, providerLabel } from "./AccountManager";

export function Transfers() {
  const status = useStatus(1000);
  const { pairs } = useSyncPairs();
  const transfers = status.live_transfers ?? [];

  // pairs.id is a numeric string ("0","1"...) but live_transfer.pair_id is
  // "pair_0","pair_1"... — build a map with both key formats for robust lookup
  const pairMap = Object.fromEntries([
    ...pairs.map((p) => [p.id, p]),
    ...pairs.map((p, i) => [`pair_${i}`, p]),
  ]);

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
              const pct = t.total > 0 ? Math.min(100, Math.round((t.bytes / t.total) * 100)) : 0;
              const indeterminate = t.total === 0 || (t.total > 0 && t.bytes === 0);
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
              const pMode = pair ? syncModeLabel(pair.sync_mode) : undefined;

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
                    {t.speed_formatted ? (
                      <span className="transfer-card-speed">{t.speed_formatted}</span>
                    ) : hasProgress ? (
                      <span className="transfer-card-speed" style={{ color: "var(--text-secondary)", fontWeight: 400, fontSize: "12px" }}>
                        {t.bytes > 0 ? "finishing…" : "starting…"}
                      </span>
                    ) : null}
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
                          className={`transfer-card-bar-fill${indeterminate ? " indeterminate" : ""}`}
                          style={!indeterminate ? { width: `${pct}%` } : undefined}
                        />
                      </div>
                      <div className="transfer-card-stats">
                        {t.total > 0 && t.bytes > 0 ? (
                          <>
                            <span>{formatBytes(t.bytes)} / {formatBytes(t.total)}</span>
                            <span>{pct}%</span>
                          </>
                        ) : t.total > 0 ? (
                          <span>— / {formatBytes(t.total)}</span>
                        ) : t.bytes > 0 ? (
                          <span>{formatBytes(t.bytes)}</span>
                        ) : (
                          <span>—</span>
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
                      {pMode && <span className="transfer-account-mode">{pMode}</span>}
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

function syncModeLabel(mode?: string): string {
  switch (mode) {
    case "two_way": return "⇄ Two-way";
    case "upload_only": return "↑ Upload only";
    case "download_only": return "↓ Download only";
    default: return "";
  }
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
