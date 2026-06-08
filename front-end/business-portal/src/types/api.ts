/**
 * Business Owner Portal — API types (M1).
 * v1 auth uses the existing /api/v1/auth/login (email+password, 24h JWT,
 * owner-scoped server-side via the token's business_id). A hardened
 * /api/v1/owner/* flow (short-lived + refresh + device) lands next.
 */

export interface BusinessOwnerUser {
  id: number;
  email: string;
  full_name: string;
  role: string;
  business_id: number | null;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  role: string;
  full_name: string;
  user_id: number;
}

export interface MeResponse {
  id: number;
  email: string;
  full_name: string;
  role: string;
  business_id: number | null;
  is_active: boolean;
  language_pref: string;
}

export type InvoiceStatus =
  | "draft"
  | "pending_allocation"
  | "finalized"
  | "sent"
  | "cancelled";

export interface Invoice {
  id: number;
  business_id: number;
  invoice_number: string;
  beneficiary_name: string;
  beneficiary_tax_id: string | null;
  amount_net: number;
  vat_rate: number;
  vat_amount: number;
  amount_total: number;
  currency: string;
  requires_allocation: number;
  allocation_number: string | null;
  allocation_status: string;
  status: InvoiceStatus;
  description: string | null;
  created_at: string | null;
  finalized_at: string | null;
  // Lifecycle timestamps (phase29) — exposed once invoice_to_dict is extended (batch 4).
  submitted_at?: string | null;
  sent_at?: string | null;
  cancelled_at?: string | null;
  due_date: string | null;
  payment_status: string;
  amount_paid: number;
  pdf_url: string | null;
}

export const TOKEN_KEYS = {
  accessToken: "aurora_owner_access_token",
  user: "aurora_owner_user",
} as const;
