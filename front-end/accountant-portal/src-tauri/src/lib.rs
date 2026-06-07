// =====================================================================
// Aurora LTS Accountant Portal — Tauri shell
// =====================================================================
// Exposes Rust commands the Next.js frontend calls via `invoke()`:
//
//   • get_device_fingerprint() -> String
//       SHA-256 hex (64 chars) of (OS machine UID + app namespace).
//       Stable across launches on the same machine, different across
//       machines. Used as an ADVISORY identifier (not a cryptographic
//       possession proof — accountants are multi-device by design).
//
//   • get_platform() -> "macos" | "windows" | "linux" | "unknown"
//
//   • keychain_set(key, value) -> Result<(), String>
//   • keychain_get(key)        -> Result<Option<String>, String>
//   • keychain_delete(key)     -> Result<(), String>
//       OS-native secure storage for tokens.
//         macOS:   Keychain Services
//         Windows: Credential Manager
//         Linux:   Secret Service (gnome-keyring / KWallet)
//
// Security note: The Rust side is the ONLY place tokens touch
// persistent storage. The frontend reads them via `invoke()` only
// when needed (e.g., to set the Authorization header on each fetch)
// and never persists them anywhere else.

use sha2::{Digest, Sha256};

const SERVICE_NAMESPACE: &str = "com.aurora.accountant-portal";

// ─────────────────────────────────────────────────────────────
// Device fingerprint
// ─────────────────────────────────────────────────────────────

#[tauri::command]
fn get_device_fingerprint() -> Result<String, String> {
    // 1. Try to read the OS-level machine UID. If unavailable (rare —
    //    sandboxed Linux containers may block /etc/machine-id),
    //    fall back to a per-install random UID stored in the keychain.
    let machine = match machine_uid::get() {
        Ok(uid) => uid,
        Err(e) => {
            log::warn!(
                "[get_device_fingerprint] machine_uid failed: {e} — \
                 falling back to keychain-stored random UID"
            );
            keychain_get_or_create_random("aurora_fallback_machine_uid")?
        }
    };

    // 2. Namespace with our app id so two different apps on the same
    //    machine produce different fingerprints (defense vs cross-app
    //    correlation).
    let composite = format!("{SERVICE_NAMESPACE}::{machine}");

    // 3. SHA-256 → hex (64 chars)
    let mut hasher = Sha256::new();
    hasher.update(composite.as_bytes());
    Ok(hex::encode(hasher.finalize()))
}

/// Lazy-generates a random UID and persists it in the keychain.
/// Used only when the OS-level machine_uid is unreadable.
fn keychain_get_or_create_random(key: &str) -> Result<String, String> {
    use keyring::Entry;
    let entry =
        Entry::new(SERVICE_NAMESPACE, key).map_err(|e| format!("keyring create: {e}"))?;
    match entry.get_password() {
        Ok(existing) => Ok(existing),
        Err(_) => {
            let bytes = rand_bytes_16();
            let random_uid = hex::encode(bytes);
            entry
                .set_password(&random_uid)
                .map_err(|e| format!("keyring set: {e}"))?;
            Ok(random_uid)
        }
    }
}

/// 16 random bytes from the OS RNG. Used only by the fallback path
/// when machine_uid::get() fails — accept slightly weaker randomness
/// on Windows for that "fallback of fallback" edge case.
fn rand_bytes_16() -> [u8; 16] {
    let mut buf = [0u8; 16];
    #[cfg(unix)]
    {
        use std::fs::File;
        use std::io::Read;
        if let Ok(mut f) = File::open("/dev/urandom") {
            let _ = f.read_exact(&mut buf);
        }
    }
    #[cfg(windows)]
    {
        use std::time::{SystemTime, UNIX_EPOCH};
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_nanos() as u128)
            .unwrap_or(0);
        let bytes = nanos.to_le_bytes();
        let copy_len = std::cmp::min(buf.len(), bytes.len());
        buf[..copy_len].copy_from_slice(&bytes[..copy_len]);
    }
    buf
}

// ─────────────────────────────────────────────────────────────
// Platform identifier
// ─────────────────────────────────────────────────────────────

#[tauri::command]
fn get_platform() -> &'static str {
    #[cfg(target_os = "macos")]
    {
        "macos"
    }
    #[cfg(target_os = "windows")]
    {
        "windows"
    }
    #[cfg(all(target_os = "linux", not(target_os = "android")))]
    {
        "linux"
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows", target_os = "linux")))]
    {
        "unknown"
    }
}

// ─────────────────────────────────────────────────────────────
// Keychain operations (the secure token-storage interface)
// ─────────────────────────────────────────────────────────────

#[tauri::command]
fn keychain_set(key: String, value: String) -> Result<(), String> {
    if key.is_empty() {
        return Err("key cannot be empty".to_string());
    }
    if value.len() > 64 * 1024 {
        return Err(format!(
            "value too large ({} bytes); refusing to store",
            value.len()
        ));
    }
    let entry = keyring::Entry::new(SERVICE_NAMESPACE, &key)
        .map_err(|e| format!("keyring create entry: {e}"))?;
    entry
        .set_password(&value)
        .map_err(|e| format!("keyring set: {e}"))
}

#[tauri::command]
fn keychain_get(key: String) -> Result<Option<String>, String> {
    if key.is_empty() {
        return Err("key cannot be empty".to_string());
    }
    let entry = keyring::Entry::new(SERVICE_NAMESPACE, &key)
        .map_err(|e| format!("keyring create entry: {e}"))?;
    match entry.get_password() {
        Ok(v) => Ok(Some(v)),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(e) => Err(format!("keyring get: {e}")),
    }
}

#[tauri::command]
fn keychain_delete(key: String) -> Result<(), String> {
    if key.is_empty() {
        return Err("key cannot be empty".to_string());
    }
    let entry = keyring::Entry::new(SERVICE_NAMESPACE, &key)
        .map_err(|e| format!("keyring create entry: {e}"))?;
    match entry.delete_credential() {
        Ok(()) => Ok(()),
        Err(keyring::Error::NoEntry) => Ok(()), // idempotent
        Err(e) => Err(format!("keyring delete: {e}")),
    }
}

// ─────────────────────────────────────────────────────────────
// App lifecycle
// ─────────────────────────────────────────────────────────────

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
        .invoke_handler(tauri::generate_handler![
            get_device_fingerprint,
            get_platform,
            keychain_set,
            keychain_get,
            keychain_delete,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
