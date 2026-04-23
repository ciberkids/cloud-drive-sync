/**
 * HTTP-based IPC client for the web UI served by the daemon's HTTP server.
 * Replaces Tauri's invoke() with fetch() calls to /api/* endpoints.
 * Used when the React app is served via the daemon's HTTP server (headless mode).
 */

import type {
  Account,
  DaemonStatus,
  SyncPair,
  ConflictRecord,
  LogEntry,
  ConflictResolution,
} from "./types";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`/api/${path}`);
  if (!res.ok) throw new Error(`API error: ${res.statusText}`);
  return res.json();
}

async function post<T>(path: string, body?: Record<string, unknown>): Promise<T> {
  const res = await fetch(`/api/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`API error: ${res.statusText}`);
  return res.json();
}

async function put<T>(path: string, body: Record<string, unknown>): Promise<T> {
  const res = await fetch(`/api/${path}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`API error: ${res.statusText}`);
  return res.json();
}

async function del<T>(path: string): Promise<T> {
  const res = await fetch(`/api/${path}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`API error: ${res.statusText}`);
  return res.json();
}

export async function getStatus(): Promise<DaemonStatus> {
  return get<DaemonStatus>("status");
}

export async function getSyncPairs(): Promise<SyncPair[]> {
  return get<SyncPair[]>("pairs");
}

export async function addSyncPair(
  localPath: string,
  remoteFolderId: string,
  ignoreHidden?: boolean,
  accountId?: string,
  provider?: string,
  syncMode?: string
): Promise<SyncPair> {
  return post<SyncPair>("pairs", {
    local_path: localPath,
    remote_folder_id: remoteFolderId,
    ignore_hidden: ignoreHidden,
    account_id: accountId,
    provider: provider,
    sync_mode: syncMode,
  });
}

export async function removeSyncPair(pairId: string): Promise<void> {
  await del(`pairs/${pairId}`);
}

export async function setConflictStrategy(strategy: string): Promise<void> {
  await put("settings/conflict-strategy", { strategy });
}

export async function resolveConflict(
  conflictId: string,
  resolution: ConflictResolution
): Promise<void> {
  await post(`conflicts/${conflictId}/resolve`, { resolution });
}

export async function forceSync(pairId?: string): Promise<void> {
  await post("sync", pairId ? { pair_id: pairId } : {});
}

export async function pauseSync(pairId?: string): Promise<void> {
  await post("sync/pause", pairId ? { pair_id: pairId } : {});
}

export async function resumeSync(pairId?: string): Promise<void> {
  await post("sync/resume", pairId ? { pair_id: pairId } : {});
}

export async function getActivityLog(
  limit: number,
  offset: number
): Promise<LogEntry[]> {
  return get<LogEntry[]>(`activity?limit=${limit}&offset=${offset}`);
}

export async function getConflicts(): Promise<ConflictRecord[]> {
  return get<ConflictRecord[]>("conflicts");
}

export async function startAuth(): Promise<unknown> {
  return post("accounts", { provider: "gdrive", headless: true });
}

export async function logout(): Promise<void> {
  // No direct equivalent — use removeAccount
}

export async function connectDaemon(): Promise<void> {
  // No-op for HTTP — always connected if server is reachable
}

export async function setSyncMode(
  pairId: string,
  syncMode: string
): Promise<void> {
  await put(`pairs/${pairId}/mode`, { sync_mode: syncMode });
}

export async function setIgnoreHidden(
  pairId: string,
  ignoreHidden: boolean
): Promise<void> {
  await put(`pairs/${pairId}/ignore-hidden`, { ignore_hidden: ignoreHidden });
}

export async function setIgnorePatterns(
  pairId: string,
  patterns: string[]
): Promise<void> {
  await put(`pairs/${pairId}/ignore-patterns`, { patterns });
}

export async function addAccount(provider?: string, extra?: Record<string, string>): Promise<unknown> {
  return post("accounts", { provider: provider || "gdrive", headless: true, ...extra });
}

export async function exchangeAuthCode(provider: string, code: string): Promise<unknown> {
  return post("accounts/auth-code", { provider, code });
}

export async function removeAccount(email: string, provider?: string): Promise<void> {
  const qs = provider ? `?provider=${encodeURIComponent(provider)}` : "";
  await del(`accounts/${encodeURIComponent(email)}${qs}`);
}

export async function listAccounts(): Promise<Account[]> {
  return get<Account[]>("accounts");
}

export async function setNotificationPrefs(prefs: {
  notify_sync_complete?: boolean;
  notify_conflicts?: boolean;
  notify_errors?: boolean;
}): Promise<{
  notify_sync_complete: boolean;
  notify_conflicts: boolean;
  notify_errors: boolean;
}> {
  return put("settings/notifications", prefs);
}

export async function getNotificationPrefs(): Promise<{
  notify_sync_complete: boolean;
  notify_conflicts: boolean;
  notify_errors: boolean;
}> {
  return get("settings/notifications");
}

export async function setBandwidthLimits(params: {
  max_upload_kbps?: number;
  max_download_kbps?: number;
}): Promise<{
  max_upload_kbps: number;
  max_download_kbps: number;
}> {
  return put("settings/bandwidth", {
    max_upload_kbps: params.max_upload_kbps,
    max_download_kbps: params.max_download_kbps,
  });
}

export async function getBandwidthLimits(): Promise<{
  max_upload_kbps: number;
  max_download_kbps: number;
}> {
  return get("settings/bandwidth");
}

export async function setSyncRules(
  pairId: string,
  rules: {
    max_file_size_mb?: number;
    include_regex?: string[];
    exclude_regex?: string[];
    min_date?: string;
  }
): Promise<unknown> {
  return put(`pairs/${pairId}/rules`, rules);
}

export async function getSyncRules(
  pairId: string
): Promise<{
  max_file_size_mb: number;
  include_regex: string[];
  exclude_regex: string[];
  min_date: string;
}> {
  return get(`pairs/${pairId}/rules`);
}

export async function setProxy(prefs: {
  http_proxy?: string;
  https_proxy?: string;
  no_proxy?: string;
}): Promise<{
  http_proxy: string;
  https_proxy: string;
  no_proxy: string;
}> {
  return put("settings/proxy", prefs);
}

export async function getProxy(): Promise<{
  http_proxy: string;
  https_proxy: string;
  no_proxy: string;
}> {
  return get("settings/proxy");
}

export async function listRemoteFolders(
  parentId: string,
  accountId?: string
): Promise<{
  folders: Array<{ id: string; name: string }>;
  shared_drives?: Array<{ id: string; name: string }>;
  parent_id: string;
  error?: string;
}> {
  let url = `remote-folders?parent_id=${encodeURIComponent(parentId)}`;
  if (accountId) url += `&account_id=${encodeURIComponent(accountId)}`;
  return get(url);
}

export async function createRemoteFolder(
  parentId: string,
  name: string,
  accountId?: string
): Promise<{ id: string; name: string; error?: string }> {
  return post("remote-folders", { parent_id: parentId, name, account_id: accountId });
}

export async function listLocalDirs(
  path?: string
): Promise<{
  path: string;
  parent: string | null;
  dirs: Array<{ name: string; path: string }>;
  error?: string;
}> {
  const url = path ? `local-dirs?path=${encodeURIComponent(path)}` : "local-dirs";
  return get(url);
}

export async function mkdirLocal(
  path: string
): Promise<{ ok: boolean; path?: string; error?: string }> {
  return post("local-dirs", { path });
}

export async function setAccountMaxTransfers(
  email: string,
  maxConcurrentTransfers: number
): Promise<unknown> {
  return put(`accounts/${encodeURIComponent(email)}/max-transfers`, {
    max_concurrent_transfers: maxConcurrentTransfers,
  });
}
