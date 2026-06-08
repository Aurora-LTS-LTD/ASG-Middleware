"use client";

import { useState } from "react";
import { onboarding, ApiClientError } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { StepCard, ErrorBanner, StepProps } from "./common";

export function ReviewStep({ token, onAdvance }: StepProps) {
  const [terms, setTerms] = useState(false);
  const [privacy, setPrivacy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setLoading(true);
    setError(null);
    try {
      const res = await onboarding.submitReview(token, terms, privacy);
      onAdvance(res.current_step);
    } catch (err) {
      setError(err instanceof ApiClientError ? err.detail.message || "Couldn't record your acceptance." : "Network error. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <StepCard title="Review & accept" description="Confirm you agree to the terms before we activate your account.">
      <ErrorBanner message={error} />
      <div className="space-y-3">
        <label className="flex items-start gap-3 rounded-lg border border-border bg-background/40 px-4 py-3 text-sm text-foreground">
          <input type="checkbox" checked={terms} onChange={(e) => setTerms(e.target.checked)} className="mt-0.5 h-4 w-4 accent-indigo-600" />
          <span>I accept the <span className="font-medium text-indigo-400">Terms of Service</span>.</span>
        </label>
        <label className="flex items-start gap-3 rounded-lg border border-border bg-background/40 px-4 py-3 text-sm text-foreground">
          <input type="checkbox" checked={privacy} onChange={(e) => setPrivacy(e.target.checked)} className="mt-0.5 h-4 w-4 accent-indigo-600" />
          <span>I have read the <span className="font-medium text-indigo-400">Privacy Policy</span>.</span>
        </label>
      </div>
      <Button onClick={submit} disabled={loading || !terms || !privacy} className="mt-4 w-full bg-indigo-600 text-white hover:bg-indigo-700">
        {loading ? "Saving…" : "Continue"}
      </Button>
    </StepCard>
  );
}
