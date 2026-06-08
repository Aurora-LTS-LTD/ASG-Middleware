"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { onboarding, ApiClientError } from "@/lib/api/client";
import { useAuth } from "@/lib/auth/context";
import { clearOnboardingSession } from "@/lib/onboarding/session";
import { Button } from "@/components/ui/button";
import { StepCard, ErrorBanner, StepProps } from "./common";

export function ActivateStep({ token, state }: StepProps) {
  const router = useRouter();
  const { adoptSession } = useAuth();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function activate() {
    setLoading(true);
    setError(null);
    try {
      const res = await onboarding.activate(token);
      // The backend created the Business + Organization + owner Membership and
      // minted a real session token. Adopt it as the main session, drop the
      // onboarding session, and land on the dashboard.
      adoptSession(res.access_token, {
        id: state.user.id,
        email: state.user.email,
        full_name: state.user.email,
        role: "business_owner",
        business_id: null,
      });
      clearOnboardingSession();
      router.replace(res.redirect_to || "/dashboard");
    } catch (err) {
      setError(err instanceof ApiClientError ? err.detail.message || "Activation failed. Please review the earlier steps." : "Network error. Please try again.");
      setLoading(false);
    }
  }

  return (
    <StepCard title="You're all set" description="Activate your account to start invoicing.">
      <ErrorBanner message={error} />
      <p className="mb-4 text-sm text-muted-foreground">
        We'll create your business profile and tax workspace, then take you to your dashboard.
      </p>
      <Button onClick={activate} disabled={loading} className="w-full bg-indigo-600 text-white hover:bg-indigo-700">
        {loading ? "Activating…" : "Activate & go to dashboard"}
      </Button>
    </StepCard>
  );
}
