mod keychain;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
  tauri::Builder::default()
    .setup(|app| {
      if cfg!(debug_assertions) {
        app.handle().plugin(
          tauri_plugin_log::Builder::default()
            .level(log::LevelFilter::Info)
            .build(),
        )?;
      }
      Ok(())
    })
    // Touch ID / Keychain IPC commands — see src/keychain.rs.
    // The JS side invokes via window.__TAURI__.core.invoke("...").
    .invoke_handler(tauri::generate_handler![
      keychain::is_touch_id_enabled,
      keychain::enable_touch_id,
      keychain::login_with_touch_id,
      keychain::disable_touch_id,
    ])
    .run(tauri::generate_context!())
    .expect("error while running tauri application");
}
