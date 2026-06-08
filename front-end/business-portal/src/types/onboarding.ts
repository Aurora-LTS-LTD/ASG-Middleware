/**
 * Business Owner Portal — self-service onboarding (registration) types.
 * Mirrors the M1 /api/v1/onboarding/* FSM. The wizard is driven by the
 * backend's `current_step`, so step order tracks the server, not the client.
 */

export type OnboardingStep =
  | "identity"
  | "phone_otp"
  | "email_otp"
  | "documents"
  | "plan"
  | "payment_method"
  | "review"
  | "activate"
  | "complete";

export type LegalStructure = "osek_morshe" | "osek_patur" | "chevra_baam";

export interface StartResponse {
  resumed: boolean;
  access_token: string;
  token_type: string;
  user: {
    id: number;
    email: string;
    language_pref: string;
    onboarding_status: string;
    phone_verified_at: string | null;
    email_verified_at: string | null;
  };
  onboarding: {
    state_id: number;
    current_step: OnboardingStep | string;
    completed_steps: string[];
    expires_at: string | null;
  };
}

export interface OnboardingStateResponse {
  state_id: number;
  current_step: OnboardingStep | string;
  completed_steps: string[];
  draft_payload: Record<string, unknown>;
  expires_at: string | null;
  user: {
    id: number;
    email: string;
    phone_verified_at: string | null;
    email_verified_at: string | null;
  };
}

export interface IdentityPayload {
  first_name: string;
  last_name: string;
  legal_structure: LegalStructure;
  tax_id: string;
  display_name: string;
  business_address?: string;
  city?: string;
  postal_code?: string;
  industry_code?: string;
  business_phone?: string;
  business_email?: string;
  website?: string;
  fax?: string;
}

export interface OtpSendResponse {
  /** Present ONLY when OTP_BACKEND=stub — surfaced in-UI so test journeys can complete. */
  dev_only_code?: string;
  channel?: string;
  target?: string;
  expires_in?: number;
  [k: string]: unknown;
}

export interface PlanOption {
  plan: "starter" | "pro" | "enterprise";
  billing_cycle: "monthly" | "quarterly" | "annual";
  cycle_amount_minor_units: number;
  vat_amount_minor_units: number;
  total_with_vat_minor_units: number;
  discount_pct: number;
  currency: string;
}

export interface PlansResponse {
  plans: PlanOption[];
  trial_days: number;
  vat_rate_pct: number;
}

export interface InitUploadResponse {
  doc_id: string;
  upload_url: string;
  upload_method: string;
  expires_in: number;
  headers: Record<string, string>;
}

export interface StepResult {
  current_step: OnboardingStep | string;
  [k: string]: unknown;
}

/**
 * Required KYC document types per legal structure.
 * MIRRORED from M1 kyc_service.REQUIRED_DOC_TYPES_BY_LEGAL_STRUCTURE — kept in
 * sync manually for now; a GET /onboarding/required-docs endpoint would let the
 * UI fetch this instead (tracked as a follow-up).
 */
export const REQUIRED_DOCS_BY_STRUCTURE: Record<LegalStructure, string[]> = {
  osek_morshe: ["israeli_id_front", "israeli_id_back", "business_certificate"],
  osek_patur: ["israeli_id_front", "israeli_id_back", "business_certificate"],
  chevra_baam: ["israeli_id_front", "israeli_id_back", "company_registry_extract"],
};

export const DOC_LABELS: Record<string, string> = {
  israeli_id_front: "Israeli ID — front",
  israeli_id_back: "Israeli ID — back",
  business_certificate: "Business registration certificate",
  company_registry_extract: "Company registry extract",
};

export const LEGAL_STRUCTURE_LABELS: Record<LegalStructure, string> = {
  osek_morshe: "Osek Morshe (licensed dealer)",
  osek_patur: "Osek Patur (exempt dealer)",
  chevra_baam: "Chevra Ba'am (Ltd. company)",
};
