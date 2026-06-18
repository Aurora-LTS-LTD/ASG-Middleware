/**
 * Aurora Accountant Portal — Mock API layer.
 *
 * Activated via NEXT_PUBLIC_USE_MOCK_API=true. Lets Phase 2 frontend
 * iteration proceed against deterministic mocks before the real
 * backend (Phase 21) lands. Once real backend is live, flip the env
 * flag to "false" or unset it.
 *
 * Magic values for testing:
 *   • OTP: enter "123456" for success; anything else → otp_invalid
 *     with attempts_remaining decrement
 *   • Email: any valid email → mocks success at /otp/send
 *   • Email "locked@test.com" → simulates lockout state
 */

import type {
  OtpSendRequest, OtpSendResponse,
  OtpVerifyRequest, OtpVerifyResponse,
  DeviceListResponse,
} from "@/types/api";
import type {
  ClientDocument, ListDocumentsFilters, ListDocumentsResponse,
  IngestionAddressResponse, ManualUploadResponse, MockClient,
  ReclassifyRequest, ReclassifyResponse,
} from "@/types/vault";

const VALID_OTP = "123456";

const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));

function maskEmail(email: string): string {
  const [local, domain] = email.split("@");
  if (!local || !domain) return "***@***";
  const parts = domain.split(".");
  return `${local[0]}***@${parts[0][0]}***.${parts.slice(1).join(".")}`;
}

let mockAttemptCount = 0;

// ─────────────────────────────────────────────────────────────
// Auth mocks
// ─────────────────────────────────────────────────────────────

export async function mockOtpSend(req: OtpSendRequest): Promise<OtpSendResponse> {
  await delay(300);
  if (req.email === "locked@test.com") {
    throw {
      status: 429,
      body: {
        detail: {
          error: "otp_rate_limited",
          message: "Too many OTP requests. Try again in 15 minutes.",
          retry_after_seconds: 900,
        },
      },
    };
  }
  mockAttemptCount = 0;
  return {
    ok: true,
    sent_to: maskEmail(req.email),
    expires_in_seconds: 60,
    method: "email",
  };
}

export async function mockOtpVerify(req: OtpVerifyRequest): Promise<OtpVerifyResponse> {
  await delay(500);
  if (req.otp !== VALID_OTP) {
    mockAttemptCount += 1;
    const remaining = Math.max(0, 3 - mockAttemptCount);
    if (remaining === 0) {
      throw {
        status: 401,
        body: { detail: { error: "otp_locked", message: "Too many wrong attempts. Locked for 15 minutes.", retry_after_seconds: 900 } },
      };
    }
    throw {
      status: 401,
      body: { detail: { error: "otp_invalid", message: `OTP is incorrect. ${remaining} attempts remaining.`, attempts_remaining: remaining } },
    };
  }
  mockAttemptCount = 0;
  const now = Date.now();
  return {
    ok: true,
    access_token: "mock_jwt_" + crypto.randomUUID().replace(/-/g, ""),
    refresh_token: "rt_mock_" + crypto.randomUUID().replace(/-/g, ""),
    access_token_expires_at: new Date(now + 15 * 60 * 1000).toISOString(),
    refresh_token_expires_at: new Date(now + 30 * 24 * 60 * 60 * 1000).toISOString(),
    device_id: 42,
    is_new_device: true,
    user: {
      id: 17,
      email: req.email,
      name: "Ibrahim Masarwa",
      role: "accountant",
      firm_name: "Masarwa & Partners CPA",
      license_number: null,
    },
  };
}

export async function mockListDevices(): Promise<DeviceListResponse> {
  await delay(150);
  const now = new Date().toISOString();
  const weekAgo = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString();
  const monthAgo = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString();
  return {
    devices: [
      {
        id: 42,
        device_fingerprint_preview: "a3f7c2e1d4b5a6c7…",
        platform: "macos",
        device_label: "MacBook Pro 14 (Office)",
        enrolled_at: weekAgo,
        last_seen_at: now,
        use_count: 47,
        is_current_device: true,
        ip_geo_hint: "Tel Aviv, IL",
      },
      {
        id: 38,
        device_fingerprint_preview: "b9e1d3f2a5c4b7e8…",
        platform: "windows",
        device_label: "HP EliteBook (Home)",
        enrolled_at: monthAgo,
        last_seen_at: new Date(Date.now() - 3 * 24 * 60 * 60 * 1000).toISOString(),
        use_count: 12,
        is_current_device: false,
        ip_geo_hint: "Haifa, IL",
      },
    ],
  };
}

// ─────────────────────────────────────────────────────────────
// Mock clients (used by the vault client selector)
// ─────────────────────────────────────────────────────────────

export const MOCK_CLIENTS: MockClient[] = [
  { id: 101, name: "Cohen Brothers Automotive",    industry: "Auto Services"  },
  { id: 102, name: "Kfar Yona Restaurant",          industry: "Food & Beverage" },
  { id: 103, name: "BuildTech Construction Ltd.",   industry: "Construction"    },
];

// ─────────────────────────────────────────────────────────────
// Mock document corpus — 30 high-fidelity entries
// ─────────────────────────────────────────────────────────────

function iso(daysAgo: number, hours = 0): string {
  return new Date(Date.now() - daysAgo * 86_400_000 - hours * 3_600_000).toISOString();
}

function archived(created: string): string {
  const d = new Date(created);
  d.setFullYear(d.getFullYear() + 7);
  return d.toISOString();
}

function sha(): string {
  return Array.from({ length: 64 }, () => "0123456789abcdef"[Math.floor(Math.random() * 16)]).join("");
}

function key(agencyId: number, clientId: number, year: number, type: string, name: string): string {
  return `vault/agency_${agencyId}/client_${clientId}/${year}/${type}/${crypto.randomUUID()}-${name}`;
}

const ALL_DOCS: ClientDocument[] = [
  // ── Client 101: Cohen Brothers Automotive ──────────────────
  { id: 1001, agency_id: 1, client_id: 101, uploaded_by_vector: "whatsapp", document_type: "expense",
    file_name: "fuel_invoice_may26.jpg", mime_type: "image/jpeg", size_bytes: 284_000,
    sha256: sha(), s3_key: key(1,101,2026,"expense","fuel_invoice_may26.jpg"),
    sender_phone_e164: "+972501234567", sender_email: null, extracted_metadata: null,
    tax_year: 2026, status: "classified", error_reason: null,
    created_at: iso(1, 9), archived_until: archived(iso(1, 9)) },

  { id: 1002, agency_id: 1, client_id: 101, uploaded_by_vector: "email", document_type: "expense",
    file_name: "parts_supplier_invoice_apr26.pdf", mime_type: "application/pdf", size_bytes: 512_000,
    sha256: sha(), s3_key: key(1,101,2026,"expense","parts_supplier_invoice_apr26.pdf"),
    sender_phone_e164: null, sender_email: "cohen.brothers@gmail.com", extracted_metadata: null,
    tax_year: 2026, status: "classified", error_reason: null,
    created_at: iso(22), archived_until: archived(iso(22)) },

  { id: 1003, agency_id: 1, client_id: 101, uploaded_by_vector: "whatsapp", document_type: "revenue",
    file_name: "service_receipt_bmw_001.jpg", mime_type: "image/jpeg", size_bytes: 198_000,
    sha256: sha(), s3_key: key(1,101,2026,"revenue","service_receipt_bmw_001.jpg"),
    sender_phone_e164: "+972501234567", sender_email: null, extracted_metadata: null,
    tax_year: 2026, status: "scanning", error_reason: null,
    created_at: iso(0, 3), archived_until: archived(iso(0, 3)) },

  { id: 1004, agency_id: 1, client_id: 101, uploaded_by_vector: "email", document_type: "statement",
    file_name: "bank_leumi_apr26.pdf", mime_type: "application/pdf", size_bytes: 740_000,
    sha256: sha(), s3_key: key(1,101,2026,"statement","bank_leumi_apr26.pdf"),
    sender_phone_e164: null, sender_email: "cohen.brothers@gmail.com", extracted_metadata: null,
    tax_year: 2026, status: "classified", error_reason: null,
    created_at: iso(30), archived_until: archived(iso(30)) },

  { id: 1005, agency_id: 1, client_id: 101, uploaded_by_vector: "manual", document_type: "expense",
    file_name: "rent_agreement_2025.pdf", mime_type: "application/pdf", size_bytes: 380_000,
    sha256: sha(), s3_key: key(1,101,2025,"expense","rent_agreement_2025.pdf"),
    sender_phone_e164: null, sender_email: null, extracted_metadata: null,
    tax_year: 2025, status: "classified", error_reason: null,
    created_at: iso(210), archived_until: archived(iso(210)) },

  { id: 1006, agency_id: 1, client_id: 101, uploaded_by_vector: "whatsapp", document_type: "unclassified",
    file_name: "photo_20260523.jpg", mime_type: "image/jpeg", size_bytes: 142_000,
    sha256: sha(), s3_key: key(1,101,2026,"unclassified","photo_20260523.jpg"),
    sender_phone_e164: "+972501234567", sender_email: null, extracted_metadata: null,
    tax_year: 2026, status: "received", error_reason: null,
    created_at: iso(2, 1), archived_until: archived(iso(2, 1)) },

  { id: 1007, agency_id: 1, client_id: 101, uploaded_by_vector: "email", document_type: "expense",
    file_name: "electricity_mar26.pdf", mime_type: "application/pdf", size_bytes: 225_000,
    sha256: sha(), s3_key: key(1,101,2026,"expense","electricity_mar26.pdf"),
    sender_phone_e164: null, sender_email: "cohen.brothers@gmail.com", extracted_metadata: null,
    tax_year: 2026, status: "classified", error_reason: null,
    created_at: iso(60), archived_until: archived(iso(60)) },

  { id: 1008, agency_id: 1, client_id: 101, uploaded_by_vector: "whatsapp", document_type: "expense",
    file_name: "telecom_feb26.jpg", mime_type: "image/jpeg", size_bytes: 168_000,
    sha256: sha(), s3_key: key(1,101,2026,"expense","telecom_feb26.jpg"),
    sender_phone_e164: "+972501234567", sender_email: null, extracted_metadata: null,
    tax_year: 2026, status: "classified", error_reason: null,
    created_at: iso(90), archived_until: archived(iso(90)) },

  { id: 1009, agency_id: 1, client_id: 101, uploaded_by_vector: "email", document_type: "statement",
    file_name: "bank_hapoalim_q1_2026.pdf", mime_type: "application/pdf", size_bytes: 910_000,
    sha256: sha(), s3_key: key(1,101,2026,"statement","bank_hapoalim_q1_2026.pdf"),
    sender_phone_e164: null, sender_email: "cohen.brothers@gmail.com", extracted_metadata: null,
    tax_year: 2026, status: "classified", error_reason: null,
    created_at: iso(45), archived_until: archived(iso(45)) },

  { id: 1010, agency_id: 1, client_id: 101, uploaded_by_vector: "manual", document_type: "expense",
    file_name: "insurance_annual_2025.pdf", mime_type: "application/pdf", size_bytes: 450_000,
    sha256: sha(), s3_key: key(1,101,2025,"expense","insurance_annual_2025.pdf"),
    sender_phone_e164: null, sender_email: null, extracted_metadata: null,
    tax_year: 2025, status: "classified", error_reason: null,
    created_at: iso(300), archived_until: archived(iso(300)) },

  // ── Client 102: Kfar Yona Restaurant ──────────────────────
  { id: 2001, agency_id: 1, client_id: 102, uploaded_by_vector: "whatsapp", document_type: "expense",
    file_name: "produce_supplier_may26.jpg", mime_type: "image/jpeg", size_bytes: 320_000,
    sha256: sha(), s3_key: key(1,102,2026,"expense","produce_supplier_may26.jpg"),
    sender_phone_e164: "+972526789012", sender_email: null, extracted_metadata: null,
    tax_year: 2026, status: "classified", error_reason: null,
    created_at: iso(3), archived_until: archived(iso(3)) },

  { id: 2002, agency_id: 1, client_id: 102, uploaded_by_vector: "email", document_type: "revenue",
    file_name: "z_report_may_wk1.pdf", mime_type: "application/pdf", size_bytes: 180_000,
    sha256: sha(), s3_key: key(1,102,2026,"revenue","z_report_may_wk1.pdf"),
    sender_phone_e164: null, sender_email: "kfaryona.rest@gmail.com", extracted_metadata: null,
    tax_year: 2026, status: "classified", error_reason: null,
    created_at: iso(20), archived_until: archived(iso(20)) },

  { id: 2003, agency_id: 1, client_id: 102, uploaded_by_vector: "whatsapp", document_type: "expense",
    file_name: "gas_invoice_apr26.jpg", mime_type: "image/jpeg", size_bytes: 240_000,
    sha256: sha(), s3_key: key(1,102,2026,"expense","gas_invoice_apr26.jpg"),
    sender_phone_e164: "+972526789012", sender_email: null, extracted_metadata: null,
    tax_year: 2026, status: "classified", error_reason: null,
    created_at: iso(25), archived_until: archived(iso(25)) },

  { id: 2004, agency_id: 1, client_id: 102, uploaded_by_vector: "email", document_type: "statement",
    file_name: "discount_bank_apr26.pdf", mime_type: "application/pdf", size_bytes: 680_000,
    sha256: sha(), s3_key: key(1,102,2026,"statement","discount_bank_apr26.pdf"),
    sender_phone_e164: null, sender_email: "kfaryona.rest@gmail.com", extracted_metadata: null,
    tax_year: 2026, status: "classified", error_reason: null,
    created_at: iso(28), archived_until: archived(iso(28)) },

  { id: 2005, agency_id: 1, client_id: 102, uploaded_by_vector: "whatsapp", document_type: "unclassified",
    file_name: "img_20260521_174301.jpg", mime_type: "image/jpeg", size_bytes: 155_000,
    sha256: sha(), s3_key: key(1,102,2026,"unclassified","img_20260521_174301.jpg"),
    sender_phone_e164: "+972526789012", sender_email: null, extracted_metadata: null,
    tax_year: 2026, status: "error", error_reason: "ocr_confidence_too_low",
    created_at: iso(4), archived_until: archived(iso(4)) },

  { id: 2006, agency_id: 1, client_id: 102, uploaded_by_vector: "email", document_type: "expense",
    file_name: "cleaning_service_mar26.pdf", mime_type: "application/pdf", size_bytes: 195_000,
    sha256: sha(), s3_key: key(1,102,2026,"expense","cleaning_service_mar26.pdf"),
    sender_phone_e164: null, sender_email: "kfaryona.rest@gmail.com", extracted_metadata: null,
    tax_year: 2026, status: "classified", error_reason: null,
    created_at: iso(55), archived_until: archived(iso(55)) },

  { id: 2007, agency_id: 1, client_id: 102, uploaded_by_vector: "whatsapp", document_type: "expense",
    file_name: "meat_supplier_may26.jpg", mime_type: "image/jpeg", size_bytes: 310_000,
    sha256: sha(), s3_key: key(1,102,2026,"expense","meat_supplier_may26.jpg"),
    sender_phone_e164: "+972526789012", sender_email: null, extracted_metadata: null,
    tax_year: 2026, status: "scanning", error_reason: null,
    created_at: iso(0, 6), archived_until: archived(iso(0, 6)) },

  { id: 2008, agency_id: 1, client_id: 102, uploaded_by_vector: "manual", document_type: "revenue",
    file_name: "z_report_annual_2025.pdf", mime_type: "application/pdf", size_bytes: 620_000,
    sha256: sha(), s3_key: key(1,102,2025,"revenue","z_report_annual_2025.pdf"),
    sender_phone_e164: null, sender_email: null, extracted_metadata: null,
    tax_year: 2025, status: "classified", error_reason: null,
    created_at: iso(150), archived_until: archived(iso(150)) },

  { id: 2009, agency_id: 1, client_id: 102, uploaded_by_vector: "email", document_type: "expense",
    file_name: "equipment_lease_2025.pdf", mime_type: "application/pdf", size_bytes: 490_000,
    sha256: sha(), s3_key: key(1,102,2025,"expense","equipment_lease_2025.pdf"),
    sender_phone_e164: null, sender_email: "kfaryona.rest@gmail.com", extracted_metadata: null,
    tax_year: 2025, status: "classified", error_reason: null,
    created_at: iso(200), archived_until: archived(iso(200)) },

  { id: 2010, agency_id: 1, client_id: 102, uploaded_by_vector: "whatsapp", document_type: "expense",
    file_name: "beverage_invoice_jan26.jpg", mime_type: "image/jpeg", size_bytes: 275_000,
    sha256: sha(), s3_key: key(1,102,2026,"expense","beverage_invoice_jan26.jpg"),
    sender_phone_e164: "+972526789012", sender_email: null, extracted_metadata: null,
    tax_year: 2026, status: "classified", error_reason: null,
    created_at: iso(130), archived_until: archived(iso(130)) },

  // ── Client 103: BuildTech Construction Ltd. ───────────────
  { id: 3001, agency_id: 1, client_id: 103, uploaded_by_vector: "email", document_type: "revenue",
    file_name: "project_invoice_haifa_port.pdf", mime_type: "application/pdf", size_bytes: 840_000,
    sha256: sha(), s3_key: key(1,103,2026,"revenue","project_invoice_haifa_port.pdf"),
    sender_phone_e164: null, sender_email: "buildtech@buildtech.co.il", extracted_metadata: null,
    tax_year: 2026, status: "classified", error_reason: null,
    created_at: iso(10), archived_until: archived(iso(10)) },

  { id: 3002, agency_id: 1, client_id: 103, uploaded_by_vector: "whatsapp", document_type: "expense",
    file_name: "cement_delivery_may26.jpg", mime_type: "image/jpeg", size_bytes: 372_000,
    sha256: sha(), s3_key: key(1,103,2026,"expense","cement_delivery_may26.jpg"),
    sender_phone_e164: "+972548901234", sender_email: null, extracted_metadata: null,
    tax_year: 2026, status: "classified", error_reason: null,
    created_at: iso(5), archived_until: archived(iso(5)) },

  { id: 3003, agency_id: 1, client_id: 103, uploaded_by_vector: "email", document_type: "statement",
    file_name: "mizrahi_bank_may26.pdf", mime_type: "application/pdf", size_bytes: 1_100_000,
    sha256: sha(), s3_key: key(1,103,2026,"statement","mizrahi_bank_may26.pdf"),
    sender_phone_e164: null, sender_email: "buildtech@buildtech.co.il", extracted_metadata: null,
    tax_year: 2026, status: "classified", error_reason: null,
    created_at: iso(8), archived_until: archived(iso(8)) },

  { id: 3004, agency_id: 1, client_id: 103, uploaded_by_vector: "whatsapp", document_type: "expense",
    file_name: "scaffolding_rental_apr26.jpg", mime_type: "image/jpeg", size_bytes: 290_000,
    sha256: sha(), s3_key: key(1,103,2026,"expense","scaffolding_rental_apr26.jpg"),
    sender_phone_e164: "+972548901234", sender_email: null, extracted_metadata: null,
    tax_year: 2026, status: "scanning", error_reason: null,
    created_at: iso(0, 1), archived_until: archived(iso(0, 1)) },

  { id: 3005, agency_id: 1, client_id: 103, uploaded_by_vector: "manual", document_type: "expense",
    file_name: "subcontractor_agreement_2026.pdf", mime_type: "application/pdf", size_bytes: 760_000,
    sha256: sha(), s3_key: key(1,103,2026,"expense","subcontractor_agreement_2026.pdf"),
    sender_phone_e164: null, sender_email: null, extracted_metadata: null,
    tax_year: 2026, status: "classified", error_reason: null,
    created_at: iso(40), archived_until: archived(iso(40)) },

  { id: 3006, agency_id: 1, client_id: 103, uploaded_by_vector: "email", document_type: "revenue",
    file_name: "project_invoice_tlv_mall.pdf", mime_type: "application/pdf", size_bytes: 920_000,
    sha256: sha(), s3_key: key(1,103,2026,"revenue","project_invoice_tlv_mall.pdf"),
    sender_phone_e164: null, sender_email: "buildtech@buildtech.co.il", extracted_metadata: null,
    tax_year: 2026, status: "classified", error_reason: null,
    created_at: iso(35), archived_until: archived(iso(35)) },

  { id: 3007, agency_id: 1, client_id: 103, uploaded_by_vector: "whatsapp", document_type: "expense",
    file_name: "steel_beams_invoice_mar26.jpg", mime_type: "image/jpeg", size_bytes: 415_000,
    sha256: sha(), s3_key: key(1,103,2026,"expense","steel_beams_invoice_mar26.jpg"),
    sender_phone_e164: "+972548901234", sender_email: null, extracted_metadata: null,
    tax_year: 2026, status: "classified", error_reason: null,
    created_at: iso(62), archived_until: archived(iso(62)) },

  { id: 3008, agency_id: 1, client_id: 103, uploaded_by_vector: "email", document_type: "statement",
    file_name: "discount_bank_q1_2026.pdf", mime_type: "application/pdf", size_bytes: 980_000,
    sha256: sha(), s3_key: key(1,103,2026,"statement","discount_bank_q1_2026.pdf"),
    sender_phone_e164: null, sender_email: "buildtech@buildtech.co.il", extracted_metadata: null,
    tax_year: 2026, status: "classified", error_reason: null,
    created_at: iso(70), archived_until: archived(iso(70)) },

  { id: 3009, agency_id: 1, client_id: 103, uploaded_by_vector: "whatsapp", document_type: "unclassified",
    file_name: "photo_site_inspection.jpg", mime_type: "image/jpeg", size_bytes: 520_000,
    sha256: sha(), s3_key: key(1,103,2026,"unclassified","photo_site_inspection.jpg"),
    sender_phone_e164: "+972548901234", sender_email: null, extracted_metadata: null,
    tax_year: 2026, status: "received", error_reason: null,
    created_at: iso(0, 2), archived_until: archived(iso(0, 2)) },

  { id: 3010, agency_id: 1, client_id: 103, uploaded_by_vector: "email", document_type: "expense",
    file_name: "architect_fee_2025.pdf", mime_type: "application/pdf", size_bytes: 635_000,
    sha256: sha(), s3_key: key(1,103,2025,"expense","architect_fee_2025.pdf"),
    sender_phone_e164: null, sender_email: "buildtech@buildtech.co.il", extracted_metadata: null,
    tax_year: 2025, status: "classified", error_reason: null,
    created_at: iso(240), archived_until: archived(iso(240)) },
];

// ─────────────────────────────────────────────────────────────
// Vault mock handlers
// ─────────────────────────────────────────────────────────────

export async function mockListDocuments(
  clientId: number,
  filters: ListDocumentsFilters = {},
): Promise<ListDocumentsResponse> {
  await delay(200);

  let docs = ALL_DOCS.filter((d) => d.client_id === clientId);

  if (filters.tax_year != null)        docs = docs.filter((d) => d.tax_year === filters.tax_year);
  if (filters.document_type)           docs = docs.filter((d) => d.document_type === filters.document_type);
  if (filters.uploaded_by_vector)      docs = docs.filter((d) => d.uploaded_by_vector === filters.uploaded_by_vector);
  if (filters.status)                  docs = docs.filter((d) => d.status === filters.status);

  // newest first
  docs = [...docs].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());

  const page = filters.page ?? 1;
  const pageSize = filters.page_size ?? 20;
  const total = docs.length;
  const paged = docs.slice((page - 1) * pageSize, page * pageSize);

  return { documents: paged, total, page, page_size: pageSize };
}

export async function mockGetDocument(
  documentId: number,
): Promise<{ document: ClientDocument; download_url: string }> {
  await delay(100);
  const doc = ALL_DOCS.find((d) => d.id === documentId);
  if (!doc) throw new Error("Document not found");
  return { document: doc, download_url: `https://mock-vault.aurora.local/download/${documentId}` };
}

const INGESTION_ADDRESSES: Record<number, { token: string; whatsapp: string | null }> = {
  101: { token: "coh101xk",  whatsapp: "+972501234567" },
  102: { token: "kfy102ab",  whatsapp: "+972526789012" },
  103: { token: "bld103zr",  whatsapp: "+972548901234" },
};

export async function mockGetIngestionAddress(clientId: number): Promise<IngestionAddressResponse> {
  await delay(100);
  const entry = INGESTION_ADDRESSES[clientId] ?? { token: `cli${clientId}xx`, whatsapp: null };
  return {
    ingestion_address: {
      client_id: clientId,
      email_alias_token: entry.token,
      whatsapp_e164: entry.whatsapp,
      active: true,
    },
    email_full: `docs+${entry.token}@api-aurora-lts.com`,
    whatsapp_display: entry.whatsapp
      ? entry.whatsapp.replace(/(\+972)(\d{2})(\d{3})(\d{4})/, "$1 $2-$3-$4")
      : null,
  };
}

export async function mockUploadManual(
  clientId: number,
  file: File,
  meta: { document_type?: string; tax_year?: number },
): Promise<ManualUploadResponse> {
  await delay(800);
  const now = new Date().toISOString();
  const doc: ClientDocument = {
    id: Math.floor(Math.random() * 90000) + 10000,
    agency_id: 1,
    client_id: clientId,
    uploaded_by_vector: "manual",
    document_type: (meta.document_type as ClientDocument["document_type"]) ?? "unclassified",
    file_name: file.name,
    mime_type: file.type || "application/octet-stream",
    size_bytes: file.size,
    sha256: sha(),
    s3_key: `vault/agency_1/client_${clientId}/${meta.tax_year ?? 2026}/manual/${file.name}`,
    sender_phone_e164: null,
    sender_email: null,
    extracted_metadata: null,
    tax_year: meta.tax_year ?? new Date().getFullYear(),
    status: "received",
    error_reason: null,
    created_at: now,
    archived_until: archived(now),
  };
  // Mirror the live backend ManualUploadResponse shape.
  return { document_id: doc.id, status: doc.status, sha256: doc.sha256, bytes_size: doc.size_bytes };
}

export async function mockReclassify(
  documentId: number,
  req: ReclassifyRequest,
): Promise<ReclassifyResponse> {
  await delay(200);
  const doc = ALL_DOCS.find((d) => d.id === documentId);
  if (!doc) throw new Error("Document not found");
  const updated = {
    ...doc,
    ...(req.document_type && { document_type: req.document_type }),
    ...(req.tax_year != null && { tax_year: req.tax_year }),
    status: "classified" as const,
  };
  return { ok: true, document: updated };
}
