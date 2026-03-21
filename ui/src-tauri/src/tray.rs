use tauri::{
    image::Image,
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    AppHandle, Emitter, Manager,
};

pub fn setup_tray(app: &AppHandle) -> Result<(), Box<dyn std::error::Error>> {
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

    let tray_icon = Image::from_bytes(include_bytes!("../icons/icon-idle.png"))
        .expect("Failed to load tray icon");

    let _tray = TrayIconBuilder::with_id("main")
        .icon(tray_icon)
        .tooltip("GDrive Sync")
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
        let tooltip = format!("GDrive Sync - {}", status);
        let _ = tray.set_tooltip(Some(&tooltip));

        // Select icon based on status
        let icon_bytes: &[u8] = match status.to_lowercase().as_str() {
            s if s.contains("syncing") || s.contains("sync") && !s.contains("idle") => {
                include_bytes!("../icons/icon-syncing.png")
            }
            s if s.contains("error") || s.contains("offline") || s.contains("failed") => {
                include_bytes!("../icons/icon-error.png")
            }
            s if s.contains("conflict") => {
                include_bytes!("../icons/icon-conflict.png")
            }
            _ => {
                include_bytes!("../icons/icon-idle.png")
            }
        };

        if let Ok(icon) = Image::from_bytes(icon_bytes) {
            let _ = tray.set_icon(Some(icon));
        }
    }
}
