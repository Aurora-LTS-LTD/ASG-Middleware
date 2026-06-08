/**
 * Business Owner Portal — M1 API client.
 *
 * Single-backend (M1 tax/billing). Bearer-token auth from the browser token
 * store; a 401 on an authed call clears the session and raises AuthFatalError
 * so the provider drops to the login screen.
 */
import { getToken, clearSession } from "@/lib/auth/tokenStore";
import type { LoginResponse, MeResponse, Invoice } from "@/types/api";

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
}

async function call<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (opts.authRequired) {
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

  const text = await resp.text();
  const data = text ? JSON.parse(text) : null;

  if (!resp.ok) {
    const raw =
      data && typeof data === "object" && "detail" in data
        ? (data as { detail: unknown }).detail
        : data;
    const detail: ApiErrorDetail =
      typeof raw === "string" ? { message: raw } : (raw as ApiErrorDetail) || {};
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
