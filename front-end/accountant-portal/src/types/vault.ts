/**
 * Vault TypeScript types — mirrors server_files/app/database/models.py
 * ClientDocument + VaultIngestionAddress + related API shapes.
 * No runtime code — pure declarations.
 */

// ─────────────────────────────────────────────────────────────
// Core domain types
// ─────────────────────────────────────────────────────────────

export type UploadVector = "whatsapp" | "email" | "manual";

export type DocumentType = "expense" | "revenue" | "statement" | "unclassified";

export type DocumentStatus = "received" | "scanning" | "classified" | "error" | "quarantined";

export interface ClientDocument {
  id: number;
  agency_id: number;
  client_id: number;
  uploaded_by_vector: UploadVector;
  s3_key: string;
  document_type: DocumentType;
  file_name: string;
  mime_type: string;
  size_bytes: number;
  sha256: string;
  sender_phone_e164: string | null;   // populated for WhatsApp ingest
  sender_email: string | null;        // populated for email ingest
  extracted_metadata: Record<string, unknown> | null;  // OCR result (P1)
  tax_year: number;
  status: DocumentStatus;
  error_reason: string | null;
  created_at: string;                 // ISO 8601
  archived_until: string;             // ISO 8601 — created_at + 7 years
}

export interface VaultIngestionAddress {
  client_id: number;
  email_alias_token: string;          // slug used in docs+{token}@api-aurora-lts.com
  whatsapp_e164: string | null;       // inbound sender phone number
  active: boolean;
}

// ─────────────────────────────────────────────────────────────
// Mock client record (used only by mock layer + client selector)
// ─────────────────────────────────────────────────────────────

export interface MockClient {
  id: number;
  name: string;
  industry: string;
}

// ─────────────────────────────────────────────────────────────
// API request / response shapes
// ─────────────────────────────────────────────────────────────

export interface ListDocumentsFilters {
  tax_year?: number | null;
  document_type?: DocumentType | null;
  uploaded_by_vector?: UploadVector | null;
  status?: DocumentStatus | null;
  page?: number;
  page_size?: number;
}

export interface ListDocumentsResponse {
  documents: ClientDocument[];
  total: number;
  page: number;
  page_size: number;
}

export interface IngestionAddressResponse {
  ingestion_address: VaultIngestionAddress;
  email_full: string;             // docs+{token}@api-aurora-lts.com
  whatsapp_display: string | null;  // formatted phone for display
}

export interface ManualUploadResponse {
  ok: true;
  document: ClientDocument;
}

export interface ReclassifyRequest {
  document_type?: DocumentType;
  tax_year?: number;
}

export interface ReclassifyResponse {
  ok: true;
  document: ClientDocument;
}
