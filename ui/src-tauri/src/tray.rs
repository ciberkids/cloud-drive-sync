use tauri::{
    image::Image,
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    AppHandle, Emitter, Manager,
};

/// Ensure tray icon files exist on disk (appindicator on Linux needs file paths).
fn ensure_tray_icons() -> std::path::PathBuf {
    let icon_dir = dirs::data_dir()
        .unwrap_or_else(|| std::path::PathBuf::from("/tmp"))
        .join("cloud-drive-sync")
        .join("tray-icons");
    let _ = std::fs::create_dir_all(&icon_dir);

    let icons: &[(&str, &[u8])] = &[
        ("tray-idle.png", include_bytes!("../icons/tray-idle.png")),
        ("tray-syncing.png", include_bytes!("../icons/tray-syncing.png")),
        ("tray-error.png", include_bytes!("../icons/tray-error.png")),
        ("tray-conflict.png", include_bytes!("../icons/tray-conflict.png")),
    ];

    for (name, data) in icons {
        let path = icon_dir.join(name);
        // Always overwrite to ensure icons are up-to-date after upgrades
        let _ = std::fs::write(&path, data);
    }

    icon_dir
}

pub fn setup_tray(app: &AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    // Write icons to disk for appindicator compatibility
    let _icon_dir = ensure_tray_icons();

    let status_i = MenuItem::with_id(app, "status", "Status: Connecting...", false, None::<&str>)?;
    let separator1 = MenuItem::with_id(app, "sep1", "─────────────", false, None::<&str>)?;
    let open_i = MenuItem::with_id(app, "open", "Open Settings", true, None::<&str>)?;
    let force_sync_i = MenuItem::with_id(app, "force_sync", "Sync Now", true, None::<&str>)?;
    let pause_i = MenuItem::with_id(app, "pause", "Pause Sync", true, None::<&str>)?;
    let separator2 = MenuItem::with_id(app, "sep2", "─────────────", false, None::<&str>)?;
    let quit_i = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;

    let menu = Menu::with_items(
        app,
        &[
            &status_i,
            &separator1,
            &open_i,
            &force_sync_i,
            &pause_i,
            &separator2,
            &quit_i,
        ],
    )?;

    let tray_icon = Image::from_bytes(include_bytes!("../icons/tray-idle.png"))
        .expect("Failed to load tray icon");

    let _tray = TrayIconBuilder::with_id("main")
        .icon(tray_icon)
        .temp_dir_path(&_icon_dir)
        .tooltip("Cloud Drive Sync")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(move |app, event| match event.id.as_ref() {
            "open" => {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.unminimize();
                    let _ = window.show();
                    let _ = window.set_focus();
                }
            }
            "force_sync" => {
                let _ = app.emit("tray-action", "force_sync");
            }
            "pause" => {
                let _ = app.emit("tray-action", "toggle_pause");
            }
            "quit" => {
                app.exit(0);
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

pub fn update_tray_status(app: &AppHandle, status: &str) {
    if let Some(tray) = app.tray_by_id("main") {
        let tooltip = format!("Cloud Drive Sync - {}", status);
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

        // Try loading from disk first (better appindicator compat)
        let icon_dir = dirs::data_dir()
            .unwrap_or_else(|| std::path::PathBuf::from("/tmp"))
            .join("cloud-drive-sync")
            .join("tray-icons");
        let icon_path = icon_dir.join(icon_name);

        // Set temp dir to our stable icon directory so KDE/appindicator
        // can reliably find the icon files (avoids transparent icon issue)
        let _ = tray.set_temp_dir_path(Some(&icon_dir));

        if icon_path.exists() {
            if let Ok(data) = std::fs::read(&icon_path) {
                if let Ok(icon) = Image::from_bytes(&data) {
                    let _ = tray.set_icon(Some(icon));
                    return;
                }
            }
        }

        // Fallback to embedded tray-idle
        if let Ok(icon) = Image::from_bytes(include_bytes!("../icons/tray-idle.png")) {
            let _ = tray.set_icon(Some(icon));
        }
    }
}
