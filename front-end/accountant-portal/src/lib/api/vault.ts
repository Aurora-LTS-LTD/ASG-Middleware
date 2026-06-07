/**
 * Vault API client — Document Vault Engine (Sprint 8.3).
 *
 * All calls route to https://api-aurora-lts.com/api/v1/accountant/vault/*
 * When NEXT_PUBLIC_USE_MOCK_API=true, swaps to the mock layer automatically.
 */

import { apiM1 } from "@/lib/api/client";
import { USE_MOCK } from "@/lib/api/client";
import type {
  ListDocumentsFilters,
  ListDocumentsResponse,
  IngestionAddressResponse,
  ManualUploadResponse,
  ReclassifyRequest,
  ReclassifyResponse,
} from "@/types/vault";

// ─────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────

function buildQuery(filters: ListDocumentsFilters): string {
  const params = new URLSearchParams();
  if (filters.tax_year != null)          params.set("tax_year",           String(filters.tax_year));
  if (filters.document_type)             params.set("document_type",      filters.document_type);
  if (filters.uploaded_by_vector)        params.set("uploaded_by_vector", filters.uploaded_by_vector);
  if (filters.status)                    params.set("status",             filters.status);
  params.set("page",      String(filters.page      ?? 1));
  params.set("page_size", String(filters.page_size ?? 20));
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

// ─────────────────────────────────────────────────────────────
// Public vault API
// ─────────────────────────────────────────────────────────────

export const vaultApi = {
  /**
   * List documents for a client with optional filters + pagination.
   * Requires a valid accountant JWT (authRequired: true).
   */
  async listDocuments(
    clientId: number,
    filters: ListDocumentsFilters = {},
  ): Promise<ListDocumentsResponse> {
    if (USE_MOCK) {
      const { mockListDocuments } = await import("./mock");
      return mockListDocuments(clientId, filters);
    }
    return apiM1.call<ListDocumentsResponse>(
      `/api/v1/accountant/vault/clients/${clientId}/documents${buildQuery(filters)}`,
      { authRequired: true },
    );
  },

  /**
   * Fetch a single document by ID (includes presigned download URL).
   */
  async getDocument(documentId: number): Promise<{ document: import("@/types/vault").ClientDocument; download_url: string }> {
    if (USE_MOCK) {
      const { mockGetDocument } = await import("./mock");
      return mockGetDocument(documentId);
    }
    return apiM1.call(`/api/v1/accountant/vault/documents/${documentId}`, { authRequired: true });
  },

  /**
   * Get the dedicated ingestion email + WhatsApp address for a client.
   * Share these with the SMB so they can email/WhatsApp documents directly.
   */
  async getIngestionAddress(clientId: number): Promise<IngestionAddressResponse> {
    if (USE_MOCK) {
      const { mockGetIngestionAddress } = await import("./mock");
      return mockGetIngestionAddress(clientId);
    }
    return apiM1.call<IngestionAddressResponse>(
      `/api/v1/accountant/vault/clients/${clientId}/ingestion-address`,
      { authRequired: true },
    );
  },

  /**
   * Manual upload: accountant attaches a file directly from the portal.
   * Sends as multipart/form-data.
   */
  async uploadManual(
    clientId: number,
    file: File,
    meta: { document_type?: string; tax_year?: number },
  ): Promise<ManualUploadResponse> {
    if (USE_MOCK) {
      const { mockUploadManual } = await import("./mock");
      return mockUploadManual(clientId, file, meta);
    }
    // Multipart upload routed through apiM1.call<T>() (engine: "m1" — vault is
    // M1-owned). apiM1.call() detects FormData, skips JSON serialisation, and
    // omits Content-Type so the browser sets the multipart boundary.
    // Auth header + proactive token refresh are handled by apiM1.call() itself.
    const form = new FormData();
    form.append("file", file);
    if (meta.document_type) form.append("document_type", meta.document_type);
    if (meta.tax_year != null) form.append("tax_year", String(meta.tax_year));
    return apiM1.call<ManualUploadResponse>(
      `/api/v1/accountant/vault/clients/${clientId}/documents/manual`,
      {
        method: "POST",
        body: form,
        authRequired: true,
      },
    );
  },

  /**
   * Override document_type or tax_year after it was auto-classified (or mis-classified).
   */
  async reclassify(
    documentId: number,
    req: ReclassifyRequest,
  ): Promise<ReclassifyResponse> {
    if (USE_MOCK) {
      const { mockReclassify } = await import("./mock");
      return mockReclassify(documentId, req);
    }
    return apiM1.call<ReclassifyResponse>(
      `/api/v1/accountant/vault/documents/${documentId}/reclassify`,
      { method: "POST", body: req, authRequired: true },
    );
  },
};
