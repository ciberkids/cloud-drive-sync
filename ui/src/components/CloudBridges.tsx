import { useState, useCallback, useEffect } from "react";
import { useSyncPairs, useStatus } from "../lib/hooks";
import { FolderPicker } from "./FolderPicker";
import { providerLabel, providerColor } from "./AccountManager";
import type { Account, SyncPair } from "../lib/types";
import * as ipc from "../lib/ipc";

// ── Bridge detection ────────────────────────────────────────────────────────

export interface CloudBridge {
  localPath: string;
  pairs: SyncPair[];
}

export function detectBridges(pairs: SyncPair[]): CloudBridge[] {
  const byLocal: Map<string, SyncPair[]> = new Map();
  for (const p of pairs) {
    if (!p.local_path) continue;
    const existing = byLocal.get(p.local_path) ?? [];
    existing.push(p);
    byLocal.set(p.local_path, existing);
  }
  const bridges: CloudBridge[] = [];
  for (const [localPath, ps] of byLocal) {
    if (ps.length >= 2) bridges.push({ localPath, pairs: ps });
  }
  return bridges;
}

// ── Inline remote folder picker (account-aware) ─────────────────────────────

interface CloudFolderPickerProps {
  accountId: string;
  value: string;
  folderName: string;
  onSelect: (folderId: string, folderName: string) => void;
}

function CloudFolderPicker({ accountId, value, folderName, onSelect }: CloudFolderPickerProps) {
  const [open, setOpen] = useState(false);
  const [folders, setFolders] = useState<Array<{ id: string; name: string }>>([]);
  const [crumbs, setCrumbs] = useState<Array<{ id: string; name: string }>>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const rootLabel = accountId.includes(":")
    ? accountId.split(":")[0].charAt(0).toUpperCase() + accountId.split(":")[0].slice(1)
    : "My Drive";

  const loadFolders = useCallback(async (parentId: string) => {
    setLoading(true);
    setError(null);
    try {
      const res = await ipc.listRemoteFolders(parentId, accountId || undefined);
      setFolders(res.error ? [] : res.folders);
      if (res.error) setError(res.error);
    } catch (e) {
      setError(String(e));
      setFolders([]);
    } finally {
      setLoading(false);
    }
  }, [accountId]);

  const handleOpen = async () => {
    const root = { id: "root", name: rootLabel };
    setCrumbs([root]);
    setOpen(true);
    await loadFolders("root");
  };

  const handleNavigate = async (folder: { id: string; name: string }) => {
    setCrumbs((prev) => [...prev, folder]);
    await loadFolders(folder.id);
  };

  const handleCrumb = async (i: number) => {
    setCrumbs((prev) => prev.slice(0, i + 1));
    await loadFolders(crumbs[i].id);
  };

  const handleSelectCurrent = () => {
    const cur = crumbs[crumbs.length - 1];
    onSelect(cur.id, cur.name);
    setOpen(false);
  };

  if (!accountId) {
    return <div className="bridge-picker-hint">Select an account first</div>;
  }

  return (
    <div className="bridge-folder-picker">
      <div className="bridge-picker-row">
        <span className="bridge-picker-value">
          {value ? (folderName || value) : <span className="text-secondary">Not selected</span>}
        </span>
        <button
          type="button"
          className="btn btn-sm btn-secondary"
          onClick={open ? () => setOpen(false) : handleOpen}
        >
          {open ? "Cancel" : "Browse"}
        </button>
      </div>
      {open && (
        <div className="bridge-picker-panel">
          <div className="bridge-picker-crumbs">
            {crumbs.map((c, i) => (
              <span key={c.id + i}>
                {i > 0 && <span className="remote-picker-sep">/</span>}
                <button className="remote-picker-crumb" onClick={() => handleCrumb(i)} type="button">
                  {c.name}
                </button>
              </span>
            ))}
          </div>
          <div className="bridge-picker-list">
            {loading && <div className="remote-picker-loading">Loading...</div>}
            {error && <div className="remote-picker-error">{error}</div>}
            {!loading && !error && folders.length === 0 && (
              <div className="remote-picker-empty">No subfolders</div>
            )}
            {!loading && folders.map((f) => (
              <button key={f.id} className="remote-picker-folder" type="button" onClick={() => handleNavigate(f)}>
                <span className="remote-picker-folder-icon">📁</span>
                <span className="remote-picker-folder-name">{f.name}</span>
              </button>
            ))}
          </div>
          <div className="bridge-picker-footer">
            <button type="button" className="btn btn-primary btn-sm" onClick={handleSelectCurrent}>
              Select current folder
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Bridge side selector ────────────────────────────────────────────────────

interface BridgeSide {
  accountId: string;
  provider: string;
  remoteFolderId: string;
  remoteFolderName: string;
}

const EMPTY_SIDE: BridgeSide = { accountId: "", provider: "gdrive", remoteFolderId: "", remoteFolderName: "" };

interface BridgeSidePickerProps {
  label: string;
  side: BridgeSide;
  accounts: Account[];
  onChange: (side: BridgeSide) => void;
}

function BridgeSidePicker({ label, side, accounts, onChange }: BridgeSidePickerProps) {
  const handleAccountChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const [provider, ...rest] = e.target.value.split(":");
    const accountId = e.target.value;
    onChange({ ...side, accountId, provider: provider || "gdrive", remoteFolderId: "", remoteFolderName: "" });
    void rest;
  };

  return (
    <div className="bridge-side-picker">
      <div className="bridge-side-label">{label}</div>
      <div className="field">
        <label className="field-label">Account</label>
        <select
          className="select"
          value={side.accountId}
          onChange={handleAccountChange}
        >
          <option value="">— choose account —</option>
          {accounts.map((a) => {
            const key = `${a.provider || "gdrive"}:${a.email}`;
            return (
              <option key={key} value={key}>
                {providerLabel(a.provider)} — {a.display_name || a.email}
              </option>
            );
          })}
        </select>
      </div>
      {side.accountId && (
        <div className="field">
          <label className="field-label">Remote folder</label>
          <CloudFolderPicker
            accountId={side.accountId}
            value={side.remoteFolderId}
            folderName={side.remoteFolderName}
            onSelect={(id, name) => onChange({ ...side, remoteFolderId: id, remoteFolderName: name })}
          />
        </div>
      )}
    </div>
  );
}

// ── Bridge card ─────────────────────────────────────────────────────────────

interface BridgeCardProps {
  bridge: CloudBridge;
  pairCountMap: Record<string, number>;
  onRemove: (pairIds: string[]) => void;
  onPairStrategyChange: (pairId: string, strategy: string) => void;
}

function BridgeCard({ bridge, pairCountMap, onRemove, onPairStrategyChange }: BridgeCardProps) {
  const [confirming, setConfirming] = useState(false);

  const localFolder = bridge.localPath.split("/").filter(Boolean).pop() || bridge.localPath;
  const modeLabel = (p: SyncPair) => {
    if (p.sync_mode === "upload_only") return "upload";
    if (p.sync_mode === "download_only") return "download";
    return "two-way";
  };
  const modeArrow = (p: SyncPair) => {
    if (p.sync_mode === "upload_only") return "↑";
    if (p.sync_mode === "download_only") return "↓";
    return "⇄";
  };
  const truncate = (s: string, n: number) =>
    s.length > n ? s.slice(0, n) + "…" : s;

  // Detect strategy conflict (both sides have same directional strategy)
  const directional = bridge.pairs
    .map((p) => p.conflict_strategy || "")
    .filter((s) => s === "local_wins" || s === "remote_wins");
  const hasConflict = directional.length >= 2 && new Set(directional).size === 1;

  return (
    <div className="bridge-card">
      <div className="bridge-flow">
        {bridge.pairs.map((p, i) => {
          const color = providerColor(p.provider);
          const label = providerLabel(p.provider);
          const folderDisplay = p.remote_folder_id === "root"
            ? "Root"
            : truncate(p.remote_folder_id, 14);
          const accountDisplay = truncate(p.account_id || "", 22);
          return (
            <div key={p.id} className="bridge-flow-segment">
              {i > 0 && <div className="bridge-flow-divider" />}
              <div className="bridge-cloud-node">
                <div className="bridge-cloud-pill" style={{ background: color }}>{label}</div>
                <div className="bridge-cloud-account" title={p.account_id || ""}>{accountDisplay}</div>
                <div className="bridge-cloud-folder" title={p.remote_folder_id}>{folderDisplay}</div>
                <div className="bridge-cloud-mode">{modeArrow(p)} {modeLabel(p)}</div>
                {pairCountMap[p.id] !== undefined && (
                  <div className="bridge-cloud-count">
                    <span className="bridge-cloud-count-value">{pairCountMap[p.id].toLocaleString()}</span>
                    <span className="bridge-cloud-count-label"> files synced</span>
                  </div>
                )}
                <div className="field" style={{ marginTop: 6 }}>
                  <select
                    className="select"
                    value={p.conflict_strategy ?? ""}
                    onChange={(e) => onPairStrategyChange(p.id, e.target.value)}
                    title="Conflict strategy for this side"
                    style={{ fontSize: 11 }}
                  >
                    <option value="">Strategy: default</option>
                    <option value="keep_both">Keep both</option>
                    <option value="local_wins">Local wins ⚠</option>
                    <option value="remote_wins">Remote wins ⚠</option>
                    {p.sync_mode === "two_way" && (
                      <>
                        <option value="newest_wins">Newest wins</option>
                        <option value="ask_user">Ask me</option>
                      </>
                    )}
                  </select>
                </div>
              </div>
            </div>
          );
        })}
      </div>
      {hasConflict && (
        <div className="bridge-strategy-error">
          &#9888; Both sides have "{directional[0]}" — they will conflict with each other.
          Set one side to "local_wins" and the other to "remote_wins".
        </div>
      )}
      <div className="bridge-local-row">
        <span className="bridge-local-icon">&#128193;</span>
        <span className="bridge-local-path" title={bridge.localPath}>{localFolder}</span>
        <span className="bridge-local-full">{bridge.localPath}</span>
      </div>
      <div className="bridge-card-actions">
        {confirming ? (
          <>
            <span className="bridge-confirm-text">Remove both pairs?</span>
            <button
              className="btn btn-danger btn-sm"
              onClick={() => { onRemove(bridge.pairs.map((p) => p.id)); setConfirming(false); }}
            >
              Yes, remove
            </button>
            <button className="btn btn-sm" onClick={() => setConfirming(false)}>Cancel</button>
          </>
        ) : (
          <button className="btn btn-danger btn-sm" onClick={() => setConfirming(true)}>
            Remove Bridge
          </button>
        )}
      </div>
    </div>
  );
}

// ── Creation wizard ──────────────────────────────────────────────────────────

type BridgeMode = "mirror" | "relay";

interface BridgeDraft {
  mode: BridgeMode;
  sideA: BridgeSide;
  sideB: BridgeSide;
  localPath: string;
}

function validationError(draft: BridgeDraft, existingLocalPaths: Set<string>): string | null {
  if (!draft.sideA.accountId) return "Choose an account for Side A";
  if (!draft.sideA.remoteFolderId) return "Choose a remote folder for Side A";
  if (!draft.sideB.accountId) return "Choose an account for Side B";
  if (!draft.sideB.remoteFolderId) return "Choose a remote folder for Side B";
  if (!draft.localPath) return "Choose a local relay folder";
  if (
    draft.sideA.accountId === draft.sideB.accountId &&
    draft.sideA.remoteFolderId === draft.sideB.remoteFolderId
  ) {
    return "Side A and Side B cannot point to the same account and folder — this would create a sync loop";
  }
  if (existingLocalPaths.has(draft.localPath)) {
    return "A bridge already uses this local folder. Each bridge needs a unique local relay folder.";
  }
  return null;
}

interface CreateBridgeFormProps {
  accounts: Account[];
  existingLocalPaths: Set<string>;
  onCreated: () => void;
  onCancel: () => void;
}

function CreateBridgeForm({ accounts, existingLocalPaths, onCreated, onCancel }: CreateBridgeFormProps) {
  const [draft, setDraft] = useState<BridgeDraft>({
    mode: "mirror",
    sideA: { ...EMPTY_SIDE },
    sideB: { ...EMPTY_SIDE },
    localPath: "",
  });
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const validErr = validationError(draft, existingLocalPaths);

  const handleCreate = async () => {
    const err = validationError(draft, existingLocalPaths);
    if (err) { setError(err); return; }
    setCreating(true);
    setError(null);
    try {
      const [modeA, modeB] = draft.mode === "relay"
        ? ["download_only", "upload_only"]
        : ["two_way", "two_way"];

      const pA = await ipc.addSyncPair(
        draft.localPath,
        draft.sideA.remoteFolderId,
        undefined,
        draft.sideA.accountId,
        draft.sideA.provider,
        modeA,
      );

      if (draft.mode === "relay") {
        await ipc.setPairConflictStrategy(pA.id, "remote_wins");
      }

      const pB = await ipc.addSyncPair(
        draft.localPath,
        draft.sideB.remoteFolderId,
        undefined,
        draft.sideB.accountId,
        draft.sideB.provider,
        modeB,
      );

      if (draft.mode === "relay") {
        await ipc.setPairConflictStrategy(pB.id, "local_wins");
      }

      onCreated();
    } catch (e) {
      setError(String(e));
      setCreating(false);
    }
  };

  return (
    <div className="bridge-create-form">
      <h3>New Cloud Bridge</h3>

      <div className="bridge-mode-selector">
        <button
          type="button"
          className={`bridge-mode-btn ${draft.mode === "mirror" ? "active" : ""}`}
          onClick={() => setDraft((d) => ({ ...d, mode: "mirror" }))}
        >
          <span className="bridge-mode-icon">⇄</span>
          <span className="bridge-mode-name">Mirror</span>
          <span className="bridge-mode-desc">Changes in either cloud appear in both</span>
        </button>
        <button
          type="button"
          className={`bridge-mode-btn ${draft.mode === "relay" ? "active" : ""}`}
          onClick={() => setDraft((d) => ({ ...d, mode: "relay" }))}
        >
          <span className="bridge-mode-icon">→</span>
          <span className="bridge-mode-name">Relay</span>
          <span className="bridge-mode-desc">One-way: data flows from Side A to Side B only</span>
        </button>
      </div>

      <div className="bridge-sides">
        <BridgeSidePicker
          label={draft.mode === "relay" ? "Source (Side A)" : "Side A"}
          side={draft.sideA}
          accounts={accounts}
          onChange={(s) => setDraft((d) => ({ ...d, sideA: s }))}
        />
        <div className="bridge-sides-arrow">
          {draft.mode === "relay" ? "→" : "⇄"}
        </div>
        <BridgeSidePicker
          label={draft.mode === "relay" ? "Destination (Side B)" : "Side B"}
          side={draft.sideB}
          accounts={accounts}
          onChange={(s) => setDraft((d) => ({ ...d, sideB: s }))}
        />
      </div>

      <div className="field">
        <label className="field-label">Local relay folder</label>
        <p className="settings-hint">
          Files are temporarily stored here while being transferred between clouds.
        </p>
        <FolderPicker
          value={draft.localPath}
          onChange={(p) => setDraft((d) => ({ ...d, localPath: p }))}
          label="Local relay folder"
        />
      </div>

      {(error || (validErr && creating)) && (
        <div className="bridge-error">{error || validErr}</div>
      )}

      <div className="bridge-form-actions">
        <button
          type="button"
          className="btn btn-primary"
          onClick={handleCreate}
          disabled={creating || !!validErr}
          title={validErr ?? undefined}
        >
          {creating ? "Creating…" : "Create Bridge"}
        </button>
        <button type="button" className="btn btn-secondary" onClick={onCancel} disabled={creating}>
          Cancel
        </button>
      </div>
    </div>
  );
}

// ── Main page ────────────────────────────────────────────────────────────────

export function CloudBridges() {
  const { pairs, refresh, remove } = useSyncPairs();
  const status = useStatus(5000);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [creating, setCreating] = useState(false);

  // pair_counts use "pair_0","pair_1"... but SyncPair.id is "0","1"... — map both
  const pairCountMap: Record<string, number> = {};
  for (const pc of status.pair_counts ?? []) {
    pairCountMap[pc.pair_id] = pc.files_synced;
    pairCountMap[pc.pair_id.replace(/^pair_/, "")] = pc.files_synced;
  }

  useEffect(() => {
    ipc.listAccounts().then(setAccounts).catch(() => {});
  }, []);

  const bridges = detectBridges(pairs);
  const existingLocalPaths = new Set(bridges.map((b) => b.localPath));

  const handleRemoveBridge = async (pairIds: string[]) => {
    for (const id of pairIds) {
      await remove(id);
    }
  };

  const handleCreated = () => {
    setCreating(false);
    refresh();
  };

  const handlePairStrategyChange = async (pairId: string, strategy: string) => {
    try {
      await ipc.setPairConflictStrategy(pairId, strategy);
      // Auto-set the opposite on bridge siblings
      if (strategy === "local_wins" || strategy === "remote_wins") {
        const opposite = strategy === "local_wins" ? "remote_wins" : "local_wins";
        const changedPair = pairs.find((p) => p.id === pairId);
        if (changedPair) {
          const siblings = pairs.filter(
            (p) => p.id !== pairId && p.local_path === changedPair.local_path
          );
          for (const sibling of siblings) {
            await ipc.setPairConflictStrategy(sibling.id, opposite);
          }
        }
      }
      refresh();
    } catch (e) {
      console.error("Failed to change bridge strategy:", e);
    }
  };

  return (
    <div className="bridges-page">
      <div className="bridges-header">
        <div>
          <h2>Cloud Bridges</h2>
          <p className="settings-hint">
            A Cloud Bridge links two cloud accounts through a local relay folder,
            keeping them in sync automatically.
          </p>
        </div>
        <div className="bridges-header-actions">
          <a
            className="btn btn-sm btn-secondary help-btn"
            href="https://github.com/ciberkids/cloud-drive-sync/wiki/Cloud-Bridge"
            target="_blank"
            rel="noopener noreferrer"
            title="How Cloud Bridges work"
          >
            ? Help
          </a>
          {!creating && (
            <button className="btn btn-primary" onClick={() => setCreating(true)}>
              New Bridge
            </button>
          )}
        </div>
      </div>

      {creating && (
        <CreateBridgeForm
          accounts={accounts}
          existingLocalPaths={existingLocalPaths}
          onCreated={handleCreated}
          onCancel={() => setCreating(false)}
        />
      )}

      {bridges.length === 0 && !creating && (
        <div className="bridges-empty">
          <div className="bridges-empty-icon">⛅</div>
          <p>No Cloud Bridges yet.</p>
          <p className="settings-hint">
            Create a bridge to automatically sync files between two cloud providers.
          </p>
        </div>
      )}

      {bridges.length > 0 && (
        <div className="bridge-list">
          {bridges.map((b) => (
            <BridgeCard
              key={b.localPath}
              bridge={b}
              pairCountMap={pairCountMap}
              onRemove={handleRemoveBridge}
              onPairStrategyChange={handlePairStrategyChange}
            />
          ))}
        </div>
      )}
    </div>
  );
}
