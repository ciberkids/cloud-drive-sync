// Prevents additional console window on Windows in release
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod commands;
mod ipc_bridge;
mod tray;

use commands::BridgeState;
use ipc_bridge::DaemonBridge;
use std::sync::Arc;
use tauri::{image::Image, Emitter, Manager};
use tauri_plugin_notification::NotificationExt;
use tokio::sync::{mpsc, Mutex};

fn main() {
    env_logger::init();

    let (notification_tx, mut notification_rx) = mpsc::channel::<(String, serde_json::Value)>(256);
    let bridge = DaemonBridge::new(notification_tx);

    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            // Focus existing window when a second instance is attempted
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(BridgeState(Arc::new(Mutex::new(bridge))))
        .invoke_handler(tauri::generate_handler![
            commands::get_status,
            commands::get_sync_pairs,
            commands::add_sync_pair,
            commands::remove_sync_pair,
            commands::set_conflict_strategy,
            commands::resolve_conflict,
            commands::force_sync,
            commands::pause_sync,
            commands::resume_sync,
            commands::get_activity_log,
            commands::get_conflicts,
            commands::start_auth,
            commands::logout,
            commands::connect_daemon,
            commands::set_sync_mode,
            commands::set_ignore_hidden,
            commands::set_ignore_patterns,
            commands::list_remote_folders,
            commands::add_account,
            commands::remove_account,
            commands::list_accounts,
            commands::set_notification_prefs,
            commands::get_notification_prefs,
            commands::set_bandwidth_limits,
            commands::get_bandwidth_limits,
            commands::set_sync_rules,
            commands::get_sync_rules,
            commands::set_proxy,
            commands::get_proxy,
            commands::set_account_max_transfers,
        ])
        .setup(|app| {
            let handle = app.handle().clone();

            // Set up system tray
            tray::setup_tray(&handle)?;

            // Spawn daemon connection task
            let bridge_state = app.state::<BridgeState>();
            let bridge_mutex = Arc::clone(&bridge_state.0);
            let connect_handle = handle.clone();
            tauri::async_runtime::spawn(async move {
                // Try to connect with retries
                let mut attempts = 0;
                loop {
                    {
                        let mut bridge = bridge_mutex.lock().await;
                        match bridge.connect().await {
                            Ok(()) => {
                                log::info!("Connected to daemon");
                                tray::update_tray_status(&connect_handle, "Connected");
                                let _ = connect_handle.emit("daemon-connected", ());
                                break;
                            }
                            Err(e) => {
                                log::warn!("Failed to connect to daemon (attempt {}): {}", attempts + 1, e);
                            }
                        }
                    }
                    attempts += 1;
                    if attempts >= 10 {
                        log::error!("Could not connect to daemon after {} attempts", attempts);
                        tray::update_tray_status(&connect_handle, "Daemon offline");
                        let _ = connect_handle.emit("daemon-offline", ());
                        break;
                    }
                    tokio::time::sleep(std::time::Duration::from_secs(3)).await;
                }
            });

            // Forward daemon notifications to frontend events
            let event_handle = handle.clone();
            tauri::async_runtime::spawn(async move {
                while let Some((method, params)) = notification_rx.recv().await {
                    let event_name = format!("daemon:{}", method);
                    let _ = event_handle.emit(&event_name, &params);

                    // Update tray based on notifications
                    match method.as_str() {
                        "sync_progress" => {
                            tray::update_tray_status(&event_handle, "Syncing");
                        }
                        "sync_complete" => {
                            if let Ok(perm) = event_handle.notification().permission_state() {
                                if perm == tauri_plugin_notification::PermissionState::Granted {
                                    let title = "Sync Complete";
                                    let body = params.get("detail").and_then(|d| d.as_str()).unwrap_or("Sync finished");
                                    let _ = event_handle.notification().builder().title(title).body(body).show();
                                }
                            }
                        }
                        "status_changed" => {
                            if let Some(status) = params.get("status").and_then(|s| s.as_str()) {
                                let display = match status {
                                    "idle" => "Connected",
                                    "syncing" | "in_progress" => "Syncing",
                                    "error" => "Error",
                                    "paused" => "Paused",
                                    _ => status,
                                };
                                tray::update_tray_status(&event_handle, display);
                            }
                        }
                        "conflict_detected" => {
                            tray::update_tray_status(&event_handle, "Conflict detected");
                            if let Ok(perm) = event_handle.notification().permission_state() {
                                if perm == tauri_plugin_notification::PermissionState::Granted {
                                    let path = params.get("path").and_then(|p| p.as_str()).unwrap_or("Unknown file");
                                    let body = format!("Conflict detected: {}", path);
                                    let _ = event_handle.notification().builder().title("Sync Conflict").body(&body).show();
                                }
                            }
                        }
                        "error" => {
                            tray::update_tray_status(&event_handle, "Error");
                            if let Ok(perm) = event_handle.notification().permission_state() {
                                if perm == tauri_plugin_notification::PermissionState::Granted {
                                    let detail = params.get("detail").and_then(|d| d.as_str()).unwrap_or("A sync error occurred");
                                    let _ = event_handle.notification().builder().title("Sync Error").body(detail).show();
                                }
                            }
                        }
                        _ => {}
                    }
                }
            });

            // Set window icon explicitly (needed on Linux/Wayland)
            let window = app.get_webview_window("main").unwrap();
            let win_icon = Image::from_bytes(include_bytes!("../icons/128x128.png"))
                .expect("Failed to load window icon");
            let _ = window.set_icon(win_icon);

            // Hide window on close instead of exiting (tray app)
            let close_handle = window.clone();
            window.on_window_event(move |event| {
                if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                    api.prevent_close();
                    let _ = close_handle.hide();
                }
            });

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error running tauri application");
}
