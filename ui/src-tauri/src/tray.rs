use std::sync::Arc;

use tauri::{
    image::Image,
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    AppHandle, Emitter, Manager,
};

use crate::commands::BridgeState;

/// Stored menu item references for runtime updates.
pub struct TrayMenuItems {
    pub status: MenuItem<tauri::Wry>,
    pub info: MenuItem<tauri::Wry>,
}

/// Ensure tray icon files exist on disk (appindicator on Linux needs file paths).
fn ensure_tray_icons() -> std::path::PathBuf {
    let icon_dir = dirs::data_dir()
        .unwrap_or_else(std::env::temp_dir)
        .join("cloud-drive-sync")
        .join("tray-icons");

    #[cfg(target_os = "linux")]
    {
        let _ = std::fs::create_dir_all(&icon_dir);

        let icons: &[(&str, &[u8])] = &[
            ("tray-idle.png", include_bytes!("../icons/tray-idle.png")),
            ("tray-syncing.png", include_bytes!("../icons/tray-syncing.png")),
            ("tray-error.png", include_bytes!("../icons/tray-error.png")),
            ("tray-conflict.png", include_bytes!("../icons/tray-conflict.png")),
        ];

        for (name, data) in icons {
            let path = icon_dir.join(name);
            let _ = std::fs::write(&path, data);
        }
    }

    icon_dir
}

pub fn setup_tray(app: &AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    let _icon_dir = ensure_tray_icons();

    let status_i = MenuItem::with_id(app, "status", "Status: Starting...", false, None::<&str>)?;
    let info_i = MenuItem::with_id(app, "info", "Daemon: connecting...", false, None::<&str>)?;
    let separator1 = MenuItem::with_id(app, "sep1", "─────────────", false, None::<&str>)?;
    let open_i = MenuItem::with_id(app, "open", "Open Dashboard", true, None::<&str>)?;
    let force_sync_i = MenuItem::with_id(app, "force_sync", "Sync Now", true, None::<&str>)?;
    let pause_i = MenuItem::with_id(app, "pause", "Pause Sync", true, None::<&str>)?;
    let resume_i = MenuItem::with_id(app, "resume", "Resume Sync", true, None::<&str>)?;
    let separator2 = MenuItem::with_id(app, "sep2", "─────────────", false, None::<&str>)?;
    let quit_i = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;

    // Store menu items for runtime updates
    app.manage(TrayMenuItems {
        status: status_i.clone(),
        info: info_i.clone(),
    });

    let menu = Menu::with_items(
        app,
        &[
            &status_i,
            &info_i,
            &separator1,
            &open_i,
            &force_sync_i,
            &pause_i,
            &resume_i,
            &separator2,
            &quit_i,
        ],
    )?;

    let tray_icon = Image::from_bytes(include_bytes!("../icons/tray-idle.png"))
        .expect("Failed to load tray icon");

    let mut builder = TrayIconBuilder::with_id("main")
        .icon(tray_icon)
        .tooltip("Cloud Drive Sync")
        .menu(&menu)
        .show_menu_on_left_click(false);

    #[cfg(target_os = "linux")]
    {
        builder = builder.temp_dir_path(&_icon_dir);
    }

    let _tray = builder
        .on_menu_event(move |app, event| match event.id.as_ref() {
            "open" => {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.unminimize();
                    let _ = window.show();
                    let _ = window.set_focus();
                }
            }
            "force_sync" => {
                call_daemon(app, "force_sync", None);
                let _ = app.emit("tray-action", "force_sync");
            }
            "pause" => {
                call_daemon(app, "pause_sync", None);
                let _ = app.emit("tray-action", "toggle_pause");
            }
            "resume" => {
                call_daemon(app, "resume_sync", None);
                let _ = app.emit("tray-action", "toggle_pause");
            }
            "quit" => {
                // Stop daemon before exiting, verify it stopped
                let app_clone = app.clone();
                tauri::async_runtime::spawn(async move {
                    let bridge_state = app_clone.state::<BridgeState>();

                    // Inform user
                    update_tray_info(&app_clone, "Shutting down...");
                    let _ = app_clone.emit("daemon-status-msg", "Stopping daemon...");

                    // Send shutdown command
                    {
                        let bridge = bridge_state.0.lock().await;
                        log::info!("Sending shutdown to daemon...");
                        let _ = bridge.call("shutdown", None).await;
                    }

                    // Wait and verify daemon stopped
                    tokio::time::sleep(std::time::Duration::from_secs(2)).await;
                    {
                        let bridge = bridge_state.0.lock().await;
                        match bridge.call("get_status", None).await {
                            Ok(_) => {
                                log::warn!("Daemon did not shut down cleanly");
                                update_tray_info(&app_clone, "Daemon did not stop!");
                                if let Some(window) = app_clone.get_webview_window("main") {
                                    let _ = window.show();
                                }
                                let _ = app_clone.emit("daemon-status-msg",
                                    "Warning: Daemon did not shut down cleanly. It may still be running.");
                                tokio::time::sleep(std::time::Duration::from_secs(3)).await;
                            }
                            Err(_) => {
                                log::info!("Daemon shut down successfully");
                            }
                        }
                    }
                    app_clone.exit(0);
                });
            }
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                let app = tray.app_handle();
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.unminimize();
                    let _ = window.show();
                    let _ = window.set_focus();
                }
            }
        })
        .build(app)?;

    Ok(())
}

/// Call the daemon via the IPC bridge from tray menu handlers.
fn call_daemon(app: &AppHandle, method: &str, params: Option<serde_json::Value>) {
    let bridge_state = app.state::<BridgeState>();
    let bridge = Arc::clone(&bridge_state.0);
    let method = method.to_string();
    tauri::async_runtime::spawn(async move {
        let bridge = bridge.lock().await;
        if let Err(e) = bridge.call(&method, params).await {
            log::error!("Tray action {} failed: {}", method, e);
        }
    });
}

pub fn update_tray_status(app: &AppHandle, status: &str) {
    // Update the status menu item text
    if let Some(items) = app.try_state::<TrayMenuItems>() {
        let _ = items.status.set_text(format!("Status: {}", status));
    }

    if let Some(tray) = app.tray_by_id("main") {
        let tooltip = format!("Cloud Drive Sync — {}", status);
        let _ = tray.set_tooltip(Some(&tooltip));

        // Select icon based on status
        let icon_name = match status.to_lowercase().as_str() {
            s if s.contains("syncing") || (s.contains("sync") && !s.contains("idle")) => {
                "tray-syncing.png"
            }
            s if s.contains("error") || s.contains("offline") || s.contains("failed") => {
                "tray-error.png"
            }
            s if s.contains("conflict") => {
                "tray-conflict.png"
            }
            _ => {
                "tray-idle.png"
            }
        };

        #[cfg(target_os = "linux")]
        {
            let icon_dir = dirs::data_dir()
                .unwrap_or_else(std::env::temp_dir)
                .join("cloud-drive-sync")
                .join("tray-icons");
            let icon_path = icon_dir.join(icon_name);
            let _ = tray.set_temp_dir_path(Some(&icon_dir));
            if icon_path.exists() {
                if let Ok(data) = std::fs::read(&icon_path) {
                    if let Ok(icon) = Image::from_bytes(&data) {
                        let _ = tray.set_icon(Some(icon));
                        return;
                    }
                }
            }
        }

        let icon_bytes: &[u8] = match icon_name {
            "tray-syncing.png" => include_bytes!("../icons/tray-syncing.png"),
            "tray-error.png" => include_bytes!("../icons/tray-error.png"),
            "tray-conflict.png" => include_bytes!("../icons/tray-conflict.png"),
            _ => include_bytes!("../icons/tray-idle.png"),
        };
        if let Ok(icon) = Image::from_bytes(icon_bytes) {
            let _ = tray.set_icon(Some(icon));
        }
    }
}

/// Update the info menu item with daemon details.
pub fn update_tray_info(app: &AppHandle, info: &str) {
    if let Some(items) = app.try_state::<TrayMenuItems>() {
        let _ = items.info.set_text(info);
    }
    if let Some(tray) = app.tray_by_id("main") {
        let _ = tray.set_tooltip(Some(info));
    }
}
