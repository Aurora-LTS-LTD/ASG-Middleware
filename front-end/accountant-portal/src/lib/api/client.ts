/**
 * Aurora Accountant Portal — Backend API client.
 *
 * Talks to https://api-aurora-lts.com/api/v1/accountant/* via fetch.
 * Handles:
 *   • Auth header injection (access token from OS keychain)
 *   • Automatic refresh-token rotation on 401
 *   • Single-flight refresh (concurrent 401s share one refresh call)
 *   • Structured error decoding (matches Pydantic ApiError shape)
 *
 * Configuration:
 *   • NEXT_PUBLIC_AURORA_API_BASE  (default: https://api-aurora-lts.com)
 *   • NEXT_PUBLIC_USE_MOCK_API     (set "true" to use mock layer)
 *
 * On any auth-fatal error (refresh_token_invalid, device_revoked,
 * device_mismatch), the client throws an `AuthFatalError`. The auth
 * context catches it, clears the keychain, and navigates to the
 * login screen.
 */

import {
  keychainGet,
  keychainSet,
  keychainClearAll,
  getDeviceFingerprint,
} from "@/lib/tauri/keychain";
import { KEYCHAIN_KEYS } from "@/types/api";
import type {
  ApiError,
  AccountantUser,
  OtpSendRequest,
  OtpSendResponse,
  OtpVerifyRequest,
  OtpVerifyResponse,
  LoginRequest,
  ForgotPasswordResponse,
  ResetPasswordRequest,
  OkResponse,
  AccountantBook,
  OrgSummary,
  Earnings,
  ExportsList,
  RefreshResponse,
  LogoutRequest,
  LogoutResponse,
  DeviceListResponse,
  DeviceRevokeRequest,
  DeviceRevokeResponse,
  DeviceRelabelRequest,
  DeviceRelabelResponse,
} from "@/types/api";

// ─────────────────────────────────────────────────────────────
// Config
// ─────────────────────────────────────────────────────────────

// Twin-Engine architecture: two independent backends that share one
// database + JWT secret, so a single access token authenticates on BOTH.
//   • M1 (tax)  — Transparent Tax & Invoices server  (aurora-api-tax)
//   • M2 (core) — Premium AI Core & Copilot server   (aurora-api-core)
const M1_TAX_URL = (
  process.env.NEXT_PUBLIC_AURORA_API_BASE || "https://api-aurora-lts.com"
).replace(/\/+$/, "");

// Set NEXT_PUBLIC_AURORA_CORE_BASE once aurora-api-core has a stable URL.
// Defaults to the tax base so the app degrades gracefully (health dot will
// just report whatever that host returns) until the core service is live.
const M2_CORE_URL = (
  process.env.NEXT_PUBLIC_AURORA_CORE_BASE || M1_TAX_URL
).replace(/\/+$/, "");

// Dual-context config layer: stable public names for the two backend base URLs.
// Window/View A (Tax/Billing/Compliance) targets M1; Window/View B (AI/Core/
// Native) targets M2. The engine-bound clients below are the only callers.
export const M1_API_BASE_URL = M1_TAX_URL;
export const M2_API_BASE_URL = M2_CORE_URL;

/** Which backend a request targets. Bound per engine-client; not a per-call option. */
export type ApiEngine = "m1" | "m2";

function baseForEngine(engine: ApiEngine | undefined): string {
  return engine === "m2" ? M2_CORE_URL : M1_TAX_URL;
}

// ═════════════════════════════════════════════════════════════════════
// M1 vs M2 ROUTING RULE — the policy every call site MUST follow
// ═════════════════════════════════════════════════════════════════════
// Aurora is split across TWO backends sharing one DB + JWT secret. The
// `engine` option on `call()` (and the auto-engine on call sites that
// hardcode a base) decides which one a request targets.
//
//   PATH PATTERN                                  ENGINE   call() opt
//   ────────────────────────────────────────────  ──────   ─────────────
//   /api/v1/marketing/*                           M1       default
//   /api/v1/accountant/{otp,refresh,logout,…}*    M1       default
//   /api/v1/accountant/devices/*                  M1       default
//   /api/v1/accountant/dashboard/*                M1       default
//   /api/v1/accountant/vault/*                    M1       default
//   /api/v1/admin/exec/copilot/*                  M2       engine: "m2"   ← REQUIRED
//   /api/v1/admin/exec/native/*                   M2       engine: "m2"   ← REQUIRED
//   /api/v1/admin/exec/{telemetry,charts,…}/*     M1       default
//   /api/v1/core/health  +  /  (root)             M2       engine: "m2"
//
// Routing health probes are pre-wired in src/lib/cockpit/context.tsx
// (M1 → /api/v1/onboarding/health, M2 → /api/v1/core/health). DO NOT
// change those without updating both sides.
//
// When NEXT_PUBLIC_AURORA_CORE_BASE is unset, M2_CORE_URL falls back to
// M1_TAX_URL — which means M2 calls hit M1 and 404. Set the env var in
// .env.local (see .env.local.example) the moment aurora-api-core is live.
// ═════════════════════════════════════════════════════════════════════

// Back-compat: existing call sites that imported API_BASE keep hitting M1.
const API_BASE = M1_TAX_URL;

const USE_MOCK =
  (process.env.NEXT_PUBLIC_USE_MOCK_API || "").toLowerCase() === "true";

const REFRESH_LEEWAY_MS = 30_000; // refresh 30s before access token expires

// ─────────────────────────────────────────────────────────────
// Error types
// ─────────────────────────────────────────────────────────────

export class ApiClientError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly errorCode: string,
    public readonly detail: ApiError["detail"],
  ) {
    super(message);
    this.name = "ApiClientError";
  }
}

/** Thrown when auth state is unrecoverable — caller must re-login. */
export class AuthFatalError extends ApiClientError {
  constructor(status: number, code: string, detail: ApiError["detail"]) {
    super(detail.message || "Authentication failed", status, code, detail);
    this.name = "AuthFatalError";
  }
}

const AUTH_FATAL_CODES = new Set([
  "refresh_token_invalid",
  "device_revoked",
  "device_mismatch",
  "user_inactive",
]);

// ─────────────────────────────────────────────────────────────
// Single-flight refresh (de-dupes concurrent 401 handlers)
// ─────────────────────────────────────────────────────────────

let refreshInFlight: Promise<string | null> | null = null;

async function tryRefresh(): Promise<string | null> {
  if (refreshInFlight) return refreshInFlight;
  refreshInFlight = (async () => {
    try {
      const refreshToken = await keychainGet(KEYCHAIN_KEYS.refreshToken);
      if (!refreshToken) return null;
      const fingerprint = await getDeviceFingerprint();

      const resp = await fetch(`${API_BASE}/api/v1/accountant/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          refresh_token: refreshToken,
          device_fingerprint: fingerprint,
        }),
        cache: "no-store",
      });

      if (!resp.ok) {
        const body = (await resp.json().catch(() => ({}))) as ApiError;
        const code = body?.detail?.error || "refresh_failed";
        if (AUTH_FATAL_CODES.has(code)) {
          await keychainClearAll();
        }
        return null;
      }

      const data = (await resp.json()) as RefreshResponse;
      // Rotated — persist new pair, drop old.
      await keychainSet(KEYCHAIN_KEYS.accessToken, data.access_token);
      await keychainSet(KEYCHAIN_KEYS.accessTokenExpiry, data.access_token_expires_at);
      await keychainSet(KEYCHAIN_KEYS.refreshToken, data.refresh_token);
      await keychainSet(KEYCHAIN_KEYS.refreshTokenExpiry, data.refresh_token_expires_at);
      return data.access_token;
    } finally {
      refreshInFlight = null;
    }
  })();
  return refreshInFlight;
}

// ─────────────────────────────────────────────────────────────
// Core fetch wrapper
// ─────────────────────────────────────────────────────────────

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  authRequired?: boolean;  // default false; set true for protected endpoints
  engine?: ApiEngine;      // default "m1" (tax); set "m2" to hit the AI core
}

// INTERNAL only — no longer exported. All requests go through an engine-bound
// client (apiM1 / apiM2) so a call site cannot target the wrong backend.
async function call<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const method = opts.method || "GET";

  // FormData bodies skip JSON serialisation + let the browser set
  // Content-Type with the correct multipart boundary. Used by vault
  // upload and any future file-attaching endpoints.
  const isMultipart =
    typeof FormData !== "undefined" && opts.body instanceof FormData;

  async function buildHeaders(): Promise<Headers> {
    const h = new Headers();
    if (!isMultipart) {
      h.set("Content-Type", "application/json");
    }
    h.set("Accept", "application/json");
    if (opts.authRequired) {
      const token = await keychainGet(KEYCHAIN_KEYS.accessToken);
      if (!token) {
        throw new AuthFatalError(401, "no_access_token", {
          error: "no_access_token",
          message: "No access token available — sign in required.",
        });
      }
      // Proactive refresh if the token is about to expire.
      const expiryIso = await keychainGet(KEYCHAIN_KEYS.accessTokenExpiry);
      if (expiryIso) {
        const expMs = Date.parse(expiryIso);
        if (!Number.isNaN(expMs) && Date.now() > expMs - REFRESH_LEEWAY_MS) {
          const refreshed = await tryRefresh();
          if (refreshed) {
            h.set("Authorization", `Bearer ${refreshed}`);
            return h;
          }
          throw new AuthFatalError(401, "refresh_token_invalid", {
            error: "refresh_token_invalid",
            message: "Session expired and refresh failed.",
          });
        }
      }
      h.set("Authorization", `Bearer ${token}`);
    }
    return h;
  }

  async function doFetch(): Promise<Response> {
    return fetch(`${baseForEngine(opts.engine)}${path}`, {
      method,
      headers: await buildHeaders(),
      body:
        opts.body === undefined
          ? undefined
          : isMultipart
          ? (opts.body as FormData)
          : JSON.stringify(opts.body),
      cache: "no-store",
    });
  }

  let resp = await doFetch();

  // Reactive refresh on 401 (covers tokens that expire mid-flight).
  if (resp.status === 401 && opts.authRequired) {
    const body = (await resp.clone().json().catch(() => ({}))) as ApiError;
    const code = body?.detail?.error || "unauthorized";
    if (AUTH_FATAL_CODES.has(code)) {
      await keychainClearAll();
      throw new AuthFatalError(401, code, body.detail);
    }
    const refreshed = await tryRefresh();
    if (refreshed) {
      resp = await doFetch();
    } else {
      throw new AuthFatalError(401, "session_expired", {
        error: "session_expired",
        message: "Your session has expired. Please sign in again.",
      });
    }
  }

  if (!resp.ok) {
    const body = (await resp.json().catch(() => ({}))) as ApiError;
    const code = body?.detail?.error || `http_${resp.status}`;
    const message = body?.detail?.message || resp.statusText;
    if (AUTH_FATAL_CODES.has(code)) {
      await keychainClearAll();
      throw new AuthFatalError(resp.status, code, body.detail);
    }
    throw new ApiClientError(message, resp.status, code, body.detail || {
      error: code,
      message,
    });
  }

  // Some endpoints (logout) return empty body — guard.
  const text = await resp.text();
  if (!text) return undefined as T;
  return JSON.parse(text) as T;
}

// ─────────────────────────────────────────────────────────────
// Engine-bound clients — the ONLY public way to make a request.
// The engine (and therefore the base URL) is FIXED at construction; the
// method signatures omit `engine`, and createEngineClient spreads it LAST,
// so a call site physically cannot reach the other backend. This is the
// structural no-cross-contamination guarantee for the dual-context UI.
// ─────────────────────────────────────────────────────────────

export type EngineRequestOptions = Omit<RequestOptions, "engine">;
type BodylessOptions = Omit<EngineRequestOptions, "method" | "body">;

export interface EngineClient {
  readonly engine: ApiEngine;
  readonly baseUrl: string;
  call<T>(path: string, opts?: EngineRequestOptions): Promise<T>;
  get<T>(path: string, opts?: BodylessOptions): Promise<T>;
  post<T>(path: string, body?: unknown, opts?: BodylessOptions): Promise<T>;
  put<T>(path: string, body?: unknown, opts?: BodylessOptions): Promise<T>;
  patch<T>(path: string, body?: unknown, opts?: BodylessOptions): Promise<T>;
  del<T>(path: string, opts?: BodylessOptions): Promise<T>;
}

export function createEngineClient(engine: ApiEngine): EngineClient {
  // `engine` is spread LAST so a stray caller-supplied engine can never win.
  const bound = <T>(path: string, opts: EngineRequestOptions = {}): Promise<T> =>
    call<T>(path, { ...opts, engine });
  return {
    engine,
    baseUrl: baseForEngine(engine),
    call: bound,
    get: (p, o) => bound(p, { ...o, method: "GET" }),
    post: (p, b, o) => bound(p, { ...o, method: "POST", body: b }),
    put: (p, b, o) => bound(p, { ...o, method: "PUT", body: b }),
    patch: (p, b, o) => bound(p, { ...o, method: "PATCH", body: b }),
    del: (p, o) => bound(p, { ...o, method: "DELETE" }),
  };
}

/** Use these — never a raw cross-engine call. M1 = tax/billing/compliance, M2 = AI core/native. */
export const apiM1: EngineClient = createEngineClient("m1");
export const apiM2: EngineClient = createEngineClient("m2");

// ─────────────────────────────────────────────────────────────
// Public API surface (M1 accountant endpoints — routed via apiM1)
// ─────────────────────────────────────────────────────────────

export const api = {
  /** Send an OTP to the supplied email. Anti-enumeration on backend. */
  async otpSend(req: OtpSendRequest): Promise<OtpSendResponse> {
    if (USE_MOCK) {
      const { mockOtpSend } = await import("./mock");
      return mockOtpSend(req);
    }
    return call<OtpSendResponse>("/api/v1/accountant/otp/send", {
      method: "POST",
      body: req,
    });
  },

  /** Verify OTP, register device, mint tokens. Persists to keychain. */
  async otpVerify(req: OtpVerifyRequest): Promise<OtpVerifyResponse> {
    if (USE_MOCK) {
      const { mockOtpVerify } = await import("./mock");
      const data = await mockOtpVerify(req);
      await persistTokens(data);
      return data;
    }
    const data = await call<OtpVerifyResponse>("/api/v1/accountant/otp/verify", {
      method: "POST",
      body: req,
    });
    await persistTokens(data);
    return data;
  },

  /** Email + password sign-in. Same response shape as otpVerify; persists tokens. */
  async login(req: LoginRequest): Promise<OtpVerifyResponse> {
    const data = await call<OtpVerifyResponse>("/api/v1/accountant/login", {
      method: "POST",
      body: req,
    });
    await persistTokens(data);
    return data;
  },

  /** Request a password-reset code by email. Anti-enumeration on backend. */
  async forgotPassword(email: string): Promise<ForgotPasswordResponse> {
    return call<ForgotPasswordResponse>("/api/v1/accountant/forgot-password", {
      method: "POST",
      body: { email },
    });
  },

  /** Complete a password reset with the emailed code. */
  async resetPassword(req: ResetPasswordRequest): Promise<OkResponse> {
    return call<OkResponse>("/api/v1/accountant/reset-password", {
      method: "POST",
      body: req,
    });
  },

  /** Change password while signed in (revokes other devices' sessions). */
  async changePassword(req: { old_password: string; new_password: string }): Promise<OkResponse> {
    return call<OkResponse>("/api/v1/accountant/change-password", {
      method: "POST",
      body: req,
      authRequired: true,
    });
  },

  // ── Accountant data (Phase 2 — dashboard + clients, all M1) ──

  /** Per-engaged-org grid: invoice_count, outstanding, review-queue, last activity. */
  async getAccountantBook(): Promise<AccountantBook> {
    return call<AccountantBook>("/api/v1/accountant/book", { authRequired: true });
  },

  /** This-month P&L + expenses-by-category + VAT for one client org. */
  async getOrgSummary(orgId: number): Promise<OrgSummary> {
    return call<OrgSummary>(`/api/v1/accountant/orgs/${orgId}/summary`, { authRequired: true });
  },

  /** Export history (uniform_file / hashavshevet) for one client org. */
  async getOrgExports(orgId: number): Promise<ExportsList> {
    return call<ExportsList>(`/api/v1/accountant/orgs/${orgId}/exports`, { authRequired: true });
  },

  /** Accountant revenue-share earnings, incl. last-12-months trend. */
  async getEarnings(): Promise<Earnings> {
    return call<Earnings>("/api/v1/accountant/earnings", { authRequired: true });
  },

  /** The signed-in accountant's editable profile. */
  async getProfile(): Promise<AccountantUser> {
    return call<AccountantUser>("/api/v1/accountant/profile", { authRequired: true });
  },

  /** Update display name and/or firm name; returns the refreshed profile. */
  async updateProfile(req: { name?: string; firm_name?: string }): Promise<AccountantUser> {
    return call<AccountantUser>("/api/v1/accountant/profile", {
      method: "PATCH",
      body: req,
      authRequired: true,
    });
  },

  /** Manual refresh — usually unneeded, the client refreshes proactively. */
  async refresh(): Promise<string | null> {
    return tryRefresh();
  },

  async logout(): Promise<LogoutResponse> {
    const refreshToken = await keychainGet(KEYCHAIN_KEYS.refreshToken);
    const req: LogoutRequest = { refresh_token: refreshToken || undefined };
    try {
      const data = await call<LogoutResponse>("/api/v1/accountant/logout", {
        method: "POST",
        body: req,
      });
      return data;
    } finally {
      await keychainClearAll();
    }
  },

  async listDevices(): Promise<DeviceListResponse> {
    return call<DeviceListResponse>("/api/v1/accountant/devices", {
      authRequired: true,
    });
  },

  async revokeDevice(
    deviceId: number,
    req: DeviceRevokeRequest = {},
  ): Promise<DeviceRevokeResponse> {
    return call<DeviceRevokeResponse>(
      `/api/v1/accountant/devices/${deviceId}/revoke`,
      { method: "POST", body: req, authRequired: true },
    );
  },

  async relabelDevice(
    deviceId: number,
    req: DeviceRelabelRequest,
  ): Promise<DeviceRelabelResponse> {
    return call<DeviceRelabelResponse>(
      `/api/v1/accountant/devices/${deviceId}/relabel`,
      { method: "POST", body: req, authRequired: true },
    );
  },

  /** P1-16 — Accountant dashboard KPI cards. */
  async getDashboardKpis(): Promise<DashboardKpisResponse> {
    return call<DashboardKpisResponse>("/api/v1/accountant/dashboard/kpis", {
      authRequired: true,
    });
  },
};

// P1-16 — KPI response shape.
export interface DashboardKpisResponse {
  vault_docs_this_month: number;
  active_clients: number;
  active_devices: number;
  security_status: "ok" | "warning" | "critical";
}

// ─────────────────────────────────────────────────────────────
// Token persistence helper
// ─────────────────────────────────────────────────────────────

async function persistTokens(data: OtpVerifyResponse): Promise<void> {
  await keychainSet(KEYCHAIN_KEYS.accessToken, data.access_token);
  await keychainSet(KEYCHAIN_KEYS.accessTokenExpiry, data.access_token_expires_at);
  await keychainSet(KEYCHAIN_KEYS.refreshToken, data.refresh_token);
  await keychainSet(KEYCHAIN_KEYS.refreshTokenExpiry, data.refresh_token_expires_at);
  await keychainSet(KEYCHAIN_KEYS.userId, String(data.user.id));
  await keychainSet(KEYCHAIN_KEYS.userEmail, data.user.email);
  await keychainSet(KEYCHAIN_KEYS.userName, data.user.name);
  await keychainSet(KEYCHAIN_KEYS.deviceId, String(data.device_id));
}

export { API_BASE, M1_TAX_URL, M2_CORE_URL, USE_MOCK };
