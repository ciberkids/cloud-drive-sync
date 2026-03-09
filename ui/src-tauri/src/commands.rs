use crate::ipc_bridge::DaemonBridge;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::sync::Arc;
use tauri::{Emitter, State};
use tokio::sync::Mutex;

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct DaemonStatus {
    pub connected: bool,
    pub syncing: bool,
    pub paused: bool,
    pub error: Option<String>,
    pub last_sync: Option<String>,
    pub files_synced: u64,
    pub active_transfers: u64,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct SyncPair {
    pub id: String,
    pub local_path: String,
    pub remote_folder_id: String,
    pub enabled: bool,
    pub sync_mode: String,
    pub ignore_hidden: Option<bool>,
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
) -> Result<SyncPair, String> {
    let bridge = bridge.0.lock().await;
    let mut params = json!({
        "local_path": local_path,
        "remote_folder_id": remote_folder_id
    });
    if let Some(ih) = ignore_hidden {
        params["ignore_hidden"] = json!(ih);
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
pub async fn list_remote_folders(
    bridge: State<'_, BridgeState>,
    parent_id: String,
) -> Result<serde_json::Value, String> {
    let bridge = bridge.0.lock().await;
    bridge
        .call(
            "list_remote_folders",
            Some(serde_json::json!({ "parent_id": parent_id })),
        )
        .await
}
