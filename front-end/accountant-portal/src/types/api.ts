/**
 * Aurora Accountant Portal — Backend API types.
 *
 * MIRRORS the Pydantic models in
 * ~/Desktop/ASG-Middleware/server_files/app/routers/accountant_auth.py
 * verbatim. Keep these in sync if either side ever changes.
 *
 * No runtime code in this file — pure type declarations.
 */

// ─────────────────────────────────────────────────────────────
// Common
// ─────────────────────────────────────────────────────────────

export type Platform = "macos" | "windows" | "linux" | "unknown";

export interface AccountantUser {
  id: number;
  email: string;
  name: string;
  role: "accountant";
  firm_name: string | null;
  license_number: string | null;
}

export interface ApiError {
  detail: {
    error: string;            // machine-readable code, e.g. "otp_invalid"
    message: string;          // human-readable
    retry_after_seconds?: number;
    attempts_remaining?: number;
    [key: string]: unknown;   // extra fields per error type
  };
}

// ─────────────────────────────────────────────────────────────
// /otp/send
// ─────────────────────────────────────────────────────────────

export interface OtpSendRequest {
  email: string;
}

export interface OtpSendResponse {
  ok: true;
  sent_to: string;             // masked email/phone for confirmation UX
  expires_in_seconds: number;
  method: "email" | "whatsapp";
}

// ─────────────────────────────────────────────────────────────
// /otp/verify
// ─────────────────────────────────────────────────────────────

export interface OtpVerifyRequest {
  email: string;
  otp: string;
  device_fingerprint: string;
  platform: Platform;
  device_label: string;
}

export interface OtpVerifyResponse {
  ok: true;
  access_token: string;
  refresh_token: string;
  access_token_expires_at: string;     // ISO 8601
  refresh_token_expires_at: string;    // ISO 8601
  device_id: number;
  is_new_device: boolean;
  user: AccountantUser;
}

// ─────────────────────────────────────────────────────────────
// /login (email + password) — returns the same shape as OtpVerifyResponse
// ─────────────────────────────────────────────────────────────

export interface LoginRequest {
  email: string;
  password: string;
  device_fingerprint: string;
  platform: Platform;
  device_label: string;
}

// ─────────────────────────────────────────────────────────────
// /forgot-password + /reset-password (email recovery)
// ─────────────────────────────────────────────────────────────

export interface ForgotPasswordResponse {
  ok: true;
  sent_to: string;             // masked email for confirmation UX
  expires_in_seconds: number;
}

export interface ResetPasswordRequest {
  email: string;
  code: string;
  new_password: string;
}

export interface OkResponse {
  ok: boolean;
}

// ─────────────────────────────────────────────────────────────
// /refresh
// ─────────────────────────────────────────────────────────────

export interface RefreshRequest {
  refresh_token: string;
  device_fingerprint: string;
}

export interface RefreshResponse {
  ok: true;
  access_token: string;
  refresh_token: string;               // ROTATED — replace stored value
  access_token_expires_at: string;
  refresh_token_expires_at: string;
}

// ─────────────────────────────────────────────────────────────
// /logout
// ─────────────────────────────────────────────────────────────

export interface LogoutRequest {
  refresh_token?: string;
}

export interface LogoutResponse {
  ok: true;
}

// ─────────────────────────────────────────────────────────────
// /devices
// ─────────────────────────────────────────────────────────────

export interface AccountantDevice {
  id: number;
  device_fingerprint_preview: string;  // first 16 chars + ellipsis
  platform: Platform;
  device_label: string;
  enrolled_at: string;
  last_seen_at: string;
  use_count: number;
  is_current_device: boolean;
  ip_geo_hint: string | null;
}

export interface DeviceListResponse {
  devices: AccountantDevice[];
}

export interface DeviceRevokeRequest {
  reason?: string;
}

export interface DeviceRevokeResponse {
  ok: true;
  device_id: number;
  revoked_at: string;
}

export interface DeviceRelabelRequest {
  device_label: string;
}

export interface DeviceRelabelResponse {
  ok: true;
  device_id: number;
  device_label: string;
}

// ─────────────────────────────────────────────────────────────
// Keychain storage keys (used by tauri/keychain.ts)
// ─────────────────────────────────────────────────────────────

export const KEYCHAIN_KEYS = {
  accessToken: "access_token",
  accessTokenExpiry: "access_token_expires_at",
  refreshToken: "refresh_token",
  refreshTokenExpiry: "refresh_token_expires_at",
  userId: "user_id",
  userEmail: "user_email",
  userName: "user_name",
  deviceId: "device_id",
  deviceLabel: "device_label",
} as const;

export type KeychainKey = (typeof KEYCHAIN_KEYS)[keyof typeof KEYCHAIN_KEYS];
