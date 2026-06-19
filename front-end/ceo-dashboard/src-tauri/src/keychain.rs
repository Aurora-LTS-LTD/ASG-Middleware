// Touch ID — Keychain-gated biometric login for the CEO Dashboard.
//
// macOS only. The JWT is stored in the user's login Keychain with
// `kSecAccessControlBiometryAny`, so READING it (via SecItemCopyMatching)
// triggers Touch ID at the OS level. We never call LAContext directly —
// the keychain integration is the prompt.
//
// Service tag: `com.aurora.ceo-dashboard.token` (matches the bundle ID).
// Account:     `aurora-jwt`
//
// `#[tauri::command]` cannot live inside a nested submodule (its macro
// expansion produces sibling helper items that the build system finds
// only at the module-where-`generate_handler!`-is-called level).
// Therefore each command is declared at top level with two cfg-gated
// implementations.

const SERVICE: &str = "com.aurora.ceo-dashboard.token";
const ACCOUNT: &str = "aurora-jwt";

// ── macOS imports + helpers ────────────────────────────────────────
#[cfg(target_os = "macos")]
use core_foundation::base::{CFType, TCFType};
#[cfg(target_os = "macos")]
use core_foundation::boolean::CFBoolean;
#[cfg(target_os = "macos")]
use core_foundation::data::CFData;
#[cfg(target_os = "macos")]
use core_foundation::dictionary::CFDictionary;
#[cfg(target_os = "macos")]
use core_foundation::string::CFString;
#[cfg(target_os = "macos")]
use core_foundation_sys::base::{CFOptionFlags, CFTypeRef};
#[cfg(target_os = "macos")]
use security_framework::access_control::{ProtectionMode, SecAccessControl};
#[cfg(target_os = "macos")]
use security_framework_sys::base::errSecSuccess;
#[cfg(target_os = "macos")]
use security_framework_sys::item::{
    kSecAttrAccessControl, kSecAttrAccount, kSecAttrService, kSecClass,
    kSecClassGenericPassword, kSecReturnData, kSecValueData,
};
#[cfg(target_os = "macos")]
use security_framework_sys::keychain_item::{SecItemAdd, SecItemCopyMatching, SecItemDelete};

/// kSecAccessControlBiometryAny — bit 1<<3 per <Security/SecAccessControl.h>.
#[cfg(target_os = "macos")]
const BIOMETRY_ANY: CFOptionFlags = 1 << 3;

/// errSecItemNotFound — SecItemDelete returns this when no entry exists.
#[cfg(target_os = "macos")]
const ERR_ITEM_NOT_FOUND: i32 = -25300;

/// kSecUseOperationPrompt is not exported by security-framework-sys 2.x —
/// fetch the underlying CFString at runtime using its system-defined name.
#[cfg(target_os = "macos")]
fn use_operation_prompt_key() -> CFString {
    CFString::from("u_OpPrompt")
}

/// Build the (kSecClass, kSecAttrService, kSecAttrAccount) base query
/// dictionary used by all 4 commands.
#[cfg(target_os = "macos")]
fn base_query_pairs() -> Vec<(CFString, CFType)> {
    let svc = CFString::from(SERVICE);
    let acct = CFString::from(ACCOUNT);
    let cls = unsafe { CFString::wrap_under_get_rule(kSecClassGenericPassword) };
    vec![
        (unsafe { CFString::wrap_under_get_rule(kSecClass) }, cls.as_CFType()),
        (unsafe { CFString::wrap_under_get_rule(kSecAttrService) }, svc.as_CFType()),
        (unsafe { CFString::wrap_under_get_rule(kSecAttrAccount) }, acct.as_CFType()),
    ]
}

// ── Command 1: is_touch_id_enabled ─────────────────────────────────
/// True if a Keychain entry exists for this app. Does NOT trigger
/// Touch ID — we omit kSecReturnData so the OS only confirms presence.
#[cfg(target_os = "macos")]
#[tauri::command]
pub fn is_touch_id_enabled() -> bool {
    let query = CFDictionary::from_CFType_pairs(&base_query_pairs());
    let mut result: CFTypeRef = std::ptr::null();
    let status = unsafe { SecItemCopyMatching(query.as_concrete_TypeRef(), &mut result) };
    // errSecSuccess (0): found. errSecInteractionNotAllowed (-25308): also
    // means the item exists but requires UI we didn't ask for.
    status == errSecSuccess || status == -25308
}

#[cfg(not(target_os = "macos"))]
#[tauri::command]
pub fn is_touch_id_enabled() -> bool {
    false
}

// ── Command 2: enable_touch_id ─────────────────────────────────────
/// Store the JWT in Keychain with Touch ID required to read it.
/// Idempotent: any existing entry is wiped first.
#[cfg(target_os = "macos")]
#[tauri::command]
pub fn enable_touch_id(token: String) -> Result<(), String> {
    let _ = disable_touch_id();

    let access = SecAccessControl::create_with_protection(
        Some(ProtectionMode::AccessibleWhenUnlockedThisDeviceOnly),
        BIOMETRY_ANY,
    )
    .map_err(|e| format!("SecAccessControl create failed: {e:?}"))?;
    // SecAccessControl doesn't expose .as_CFType() directly — wrap its raw
    // CFTypeRef into a CFType so we can put it in the attribute dict.
    let access_cf: CFType = unsafe { CFType::wrap_under_get_rule(access.as_CFTypeRef()) };

    let data = CFData::from_buffer(token.as_bytes());

    let mut pairs = base_query_pairs();
    pairs.push((unsafe { CFString::wrap_under_get_rule(kSecValueData) }, data.as_CFType()));
    pairs.push((
        unsafe { CFString::wrap_under_get_rule(kSecAttrAccessControl) },
        access_cf,
    ));
    let attrs = CFDictionary::from_CFType_pairs(&pairs);

    let status = unsafe { SecItemAdd(attrs.as_concrete_TypeRef(), std::ptr::null_mut()) };
    if status == errSecSuccess {
        Ok(())
    } else {
        Err(format!("SecItemAdd failed: OSStatus {status}"))
    }
}

#[cfg(not(target_os = "macos"))]
#[tauri::command]
pub fn enable_touch_id(_token: String) -> Result<(), String> {
    Err("Touch ID is only available on macOS".to_string())
}

// ── Command 3: login_with_touch_id ─────────────────────────────────
/// Read the JWT from Keychain — fires the Touch ID prompt as a side
/// effect of the kSecAttrAccessControl flag set at enable time.
#[cfg(target_os = "macos")]
#[tauri::command]
pub fn login_with_touch_id() -> Result<String, String> {
    let prompt = CFString::from("Authenticate to open Aurora LTS CEO Dashboard");
    let mut pairs = base_query_pairs();
    pairs.push((
        unsafe { CFString::wrap_under_get_rule(kSecReturnData) },
        CFBoolean::true_value().as_CFType(),
    ));
    pairs.push((use_operation_prompt_key(), prompt.as_CFType()));
    let query = CFDictionary::from_CFType_pairs(&pairs);

    let mut result: CFTypeRef = std::ptr::null();
    let status = unsafe { SecItemCopyMatching(query.as_concrete_TypeRef(), &mut result) };

    if status != errSecSuccess {
        // Common failure codes:
        //   -25293 errSecAuthFailed   (Touch ID failed / cancelled)
        //   -128   errSecUserCanceled
        //   -25300 errSecItemNotFound (entry was wiped)
        return Err(format!("Touch ID failed: OSStatus {status}"));
    }

    let data = unsafe { CFData::wrap_under_create_rule(result as _) };
    String::from_utf8(data.bytes().to_vec()).map_err(|e| format!("utf8 decode: {e}"))
}

#[cfg(not(target_os = "macos"))]
#[tauri::command]
pub fn login_with_touch_id() -> Result<String, String> {
    Err("Touch ID is only available on macOS".to_string())
}

// ── Command 4: disable_touch_id ────────────────────────────────────
/// Remove the Keychain entry. Idempotent — OK if no entry exists.
#[cfg(target_os = "macos")]
#[tauri::command]
pub fn disable_touch_id() -> Result<(), String> {
    let query = CFDictionary::from_CFType_pairs(&base_query_pairs());
    let status = unsafe { SecItemDelete(query.as_concrete_TypeRef()) };
    if status == errSecSuccess || status == ERR_ITEM_NOT_FOUND {
        Ok(())
    } else {
        Err(format!("SecItemDelete failed: OSStatus {status}"))
    }
}

#[cfg(not(target_os = "macos"))]
#[tauri::command]
pub fn disable_touch_id() -> Result<(), String> {
    Ok(())
}
