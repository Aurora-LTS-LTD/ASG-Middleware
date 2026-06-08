"use client";

import { useState } from "react";
import { onboarding, ApiClientError } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { StepCard, ErrorBanner, StubNotice, StepProps, inputClass } from "./common";

export function PaymentStep({ token, onAdvance }: StepProps) {
  const [holder, setHolder] = useState("");
  const [last4, setLast4] = useState("");
  const [expMonth, setExpMonth] = useState("");
  const [expYear, setExpYear] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setLoading(true);
    setError(null);
    try {
      // In production the PayPlus iframe returns a tokenization payload that
      // never exposes the PAN to Aurora's origin. In stub mode the backend
      // accepts these display-only fields and synthesizes a token.
      const res = await onboarding.submitPayment(token, "credit_card", {
        holder_name: holder,
        card_last4: last4,
        card_brand: "visa",
        card_exp_month: Number(expMonth),
        card_exp_year: Number(expYear),
      });
      onAdvance(res.current_step);
    } catch (err) {
      setError(err instanceof ApiClientError ? err.detail.message || "Couldn't save your payment method." : "Network error. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  const valid = holder.trim().length >= 2 && /^\d{4}$/.test(last4) && /^\d{1,2}$/.test(expMonth) && /^\d{4}$/.test(expYear);

  return (
    <StepCard title="Payment method" description="Add a card so billing can start after your free trial.">
      <ErrorBanner message={error} />
      <StubNotice>
        Test mode (PAYPLUS_BACKEND=stub): no real card is processed. In production this step is a secure PayPlus
        iframe — Aurora never sees your full card number.
      </StubNotice>

      <div className="space-y-4">
        <div>
          <label className="mb-1.5 block text-sm font-medium text-foreground">Cardholder name</label>
          <Input value={holder} onChange={(e) => setHolder(e.target.value)} className={inputClass} />
        </div>
        <div>
          <label className="mb-1.5 block text-sm font-medium text-foreground">Card last 4 digits</label>
          <Input value={last4} onChange={(e) => setLast4(e.target.value.replace(/\D/g, "").slice(0, 4))} placeholder="1234" className={inputClass} />
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="mb-1.5 block text-sm font-medium text-foreground">Exp. month</label>
            <Input value={expMonth} onChange={(e) => setExpMonth(e.target.value.replace(/\D/g, "").slice(0, 2))} placeholder="12" className={inputClass} />
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium text-foreground">Exp. year</label>
            <Input value={expYear} onChange={(e) => setExpYear(e.target.value.replace(/\D/g, "").slice(0, 4))} placeholder="2030" className={inputClass} />
          </div>
        </div>
        <Button onClick={submit} disabled={loading || !valid} className="w-full bg-indigo-600 text-white hover:bg-indigo-700">
          {loading ? "Saving…" : "Save & continue"}
        </Button>
      </div>
    </StepCard>
  );
}
