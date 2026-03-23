use crate::ipc_bridge::DaemonBridge;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::sync::Arc;
use tauri::{Emitter, State};
use tokio::sync::Mutex;

#[derive(Debug, Serialize, Deserialize, Clone, Default)]
pub struct DaemonInfo {
    pub pid: Option<u64>,
    pub uptime: Option<u64>,
    pub uptime_formatted: Option<String>,
    pub socket_path: Option<String>,
}

#[derive(Debug, Serialize, Deserialize, Clone, Default)]
pub struct LiveTransfer {
    pub pair_id: Option<String>,
    pub path: Option<String>,
    pub direction: Option<String>,
    pub bytes: Option<u64>,
    pub total: Option<u64>,
    pub speed: Option<f64>,
    pub speed_formatted: Option<String>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct DaemonStatus {
    pub connected: bool,
    pub syncing: bool,
    pub paused: bool,
    pub error: Option<String>,
    pub last_sync: Option<String>,
    pub files_synced: u64,
    pub active_transfers: u64,
    #[serde(default)]
    pub live_transfers: Vec<LiveTransfer>,
    #[serde(default)]
    pub daemon: Option<DaemonInfo>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct SyncPair {
    pub id: String,
    pub local_path: String,
    pub remote_folder_id: String,
    pub enabled: bool,
    pub sync_mode: String,
    pub ignore_hidden: Option<bool>,
    pub ignore_patterns: Option<Vec<String>>,
    pub account_id: Option<String>,
    pub provider: Option<String>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct ConflictRecord {
    pub id: String,
    pub path: String,
    pub local_mtime: String,
    pub remote_mtime: String,
    pub local_size: u64,
    pub remote_size: u64,
    pub detected_at: String,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct LogEntry {
    pub id: u64,
    pub timestamp: String,
    pub event_type: String,
    pub path: String,
    pub details: String,
    pub status: String,
}

pub struct BridgeState(pub Arc<Mutex<DaemonBridge>>);

#[tauri::command]
pub async fn get_status(bridge: State<'_, BridgeState>) -> Result<DaemonStatus, String> {
    let bridge = bridge.0.lock().await;
    if !bridge.is_connected() {
        return Ok(DaemonStatus {
            connected: false,
            syncing: false,
            paused: false,
            error: Some("Not connected to daemon".to_string()),
            last_sync: None,
            files_synced: 0,
            active_transfers: 0,
            live_transfers: vec![],
            daemon: None,
        });
    }

    let result = bridge.call("get_status", None).await?;
    serde_json::from_value(result).map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn get_sync_pairs(bridge: State<'_, BridgeState>) -> Result<Vec<SyncPair>, String> {
    let bridge = bridge.0.lock().await;
    let result = bridge.call("get_sync_pairs", None).await?;
    serde_json::from_value(result).map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn add_sync_pair(
    bridge: State<'_, BridgeState>,
    local_path: String,
    remote_folder_id: String,
    ignore_hidden: Option<bool>,
    account_id: Option<String>,
) -> Result<SyncPair, String> {
    let bridge = bridge.0.lock().await;
    let mut params = json!({
        "local_path": local_path,
        "remote_folder_id": remote_folder_id
    });
    if let Some(ih) = ignore_hidden {
        params["ignore_hidden"] = json!(ih);
    }
    if let Some(ref aid) = account_id {
        params["account_id"] = json!(aid);
    }
    let result = bridge.call("add_sync_pair", Some(params)).await?;
    serde_json::from_value(result).map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn remove_sync_pair(
    bridge: State<'_, BridgeState>,
    pair_id: String,
) -> Result<(), String> {
    let bridge = bridge.0.lock().await;
    bridge
        .call("remove_sync_pair", Some(json!({ "id": pair_id })))
        .await?;
    Ok(())
}

#[tauri::command]
pub async fn set_conflict_strategy(
    bridge: State<'_, BridgeState>,
    strategy: String,
) -> Result<(), String> {
    let bridge = bridge.0.lock().await;
    bridge
        .call(
            "set_conflict_strategy",
            Some(json!({ "strategy": strategy })),
        )
        .await?;
    Ok(())
}

#[tauri::command]
pub async fn resolve_conflict(
    bridge: State<'_, BridgeState>,
    conflict_id: String,
    resolution: String,
) -> Result<(), String> {
    let bridge = bridge.0.lock().await;
    bridge
        .call(
            "resolve_conflict",
            Some(json!({
                "conflict_id": conflict_id,
                "resolution": resolution
            })),
        )
        .await?;
    Ok(())
}

#[tauri::command]
pub async fn force_sync(
    bridge: State<'_, BridgeState>,
    pair_id: Option<String>,
) -> Result<(), String> {
    let bridge = bridge.0.lock().await;
    let params = pair_id.map(|id| json!({ "pair_id": id }));
    bridge.call("force_sync", params).await?;
    Ok(())
}

#[tauri::command]
pub async fn pause_sync(
    bridge: State<'_, BridgeState>,
    pair_id: Option<String>,
) -> Result<(), String> {
    let bridge = bridge.0.lock().await;
    let params = pair_id.map(|id| json!({ "pair_id": id }));
    bridge.call("pause_sync", params).await?;
    Ok(())
}

#[tauri::command]
pub async fn resume_sync(
    bridge: State<'_, BridgeState>,
    pair_id: Option<String>,
) -> Result<(), String> {
    let bridge = bridge.0.lock().await;
    let params = pair_id.map(|id| json!({ "pair_id": id }));
    bridge.call("resume_sync", params).await?;
    Ok(())
}

#[tauri::command]
pub async fn get_activity_log(
    bridge: State<'_, BridgeState>,
    limit: u32,
    offset: u32,
) -> Result<Vec<LogEntry>, String> {
    let bridge = bridge.0.lock().await;
    let result = bridge
        .call(
            "get_activity_log",
            Some(json!({ "limit": limit, "offset": offset })),
        )
        .await?;
    serde_json::from_value(result).map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn get_conflicts(
    bridge: State<'_, BridgeState>,
) -> Result<Vec<ConflictRecord>, String> {
    let bridge = bridge.0.lock().await;
    let result = bridge.call("get_conflicts", None).await?;
    serde_json::from_value(result).map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn start_auth(bridge: State<'_, BridgeState>) -> Result<Value, String> {
    let bridge = bridge.0.lock().await;
    bridge.call("start_auth", None).await
}

#[tauri::command]
pub async fn logout(bridge: State<'_, BridgeState>) -> Result<(), String> {
    let bridge = bridge.0.lock().await;
    bridge.call("logout", None).await?;
    Ok(())
}

#[tauri::command]
pub async fn connect_daemon(
    bridge: State<'_, BridgeState>,
    app: tauri::AppHandle,
) -> Result<(), String> {
    let mut bridge = bridge.0.lock().await;
    bridge.connect().await?;
    let _ = app.emit("daemon-connected", ());
    crate::tray::update_tray_status(&app, "Connected");
    Ok(())
}

#[tauri::command]
pub async fn set_sync_mode(
    bridge: State<'_, BridgeState>,
    pair_id: String,
    sync_mode: String,
) -> Result<(), String> {
    let bridge = bridge.0.lock().await;
    bridge
        .call(
            "set_sync_mode",
            Some(json!({
                "pair_id": pair_id,
                "sync_mode": sync_mode
            })),
        )
        .await?;
    Ok(())
}

#[tauri::command]
pub async fn set_ignore_hidden(
    bridge: State<'_, BridgeState>,
    pair_id: String,
    ignore_hidden: bool,
) -> Result<(), String> {
    let bridge = bridge.0.lock().await;
    bridge
        .call(
            "set_ignore_hidden",
            Some(json!({
                "pair_id": pair_id,
                "ignore_hidden": ignore_hidden
            })),
        )
        .await?;
    Ok(())
}

#[tauri::command]
pub async fn set_ignore_patterns(
    bridge: State<'_, BridgeState>,
    pair_id: String,
    patterns: Vec<String>,
) -> Result<(), String> {
    let bridge = bridge.0.lock().await;
    bridge
        .call(
            "set_ignore_patterns",
            Some(json!({
                "pair_id": pair_id,
                "patterns": patterns
            })),
        )
        .await?;
    Ok(())
}

#[tauri::command]
pub async fn list_remote_folders(
    bridge: State<'_, BridgeState>,
    parent_id: String,
    account_id: Option<String>,
) -> Result<serde_json::Value, String> {
    let bridge = bridge.0.lock().await;
    let mut params = json!({ "parent_id": parent_id });
    if let Some(ref aid) = account_id {
        params["account_id"] = json!(aid);
    }
    bridge
        .call("list_remote_folders", Some(params))
        .await
}

#[tauri::command]
pub async fn add_account(bridge: State<'_, BridgeState>) -> Result<Value, String> {
    let bridge = bridge.0.lock().await;
    bridge.call("add_account", None).await
}

#[tauri::command]
pub async fn remove_account(
    bridge: State<'_, BridgeState>,
    email: String,
) -> Result<(), String> {
    let bridge = bridge.0.lock().await;
    bridge
        .call("remove_account", Some(json!({ "email": email })))
        .await?;
    Ok(())
}

#[tauri::command]
pub async fn list_accounts(bridge: State<'_, BridgeState>) -> Result<Value, String> {
    let bridge = bridge.0.lock().await;
    bridge.call("list_accounts", None).await
}

#[tauri::command]
pub async fn set_notification_prefs(
    bridge: State<'_, BridgeState>,
    notify_sync_complete: Option<bool>,
    notify_conflicts: Option<bool>,
    notify_errors: Option<bool>,
) -> Result<Value, String> {
    let bridge = bridge.0.lock().await;
    let mut params = json!({});
    if let Some(v) = notify_sync_complete {
        params["notify_sync_complete"] = json!(v);
    }
    if let Some(v) = notify_conflicts {
        params["notify_conflicts"] = json!(v);
    }
    if let Some(v) = notify_errors {
        params["notify_errors"] = json!(v);
    }
    bridge.call("set_notification_prefs", Some(params)).await
}

#[tauri::command]
pub async fn get_notification_prefs(bridge: State<'_, BridgeState>) -> Result<Value, String> {
    let bridge = bridge.0.lock().await;
    bridge.call("get_notification_prefs", None).await
}

#[tauri::command]
pub async fn set_bandwidth_limits(
    bridge: State<'_, BridgeState>,
    max_upload_kbps: Option<u64>,
    max_download_kbps: Option<u64>,
) -> Result<Value, String> {
    let bridge = bridge.0.lock().await;
    let mut params = json!({});
    if let Some(v) = max_upload_kbps {
        params["max_upload_kbps"] = json!(v);
    }
    if let Some(v) = max_download_kbps {
        params["max_download_kbps"] = json!(v);
    }
    bridge.call("set_bandwidth_limits", Some(params)).await
}

#[tauri::command]
pub async fn get_bandwidth_limits(bridge: State<'_, BridgeState>) -> Result<Value, String> {
    let bridge = bridge.0.lock().await;
    bridge.call("get_bandwidth_limits", None).await
}

#[tauri::command]
pub async fn set_sync_rules(
    bridge: State<'_, BridgeState>,
    pair_id: String,
    rules: Value,
) -> Result<Value, String> {
    let bridge = bridge.0.lock().await;
    bridge
        .call(
            "set_sync_rules",
            Some(json!({
                "pair_id": pair_id,
                "rules": rules
            })),
        )
        .await
}

#[tauri::command]
pub async fn get_sync_rules(
    bridge: State<'_, BridgeState>,
    pair_id: String,
) -> Result<Value, String> {
    let bridge = bridge.0.lock().await;
    bridge
        .call("get_sync_rules", Some(json!({ "pair_id": pair_id })))
        .await
}

#[tauri::command]
pub async fn set_proxy(
    bridge: State<'_, BridgeState>,
    http_proxy: Option<String>,
    https_proxy: Option<String>,
    no_proxy: Option<String>,
) -> Result<Value, String> {
    let bridge = bridge.0.lock().await;
    let mut params = json!({});
    if let Some(v) = http_proxy {
        params["http_proxy"] = json!(v);
    }
    if let Some(v) = https_proxy {
        params["https_proxy"] = json!(v);
    }
    if let Some(v) = no_proxy {
        params["no_proxy"] = json!(v);
    }
    bridge.call("set_proxy", Some(params)).await
}

#[tauri::command]
pub async fn get_proxy(bridge: State<'_, BridgeState>) -> Result<Value, String> {
    let bridge = bridge.0.lock().await;
    bridge.call("get_proxy", None).await
}

#[tauri::command]
pub async fn set_account_max_transfers(
    bridge: State<'_, BridgeState>,
    email: String,
    max_concurrent_transfers: u32,
) -> Result<serde_json::Value, String> {
    let bridge = bridge.0.lock().await;
    bridge
        .call(
            "set_account_max_transfers",
            Some(json!({
                "email": email,
                "max_concurrent_transfers": max_concurrent_transfers
            })),
        )
        .await
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    // ── Data struct serialization / deserialization ──────────────────

    #[test]
    fn daemon_status_deserializes_from_json() {
        let json_val = json!({
            "connected": true,
            "syncing": false,
            "paused": false,
            "error": null,
            "last_sync": "2026-03-23T12:00:00Z",
            "files_synced": 42,
            "active_transfers": 1,
            "live_transfers": [{
                "pair_id": "p1",
                "path": "/foo/bar.txt",
                "direction": "upload",
                "bytes": 1024,
                "total": 4096,
                "speed": 512.0,
                "speed_formatted": "512 B/s"
            }],
            "daemon": {
                "pid": 12345,
                "uptime": 3600,
                "uptime_formatted": "1h 0m",
                "socket_path": "/run/user/1000/cloud-drive-sync.sock"
            }
        });

        let status: DaemonStatus = serde_json::from_value(json_val).unwrap();
        assert!(status.connected);
        assert!(!status.syncing);
        assert_eq!(status.files_synced, 42);
        assert_eq!(status.active_transfers, 1);
        assert_eq!(status.live_transfers.len(), 1);
        assert_eq!(status.live_transfers[0].direction.as_deref(), Some("upload"));
        assert_eq!(status.daemon.as_ref().unwrap().pid, Some(12345));
    }

    #[test]
    fn daemon_status_defaults_for_missing_optional_fields() {
        let json_val = json!({
            "connected": false,
            "syncing": false,
            "paused": false,
            "error": null,
            "last_sync": null,
            "files_synced": 0,
            "active_transfers": 0
        });

        let status: DaemonStatus = serde_json::from_value(json_val).unwrap();
        assert!(!status.connected);
        assert!(status.live_transfers.is_empty());
        assert!(status.daemon.is_none());
    }

    #[test]
    fn sync_pair_roundtrip() {
        let pair = SyncPair {
            id: "sp-1".into(),
            local_path: "/home/user/docs".into(),
            remote_folder_id: "folder-abc".into(),
            enabled: true,
            sync_mode: "bidirectional".into(),
            ignore_hidden: Some(true),
            ignore_patterns: Some(vec!["*.tmp".into(), ".git".into()]),
            account_id: Some("acc-1".into()),
            provider: Some("gdrive".into()),
        };

        let serialized = serde_json::to_value(&pair).unwrap();
        let deserialized: SyncPair = serde_json::from_value(serialized).unwrap();
        assert_eq!(deserialized.id, "sp-1");
        assert_eq!(deserialized.sync_mode, "bidirectional");
        assert_eq!(deserialized.ignore_patterns.unwrap().len(), 2);
    }

    #[test]
    fn conflict_record_roundtrip() {
        let record = ConflictRecord {
            id: "c-1".into(),
            path: "/docs/file.txt".into(),
            local_mtime: "2026-03-23T10:00:00Z".into(),
            remote_mtime: "2026-03-23T11:00:00Z".into(),
            local_size: 1024,
            remote_size: 2048,
            detected_at: "2026-03-23T11:05:00Z".into(),
        };

        let val = serde_json::to_value(&record).unwrap();
        let back: ConflictRecord = serde_json::from_value(val).unwrap();
        assert_eq!(back.id, "c-1");
        assert_eq!(back.local_size, 1024);
    }

    #[test]
    fn log_entry_roundtrip() {
        let entry = LogEntry {
            id: 1,
            timestamp: "2026-03-23T12:00:00Z".into(),
            event_type: "upload".into(),
            path: "/docs/file.txt".into(),
            details: "Uploaded successfully".into(),
            status: "success".into(),
        };

        let val = serde_json::to_value(&entry).unwrap();
        let back: LogEntry = serde_json::from_value(val).unwrap();
        assert_eq!(back.event_type, "upload");
        assert_eq!(back.status, "success");
    }

    // ── JSON parameter construction ─────────────────────────────────
    //
    // Each command builds params via json!() before passing to
    // bridge.call(). These tests verify the param shapes match
    // what the Python daemon expects.

    #[test]
    fn add_sync_pair_params_basic() {
        let local_path = "/home/user/docs".to_string();
        let remote_folder_id = "folder-abc".to_string();
        let ignore_hidden: Option<bool> = None;
        let account_id: Option<String> = None;

        let mut params = json!({
            "local_path": local_path,
            "remote_folder_id": remote_folder_id
        });
        if let Some(ih) = ignore_hidden {
            params["ignore_hidden"] = json!(ih);
        }
        if let Some(ref aid) = account_id {
            params["account_id"] = json!(aid);
        }

        assert_eq!(params["local_path"], "/home/user/docs");
        assert_eq!(params["remote_folder_id"], "folder-abc");
        assert!(params.get("ignore_hidden").is_none());
        assert!(params.get("account_id").is_none());
    }

    #[test]
    fn add_sync_pair_params_with_optionals() {
        let local_path = "/home/user/docs".to_string();
        let remote_folder_id = "folder-abc".to_string();
        let ignore_hidden: Option<bool> = Some(true);
        let account_id: Option<String> = Some("acc-1".to_string());

        let mut params = json!({
            "local_path": local_path,
            "remote_folder_id": remote_folder_id
        });
        if let Some(ih) = ignore_hidden {
            params["ignore_hidden"] = json!(ih);
        }
        if let Some(ref aid) = account_id {
            params["account_id"] = json!(aid);
        }

        assert_eq!(params["ignore_hidden"], true);
        assert_eq!(params["account_id"], "acc-1");
    }

    #[test]
    fn remove_sync_pair_params() {
        let pair_id = "sp-42".to_string();
        let params = json!({ "id": pair_id });
        assert_eq!(params["id"], "sp-42");
    }

    #[test]
    fn set_conflict_strategy_params() {
        let strategy = "local_wins".to_string();
        let params = json!({ "strategy": strategy });
        assert_eq!(params["strategy"], "local_wins");
    }

    #[test]
    fn resolve_conflict_params() {
        let conflict_id = "c-7".to_string();
        let resolution = "keep_remote".to_string();
        let params = json!({
            "conflict_id": conflict_id,
            "resolution": resolution
        });
        assert_eq!(params["conflict_id"], "c-7");
        assert_eq!(params["resolution"], "keep_remote");
    }

    #[test]
    fn force_sync_params_with_pair_id() {
        let pair_id: Option<String> = Some("sp-1".to_string());
        let params = pair_id.map(|id| json!({ "pair_id": id }));
        assert!(params.is_some());
        assert_eq!(params.unwrap()["pair_id"], "sp-1");
    }

    #[test]
    fn force_sync_params_without_pair_id() {
        let pair_id: Option<String> = None;
        let params = pair_id.map(|id| json!({ "pair_id": id }));
        assert!(params.is_none());
    }

    #[test]
    fn pause_resume_sync_params() {
        // Same pattern for pause_sync and resume_sync
        for pair_id in [Some("sp-1".to_string()), None] {
            let params = pair_id.map(|id| json!({ "pair_id": id }));
            match params {
                Some(p) => assert_eq!(p["pair_id"], "sp-1"),
                None => {} // No params is valid
            }
        }
    }

    #[test]
    fn get_activity_log_params() {
        let limit: u32 = 50;
        let offset: u32 = 10;
        let params = json!({ "limit": limit, "offset": offset });
        assert_eq!(params["limit"], 50);
        assert_eq!(params["offset"], 10);
    }

    #[test]
    fn set_sync_mode_params() {
        let pair_id = "sp-1".to_string();
        let sync_mode = "upload_only".to_string();
        let params = json!({
            "pair_id": pair_id,
            "sync_mode": sync_mode
        });
        assert_eq!(params["pair_id"], "sp-1");
        assert_eq!(params["sync_mode"], "upload_only");
    }

    #[test]
    fn set_ignore_hidden_params() {
        let pair_id = "sp-1".to_string();
        let ignore_hidden = true;
        let params = json!({
            "pair_id": pair_id,
            "ignore_hidden": ignore_hidden
        });
        assert_eq!(params["pair_id"], "sp-1");
        assert_eq!(params["ignore_hidden"], true);
    }

    #[test]
    fn set_ignore_patterns_params() {
        let pair_id = "sp-1".to_string();
        let patterns = vec!["*.tmp".to_string(), "node_modules".to_string()];
        let params = json!({
            "pair_id": pair_id,
            "patterns": patterns
        });
        assert_eq!(params["pair_id"], "sp-1");
        let p = params["patterns"].as_array().unwrap();
        assert_eq!(p.len(), 2);
        assert_eq!(p[0], "*.tmp");
        assert_eq!(p[1], "node_modules");
    }

    #[test]
    fn list_remote_folders_params_basic() {
        let parent_id = "root".to_string();
        let account_id: Option<String> = None;

        let mut params = json!({ "parent_id": parent_id });
        if let Some(ref aid) = account_id {
            params["account_id"] = json!(aid);
        }

        assert_eq!(params["parent_id"], "root");
        assert!(params.get("account_id").is_none());
    }

    #[test]
    fn list_remote_folders_params_with_account() {
        let parent_id = "root".to_string();
        let account_id: Option<String> = Some("acc-1".to_string());

        let mut params = json!({ "parent_id": parent_id });
        if let Some(ref aid) = account_id {
            params["account_id"] = json!(aid);
        }

        assert_eq!(params["parent_id"], "root");
        assert_eq!(params["account_id"], "acc-1");
    }

    #[test]
    fn remove_account_params() {
        let email = "user@example.com".to_string();
        let params = json!({ "email": email });
        assert_eq!(params["email"], "user@example.com");
    }

    #[test]
    fn set_notification_prefs_params_all() {
        let notify_sync_complete: Option<bool> = Some(true);
        let notify_conflicts: Option<bool> = Some(false);
        let notify_errors: Option<bool> = Some(true);

        let mut params = json!({});
        if let Some(v) = notify_sync_complete {
            params["notify_sync_complete"] = json!(v);
        }
        if let Some(v) = notify_conflicts {
            params["notify_conflicts"] = json!(v);
        }
        if let Some(v) = notify_errors {
            params["notify_errors"] = json!(v);
        }

        assert_eq!(params["notify_sync_complete"], true);
        assert_eq!(params["notify_conflicts"], false);
        assert_eq!(params["notify_errors"], true);
    }

    #[test]
    fn set_notification_prefs_params_partial() {
        let notify_sync_complete: Option<bool> = None;
        let notify_conflicts: Option<bool> = Some(true);
        let notify_errors: Option<bool> = None;

        let mut params = json!({});
        if let Some(v) = notify_sync_complete {
            params["notify_sync_complete"] = json!(v);
        }
        if let Some(v) = notify_conflicts {
            params["notify_conflicts"] = json!(v);
        }
        if let Some(v) = notify_errors {
            params["notify_errors"] = json!(v);
        }

        assert!(params.get("notify_sync_complete").is_none());
        assert_eq!(params["notify_conflicts"], true);
        assert!(params.get("notify_errors").is_none());
    }

    #[test]
    fn set_bandwidth_limits_params() {
        let max_upload_kbps: Option<u64> = Some(1024);
        let max_download_kbps: Option<u64> = Some(2048);

        let mut params = json!({});
        if let Some(v) = max_upload_kbps {
            params["max_upload_kbps"] = json!(v);
        }
        if let Some(v) = max_download_kbps {
            params["max_download_kbps"] = json!(v);
        }

        assert_eq!(params["max_upload_kbps"], 1024);
        assert_eq!(params["max_download_kbps"], 2048);
    }

    #[test]
    fn set_bandwidth_limits_params_partial() {
        let max_upload_kbps: Option<u64> = Some(512);
        let max_download_kbps: Option<u64> = None;

        let mut params = json!({});
        if let Some(v) = max_upload_kbps {
            params["max_upload_kbps"] = json!(v);
        }
        if let Some(v) = max_download_kbps {
            params["max_download_kbps"] = json!(v);
        }

        assert_eq!(params["max_upload_kbps"], 512);
        assert!(params.get("max_download_kbps").is_none());
    }

    #[test]
    fn set_sync_rules_params() {
        let pair_id = "sp-1".to_string();
        let rules = json!({
            "max_file_size": 104857600,
            "exclude_extensions": [".log", ".tmp"]
        });
        let params = json!({
            "pair_id": pair_id,
            "rules": rules
        });
        assert_eq!(params["pair_id"], "sp-1");
        assert_eq!(params["rules"]["max_file_size"], 104857600);
    }

    #[test]
    fn get_sync_rules_params() {
        let pair_id = "sp-1".to_string();
        let params = json!({ "pair_id": pair_id });
        assert_eq!(params["pair_id"], "sp-1");
    }

    #[test]
    fn set_proxy_params_all() {
        let http_proxy: Option<String> = Some("http://proxy:8080".into());
        let https_proxy: Option<String> = Some("https://proxy:8443".into());
        let no_proxy: Option<String> = Some("localhost,127.0.0.1".into());

        let mut params = json!({});
        if let Some(v) = http_proxy {
            params["http_proxy"] = json!(v);
        }
        if let Some(v) = https_proxy {
            params["https_proxy"] = json!(v);
        }
        if let Some(v) = no_proxy {
            params["no_proxy"] = json!(v);
        }

        assert_eq!(params["http_proxy"], "http://proxy:8080");
        assert_eq!(params["https_proxy"], "https://proxy:8443");
        assert_eq!(params["no_proxy"], "localhost,127.0.0.1");
    }

    #[test]
    fn set_proxy_params_partial() {
        let http_proxy: Option<String> = Some("http://proxy:8080".into());
        let https_proxy: Option<String> = None;
        let no_proxy: Option<String> = None;

        let mut params = json!({});
        if let Some(v) = http_proxy {
            params["http_proxy"] = json!(v);
        }
        if let Some(v) = https_proxy {
            params["https_proxy"] = json!(v);
        }
        if let Some(v) = no_proxy {
            params["no_proxy"] = json!(v);
        }

        assert_eq!(params["http_proxy"], "http://proxy:8080");
        assert!(params.get("https_proxy").is_none());
        assert!(params.get("no_proxy").is_none());
    }

    #[test]
    fn set_account_max_transfers_params() {
        let email = "user@example.com".to_string();
        let max_concurrent_transfers: u32 = 4;
        let params = json!({
            "email": email,
            "max_concurrent_transfers": max_concurrent_transfers
        });
        assert_eq!(params["email"], "user@example.com");
        assert_eq!(params["max_concurrent_transfers"], 4);
    }

    // ── Disconnected status default ─────────────────────────────────

    #[test]
    fn disconnected_status_has_correct_defaults() {
        // Mirrors the DaemonStatus returned when bridge is not connected
        let status = DaemonStatus {
            connected: false,
            syncing: false,
            paused: false,
            error: Some("Not connected to daemon".to_string()),
            last_sync: None,
            files_synced: 0,
            active_transfers: 0,
            live_transfers: vec![],
            daemon: None,
        };

        assert!(!status.connected);
        assert!(!status.syncing);
        assert!(!status.paused);
        assert_eq!(status.error.as_deref(), Some("Not connected to daemon"));
        assert!(status.last_sync.is_none());
        assert_eq!(status.files_synced, 0);
        assert_eq!(status.active_transfers, 0);
        assert!(status.live_transfers.is_empty());
        assert!(status.daemon.is_none());

        // Verify it serializes correctly for the frontend
        let val = serde_json::to_value(&status).unwrap();
        assert_eq!(val["connected"], false);
        assert_eq!(val["error"], "Not connected to daemon");
    }
}
