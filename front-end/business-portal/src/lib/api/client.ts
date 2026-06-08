/**
 * Business Owner Portal — M1 API client.
 *
 * Single-backend (M1 tax/billing). Bearer-token auth from the browser token
 * store; a 401 on an authed call clears the session and raises AuthFatalError
 * so the provider drops to the login screen.
 */
import { getToken, clearSession } from "@/lib/auth/tokenStore";
import type { LoginResponse, MeResponse, Invoice } from "@/types/api";
import type {
  StartResponse,
  OnboardingStateResponse,
  IdentityPayload,
  OtpSendResponse,
  PlansResponse,
  InitUploadResponse,
  StepResult,
} from "@/types/onboarding";

const M1_BASE = (
  process.env.NEXT_PUBLIC_AURORA_API_BASE || "https://api-aurora-lts.com"
).replace(/\/$/, "");

export interface ApiErrorDetail {
  error?: string;
  message?: string;
  [k: string]: unknown;
}

export class ApiClientError extends Error {
  status: number;
  detail: ApiErrorDetail;
  constructor(status: number, detail: ApiErrorDetail) {
    super(detail?.message || `HTTP ${status}`);
    this.name = "ApiClientError";
    this.status = status;
    this.detail = detail || {};
  }
  get errorCode(): string {
    return this.detail?.error || "unknown";
  }
}

/** Thrown when auth is unrecoverable (no/expired token) → forces re-login. */
export class AuthFatalError extends Error {}

interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  authRequired?: boolean;
  /** Explicit bearer (used by onboarding — its JWT lives outside the main session). */
  bearer?: string;
}

async function call<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (opts.bearer) {
    headers["Authorization"] = `Bearer ${opts.bearer}`;
  } else if (opts.authRequired) {
    const tok = getToken();
    if (!tok) throw new AuthFatalError("No access token");
    headers["Authorization"] = `Bearer ${tok}`;
  }

  const resp = await fetch(`${M1_BASE}${path}`, {
    method: opts.method || "GET",
    headers,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });

  if (resp.status === 401 && opts.authRequired) {
    clearSession();
    throw new AuthFatalError("Session expired");
  }
  // Onboarding (bearer) 401 → token expired; surface so the wizard restarts,
  // but DON'T touch the main session store (onboarding lives outside it).
  if (resp.status === 401 && opts.bearer) {
    throw new AuthFatalError("Onboarding session expired");
  }

  const text = await resp.text();
  // Guard the parse: a gateway/edge can return a non-JSON body (HTML 502/503,
  // plain-text 429). An uncaught SyntaxError here would be misclassified as a
  // generic "Network error" and mask the real, actionable status.
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }

  if (!resp.ok) {
    const raw =
      data && typeof data === "object" && "detail" in data
        ? (data as { detail: unknown }).detail
        : data;
    let detail: ApiErrorDetail;
    if (raw && typeof raw === "object") {
      detail = raw as ApiErrorDetail;
    } else if (typeof raw === "string" && raw.trim()) {
      detail = { message: raw };
    } else {
      // Non-JSON / empty error body → derive a clean, status-aware message.
      detail = { message: `Service unavailable (HTTP ${resp.status}). Please try again.` };
    }
    throw new ApiClientError(resp.status, detail);
  }
  return data as T;
}

export const api = {
  /** Email + password sign-in (existing M1 owner login). */
  login(email: string, password: string): Promise<LoginResponse> {
    return call<LoginResponse>("/api/v1/auth/login", {
      method: "POST",
      body: { email, password },
    });
  },

  /** Current user (used to enrich/validate the session on bootstrap). */
  me(): Promise<MeResponse> {
    return call<MeResponse>("/api/v1/auth/me", { authRequired: true });
  },

  /** The owner's invoices (server-scoped to their business). */
  listInvoices(): Promise<Invoice[]> {
    return call<Invoice[]>("/api/v1/invoices", { authRequired: true });
  },

  getInvoice(id: number): Promise<Invoice> {
    return call<Invoice>(`/api/v1/invoices/${id}`, { authRequired: true });
  },

  /** Void a draft / pending invoice (finalized are tax-locked → 409). */
  cancelInvoice(id: number, reason?: string): Promise<{ message: string; status: string }> {
    return call(`/api/v1/invoices/${id}/cancel`, {
      method: "POST",
      body: { reason },
      authRequired: true,
    });
  },
};

/**
 * Self-service onboarding (registration) surface — M1 /api/v1/onboarding/*.
 * The FSM hands back `current_step` on each submit; the wizard renders that.
 * Every authed call carries the onboarding JWT explicitly via `bearer` (it is
 * NOT the main session token — see lib/onboarding/session.ts).
 */
export const onboarding = {
  /** Public health/config — surfaces which provider backends are live vs. stub. */
  health(): Promise<{ ok: boolean; otp_backend: string; kyc_backend: string; payplus_backend: string; trial_days: number }> {
    return call("/api/v1/onboarding/health");
  },

  /** Public pricing table for the plan step. */
  plans(): Promise<PlansResponse> {
    return call<PlansResponse>("/api/v1/onboarding/plans");
  },

  /** Public — create the user + onboarding session, returns the onboarding JWT. */
  start(email: string, password: string, language_pref: "en" | "he" | "ar" = "en"): Promise<StartResponse> {
    return call<StartResponse>("/api/v1/onboarding/start", {
      method: "POST",
      body: { email, password, language_pref, surface: "web" },
    });
  },

  state(token: string): Promise<OnboardingStateResponse> {
    return call<OnboardingStateResponse>("/api/v1/onboarding/state", { bearer: token });
  },

  identity(token: string, payload: IdentityPayload): Promise<StepResult> {
    return call<StepResult>("/api/v1/onboarding/identity", { method: "POST", body: payload, bearer: token });
  },

  sendOtp(token: string, channel: "email" | "phone", target: string): Promise<OtpSendResponse> {
    return call<OtpSendResponse>(`/api/v1/onboarding/${channel}/send-otp`, {
      method: "POST",
      body: { target, purpose: "signup" },
      bearer: token,
    });
  },

  verifyOtp(token: string, channel: "email" | "phone", target: string, code: string): Promise<StepResult & { verified: boolean }> {
    return call(`/api/v1/onboarding/${channel}/verify-otp`, {
      method: "POST",
      body: { target, code },
      bearer: token,
    });
  },

  initUpload(token: string, document_type: string, mime_type: string, bytes_size: number): Promise<InitUploadResponse> {
    return call<InitUploadResponse>("/api/v1/onboarding/documents/init-upload", {
      method: "POST",
      body: { document_type, mime_type, bytes_size },
      bearer: token,
    });
  },

  /**
   * PUT the raw bytes to the upload URL from initUpload(). In stub mode this is
   * a relative path on M1; in production it's an absolute GCS signed URL (no
   * Authorization header — the signature authorizes it).
   */
  async putBytes(uploadUrl: string, file: File, mimeType: string): Promise<void> {
    const url = uploadUrl.startsWith("http") ? uploadUrl : `${M1_BASE}${uploadUrl}`;
    const resp = await fetch(url, { method: "PUT", headers: { "Content-Type": mimeType }, body: file });
    if (!resp.ok && resp.status !== 204) {
      throw new ApiClientError(resp.status, { message: "Upload failed. Please try again." });
    }
  },

  finalizeUpload(token: string, doc_id: string): Promise<{ doc_id: string; status: string; advanced_to_next_step: boolean }> {
    return call("/api/v1/onboarding/documents/finalize", { method: "POST", body: { doc_id }, bearer: token });
  },

  submitPlan(token: string, plan: string, billing_cycle: string): Promise<StepResult> {
    return call<StepResult>("/api/v1/onboarding/billing/plan", {
      method: "POST",
      body: { plan, billing_cycle },
      bearer: token,
    });
  },

  submitPayment(token: string, kind: "credit_card" | "direct_debit", tokenization_payload: Record<string, unknown>): Promise<StepResult> {
    return call<StepResult>("/api/v1/onboarding/billing/payment-method", {
      method: "POST",
      body: { kind, tokenization_payload },
      bearer: token,
    });
  },

  submitReview(token: string, terms_accepted: boolean, privacy_accepted: boolean): Promise<StepResult> {
    return call<StepResult>("/api/v1/onboarding/review", {
      method: "POST",
      body: { terms_accepted, privacy_accepted },
      bearer: token,
    });
  },

  /** Final commit — creates the Business + Organization + owner Membership, returns a real session JWT. */
  activate(token: string): Promise<{ access_token: string; token_type: string; redirect_to: string }> {
    return call("/api/v1/onboarding/activate", { method: "POST", bearer: token });
  },
};
