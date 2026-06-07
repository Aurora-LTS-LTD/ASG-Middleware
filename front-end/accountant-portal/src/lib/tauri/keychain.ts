/**
 * Thin TypeScript wrapper around the Rust keychain commands.
 *
 * The actual storage lives in OS-native facilities (Keychain on macOS,
 * Credential Manager on Windows, Secret Service on Linux) — see
 * src-tauri/src/lib.rs. This module is the only frontend module that
 * imports `@tauri-apps/api/core` for `invoke`; everything else goes
 * through `keychain` to keep the Tauri-vs-browser distinction local.
 *
 * For dev / browser-only runs (npm run dev WITHOUT Tauri), invoke
 * fails. We detect that and fall back to sessionStorage so the UI
 * still works for design iteration — but this fallback is NOT used
 * when running inside the Tauri shell.
 */

import { KEYCHAIN_KEYS, type KeychainKey } from "@/types/api";

// Detect Tauri runtime — `window.__TAURI__` is defined inside the
// Tauri webview, absent in plain Next.js dev.
function isTauri(): boolean {
  if (typeof window === "undefined") return false;
  return Boolean(
    (window as unknown as { __TAURI__?: unknown; __TAURI_INTERNALS__?: unknown })
      .__TAURI_INTERNALS__ ||
      (window as unknown as { __TAURI__?: unknown }).__TAURI__,
  );
}

async function invokeTauri<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  // Lazy-import so server-side rendering doesn't try to resolve
  // @tauri-apps/api (which expects a browser environment).
  const { invoke } = await import("@tauri-apps/api/core");
  return (await invoke(cmd, args)) as T;
}

// ─────────────────────────────────────────────────────────────
// Public API
// ─────────────────────────────────────────────────────────────

export async function keychainSet(key: KeychainKey, value: string): Promise<void> {
  if (isTauri()) {
    await invokeTauri<void>("keychain_set", { key, value });
    return;
  }
  // Browser fallback — sessionStorage so the value DOESN'T survive
  // tab close (still safer than localStorage for dev).
  if (typeof sessionStorage !== "undefined") {
    sessionStorage.setItem(`aurora.${key}`, value);
  }
}

export async function keychainGet(key: KeychainKey): Promise<string | null> {
  if (isTauri()) {
    const v = await invokeTauri<string | null>("keychain_get", { key });
    return v ?? null;
  }
  if (typeof sessionStorage !== "undefined") {
    return sessionStorage.getItem(`aurora.${key}`);
  }
  return null;
}

export async function keychainDelete(key: KeychainKey): Promise<void> {
  if (isTauri()) {
    await invokeTauri<void>("keychain_delete", { key });
    return;
  }
  if (typeof sessionStorage !== "undefined") {
    sessionStorage.removeItem(`aurora.${key}`);
  }
}

/** Clear ALL Aurora keychain entries — used by logout. */
export async function keychainClearAll(): Promise<void> {
  await Promise.all(
    Object.values(KEYCHAIN_KEYS).map((k) => keychainDelete(k).catch(() => undefined)),
  );
}

// ─────────────────────────────────────────────────────────────
// Device / Platform helpers — also Rust-backed
// ─────────────────────────────────────────────────────────────

const DEV_MOCK_FINGERPRINT =
  "a3f7c2e1d4b5a6c7e8d9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1";

export async function getDeviceFingerprint(): Promise<string> {
  if (isTauri()) {
    return await invokeTauri<string>("get_device_fingerprint");
  }
  // Browser fallback for design iteration — return a stable dev fp.
  return DEV_MOCK_FINGERPRINT;
}

export async function getPlatform(): Promise<"macos" | "windows" | "linux" | "unknown"> {
  if (isTauri()) {
    return await invokeTauri<"macos" | "windows" | "linux" | "unknown">("get_platform");
  }
  // Browser fallback — infer from navigator
  if (typeof navigator === "undefined") return "unknown";
  const ua = navigator.userAgent;
  if (/Macintosh/i.test(ua)) return "macos";
  if (/Windows/i.test(ua)) return "windows";
  if (/Linux/i.test(ua)) return "linux";
  return "unknown";
}

/** Useful for UI — `data-runtime="tauri"` vs `"browser"`. */
export const isTauriRuntime = isTauri;
