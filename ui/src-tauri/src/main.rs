// Prevents additional console window on Windows in release
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod commands;
mod ipc_bridge;
mod tray;

use commands::BridgeState;
use ipc_bridge::DaemonBridge;
use std::sync::Arc;
use tauri::{image::Image, AppHandle, Emitter, Manager};
use tauri_plugin_notification::NotificationExt;
use tauri_plugin_shell::ShellExt;
use tokio::sync::{mpsc, Mutex};

fn main() {
    env_logger::init();

    let tray_only = std::env::args().any(|a| a == "--tray");

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
        .setup(move |app| {
            let handle = app.handle().clone();

            // Set up system tray
            tray::setup_tray(&handle)?;

            // In --tray mode: hide window, don't launch sidecar (daemon managed by systemd)
            if tray_only {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.hide();
                }
                log::info!("Started in tray-only mode (window hidden)");
            }

            // Spawn daemon connection task with health checks
            let bridge_state = app.state::<BridgeState>();
            let bridge_mutex = Arc::clone(&bridge_state.0);
            let connect_handle = handle.clone();
            let launch_sidecar = !tray_only;
            tauri::async_runtime::spawn(async move {
                // Step 1: Check for stale PID / zombie daemon
                tray::update_tray_info(&connect_handle, "Cloud Drive Sync — Starting...");
                let _ = connect_handle.emit("daemon-status-msg", "Checking daemon status...");
                check_daemon_health(&connect_handle);

                // Step 2: Try connecting
                let mut attempts = 0;
                let mut sidecar_launched = false;
                loop {
                    {
                        let mut bridge = bridge_mutex.lock().await;
                        match bridge.connect().await {
                            Ok(()) => {
                                log::info!("Connected to daemon");
                                tray::update_tray_status(&connect_handle, "Connected");
                                tray::update_tray_info(&connect_handle, "Cloud Drive Sync — Connected");
                                let _ = connect_handle.emit("daemon-status-msg", "Connected to daemon");
                                let _ = connect_handle.emit("daemon-connected", ());
                                break;
                            }
                            Err(e) => {
                                log::warn!("Failed to connect to daemon (attempt {}): {}", attempts + 1, e);
                                let msg = format!("Connecting to daemon (attempt {})...", attempts + 1);
                                tray::update_tray_info(
                                    &connect_handle,
                                    &format!("Cloud Drive Sync — {}", msg),
                                );
                                let _ = connect_handle.emit("daemon-status-msg", &msg);
                            }
                        }
                    }
                    attempts += 1;
                    // Only launch daemon in non-tray mode (standalone app)
                    if launch_sidecar && attempts == 2 && !sidecar_launched {
                        log::info!("Daemon not reachable, attempting to start daemon");
                        tray::update_tray_info(&connect_handle, "Cloud Drive Sync — Starting daemon...");
                        let _ = connect_handle.emit("daemon-status-msg", "Starting daemon...");

                        // Try sidecar first (Tauri bundled binary), then fall back to system PATH
                        let sidecar_ok = match connect_handle.shell().sidecar("bin/cloud-drive-sync-daemon") {
                            Ok(cmd) => match cmd.args(["start", "--foreground"]).spawn() {
                                Ok((_rx, _child)) => {
                                    log::info!("Daemon launched via sidecar");
                                    true
                                }
                                Err(e) => {
                                    log::warn!("Sidecar failed: {}, trying system PATH", e);
                                    false
                                }
                            },
                            Err(e) => {
                                log::warn!("Sidecar not available: {}, trying system PATH", e);
                                false
                            }
                        };

                        if !sidecar_ok {
                            // Fall back to system PATH (installed via RPM/DEB/Flatpak)
                            match std::process::Command::new("cloud-drive-sync-daemon")
                                .args(["start", "--foreground"])
                                .stdin(std::process::Stdio::null())
                                .stdout(std::process::Stdio::null())
                                .stderr(std::process::Stdio::null())
                                .spawn()
                            {
                                Ok(_child) => {
                                    log::info!("Daemon launched from system PATH");
                                    sidecar_launched = true;
                                }
                                Err(e) => {
                                    log::error!("Failed to start daemon: {}", e);
                                    tray::update_tray_info(
                                        &connect_handle,
                                        "Cloud Drive Sync — Failed to start daemon",
                                    );
                                }
                            }
                        } else {
                            sidecar_launched = true;
                        }
                    }
                    if attempts >= 10 {
                        log::error!("Could not connect to daemon after {} attempts", attempts);
                        tray::update_tray_status(&connect_handle, "Daemon offline");
                        tray::update_tray_info(&connect_handle, "Cloud Drive Sync — Daemon not running");
                        let _ = connect_handle.emit("daemon-status-msg", "Could not connect to daemon after multiple attempts");
                        let _ = connect_handle.emit("daemon-offline", ());

                        // Show notification about failed connection
                        if let Ok(perm) = connect_handle.notification().permission_state() {
                            if perm == tauri_plugin_notification::PermissionState::Granted {
                                let _ = connect_handle.notification().builder()
                                    .title("Cloud Drive Sync")
                                    .body("Could not connect to daemon. Start it with: cloud-drive-sync start --foreground")
                                    .show();
                            }
                        }
                        break;
                    }
                    tokio::time::sleep(std::time::Duration::from_secs(3)).await;
                }
            });

            // Periodic tray info polling — update daemon status in tray menu
            let info_handle = handle.clone();
            let info_bridge = Arc::clone(&app.state::<BridgeState>().0);
            tauri::async_runtime::spawn(async move {
                loop {
                    tokio::time::sleep(std::time::Duration::from_secs(5)).await;
                    let bridge = info_bridge.lock().await;
                    match bridge.call("get_status", None).await {
                        Ok(status) => {
                            let files = status.get("files_synced").and_then(|v| v.as_u64()).unwrap_or(0);
                            let transfers = status.get("active_transfers").and_then(|v| v.as_u64()).unwrap_or(0);
                            let daemon = status.get("daemon").unwrap_or(&serde_json::Value::Null);
                            let pid = daemon.get("pid").and_then(|v| v.as_u64()).unwrap_or(0);
                            let uptime = daemon.get("uptime_formatted").and_then(|v| v.as_str()).unwrap_or("--");

                            let info = if transfers > 0 {
                                format!("Syncing {} file{} | {} synced | PID {}", transfers, if transfers != 1 { "s" } else { "" }, files, pid)
                            } else {
                                format!("{} files synced | Uptime: {} | PID {}", files, uptime, pid)
                            };
                            tray::update_tray_info(&info_handle, &info);
                        }
                        Err(_) => {
                            tray::update_tray_info(&info_handle, "Daemon: not reachable");
                        }
                    }
                }
            });

            // Forward daemon notifications to frontend events
            let event_handle = handle.clone();
            tauri::async_runtime::spawn(async move {
                while let Some((method, params)) = notification_rx.recv().await {
                    let event_name = format!("daemon:{}", method);
                    let _ = event_handle.emit(&event_name, &params);

                    match method.as_str() {
                        "sync_progress" => {
                            tray::update_tray_status(&event_handle, "Syncing");
                        }
                        "sync_complete" => {
                            tray::update_tray_status(&event_handle, "Connected");
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

            // Hide window on close instead of exiting (tray stays alive)
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

/// Check for stale PID files / zombie daemon processes and clean up.
fn check_daemon_health(app: &AppHandle) {
    let pid_path = get_pid_path();
    if !pid_path.exists() {
        log::info!("No PID file found — daemon not previously running");
        return;
    }

    let pid_str = match std::fs::read_to_string(&pid_path) {
        Ok(s) => s.trim().to_string(),
        Err(_) => return,
    };

    let pid: u32 = match pid_str.parse() {
        Ok(p) => p,
        Err(_) => {
            log::warn!("Invalid PID file, cleaning up");
            let _ = std::fs::remove_file(&pid_path);
            return;
        }
    };

    // Check if process is alive
    let alive = is_process_alive(pid);

    if alive {
        log::info!("Daemon already running (PID {})", pid);
        tray::update_tray_info(app, &format!("Cloud Drive Sync — Daemon running (PID {})", pid));
    } else {
        // Zombie: PID file exists but process is dead
        log::warn!("Stale daemon PID file found (PID {} is dead) — cleaning up", pid);
        tray::update_tray_info(app, "Cloud Drive Sync — Cleaning up stale daemon...");

        // Clean up stale files
        let _ = std::fs::remove_file(&pid_path);
        let socket = pid_path.with_file_name("cloud-drive-sync.sock");
        let _ = std::fs::remove_file(&socket);

        // Notify user
        if let Ok(perm) = app.notification().permission_state() {
            if perm == tauri_plugin_notification::PermissionState::Granted {
                let _ = app.notification().builder()
                    .title("Cloud Drive Sync")
                    .body(&format!("Stale daemon detected (PID {}). Cleaned up — reconnecting.", pid))
                    .show();
            }
        }
    }
}

fn get_pid_path() -> std::path::PathBuf {
    if let Ok(runtime_dir) = std::env::var("XDG_RUNTIME_DIR") {
        let with_subdir = std::path::PathBuf::from(&runtime_dir)
            .join("cloud-drive-sync")
            .join("cloud-drive-sync.pid");
        if with_subdir.parent().map_or(false, |p| p.exists()) {
            return with_subdir;
        }
        return std::path::PathBuf::from(runtime_dir).join("cloud-drive-sync.pid");
    }
    #[cfg(unix)]
    {
        let uid = unsafe { libc::getuid() };
        return std::path::PathBuf::from(format!("/run/user/{}/cloud-drive-sync/cloud-drive-sync.pid", uid));
    }
    #[cfg(windows)]
    {
        return dirs::data_local_dir()
            .unwrap_or_else(std::env::temp_dir)
            .join("cloud-drive-sync")
            .join("cloud-drive-sync.pid");
    }
}

#[cfg(unix)]
fn is_process_alive(pid: u32) -> bool {
    // signal 0 checks if process exists without sending a signal
    unsafe { libc::kill(pid as i32, 0) == 0 }
}

#[cfg(windows)]
fn is_process_alive(pid: u32) -> bool {
    // Use tasklist to check if PID exists (no extra crate needed)
    std::process::Command::new("tasklist")
        .args(["/FI", &format!("PID eq {}", pid), "/NH"])
        .output()
        .map(|o| String::from_utf8_lossy(&o.stdout).contains(&pid.to_string()))
        .unwrap_or(false)
}
