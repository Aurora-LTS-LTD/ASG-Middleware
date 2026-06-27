// Aurora Command Center — API client.
//
// Thin shell rule: the Mac app injects window.AuroraNative.token (the
// device-bound session) at document-start and also adds X-Aurora-Native-Session
// on every fetch. We build ABSOLUTE URLs to the M1 API ourselves so the same
// code works in the shell (file://) and in a browser during `npm run dev`.
//
// Token handling: in-memory ONLY (no localStorage, per security rule). In the
// shell it comes from window.AuroraNative.token; for browser dev you can set
// window.__AURORA_DEV_TOKEN__ in the console.

const API_BASE =
  (typeof window !== "undefined" && window.AuroraNative && window.AuroraNative.apiBase) ||
  "https://api-aurora-lts.com";

function token() {
  if (typeof window === "undefined") return null;
  return (window.AuroraNative && window.AuroraNative.token) || window.__AURORA_DEV_TOKEN__ || null;
}

export class ApiError extends Error {
  constructor(status, body) {
    super((body && (body.message || (body.detail && body.detail.message))) || `HTTP ${status}`);
    this.status = status;
    this.body = body;
  }
}

async function request(path, { method = "GET", body, stepUp } = {}) {
  const headers = { "Content-Type": "application/json" };
  const t = token();
  if (t) headers["Authorization"] = "Bearer " + t; // shell ALSO adds X-Aurora-Native-Session
  if (stepUp) headers["X-Aurora-Step-Up"] = stepUp;

  const res = await fetch(API_BASE + path, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  let data = null;
  try { data = await res.json(); } catch (_) { /* empty body */ }

  if (!res.ok) {
    if (res.status === 401) {
      // Session gone — in the shell, signing out returns to the native login.
      try { window.AuroraNative && window.AuroraNative.signOut && window.AuroraNative.signOut(); } catch (_) {}
    }
    throw new ApiError(res.status, data);
  }
  return data;
}

export const api = {
  // Overview / finance / system
  overview: () => request("/api/v1/admin/overview"),
  financeSummary: () => request("/api/v1/admin/finance/summary"),
  systemHealth: () => request("/api/v1/admin/system/health"),
  systemConfig: () => request("/api/v1/admin/system/config"),
  // Customers
  customers: (qs = "") => request("/api/v1/admin/customers" + (qs ? "?" + qs : "")),
  customer: (id) => request(`/api/v1/admin/customers/${id}`),
  createCustomer: (b) => request("/api/v1/admin/customers", { method: "POST", body: b }),
  editCustomer: (id, b) => request(`/api/v1/admin/customers/${id}`, { method: "PATCH", body: b }),
  suspendCustomer: (id, stepUp) => request(`/api/v1/admin/customers/${id}/suspend`, { method: "POST", stepUp }),
  archiveCustomer: (id, stepUp) => request(`/api/v1/admin/customers/${id}/archive`, { method: "POST", stepUp }),
  notes: (id) => request(`/api/v1/admin/customers/${id}/notes`),
  addNote: (id, b) => request(`/api/v1/admin/customers/${id}/notes`, { method: "POST", body: b }),
  // Pilot + audit
  pilot: () => request("/api/v1/admin/pilot"),
  auditEvents: (qs = "") => request("/api/v1/admin/audit/events" + (qs ? "?" + qs : "")),
  // v3.1 — KYC actions + timeline
  kycApprove: (id, stepUp) => request(`/api/v1/admin/customers/${id}/kyc/approve`, { method: "POST", stepUp }),
  kycReject: (id, reason, stepUp) => request(`/api/v1/admin/customers/${id}/kyc/reject`, { method: "POST", body: { reason }, stepUp }),
  kycRequestDocs: (id, message) => request(`/api/v1/admin/customers/${id}/kyc/request-docs`, { method: "POST", body: { message } }),
  timeline: (id) => request(`/api/v1/admin/customers/${id}/timeline`),
  // v3.1 — Support / tickets
  tickets: (qs = "") => request("/api/v1/admin/tickets" + (qs ? "?" + qs : "")),
  ticket: (id) => request(`/api/v1/admin/tickets/${id}`),
  createTicket: (b) => request("/api/v1/admin/tickets", { method: "POST", body: b }),
  editTicket: (id, b) => request(`/api/v1/admin/tickets/${id}`, { method: "PATCH", body: b }),
  addTicketMessage: (id, b) => request(`/api/v1/admin/tickets/${id}/messages`, { method: "POST", body: b }),
};
