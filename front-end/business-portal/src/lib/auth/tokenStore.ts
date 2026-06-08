"use client";

/**
 * Browser token store for the (web-only) Business Owner Portal.
 * localStorage so the session survives a tab close; cleared on sign-out or a
 * fatal 401. (The accountant portal uses the OS keychain via Tauri; this portal
 * is a plain browser SPA, so localStorage is the right home.)
 */
import { TOKEN_KEYS } from "@/types/api";
import type { BusinessOwnerUser } from "@/types/api";

const isBrowser = () => typeof window !== "undefined";

export function getToken(): string | null {
  return isBrowser() ? window.localStorage.getItem(TOKEN_KEYS.accessToken) : null;
}

export function setSession(token: string, user: BusinessOwnerUser): void {
  if (!isBrowser()) return;
  window.localStorage.setItem(TOKEN_KEYS.accessToken, token);
  window.localStorage.setItem(TOKEN_KEYS.user, JSON.stringify(user));
}

export function getStoredUser(): BusinessOwnerUser | null {
  if (!isBrowser()) return null;
  const raw = window.localStorage.getItem(TOKEN_KEYS.user);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as BusinessOwnerUser;
  } catch {
    return null;
  }
}

export function clearSession(): void {
  if (!isBrowser()) return;
  window.localStorage.removeItem(TOKEN_KEYS.accessToken);
  window.localStorage.removeItem(TOKEN_KEYS.user);
}
