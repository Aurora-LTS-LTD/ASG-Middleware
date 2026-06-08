"use client";

import { useEffect, useState } from "react";
import { onboarding, ApiClientError } from "@/lib/api/client";
import { formatILS } from "@/lib/format/currency";
import type { PlanOption } from "@/types/onboarding";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { StepCard, ErrorBanner, StepProps } from "./common";

const CYCLES: Array<{ key: "monthly" | "quarterly" | "annual"; label: string }> = [
  { key: "monthly", label: "Monthly" },
  { key: "quarterly", label: "Quarterly" },
  { key: "annual", label: "Annual" },
];

const PLAN_LABELS: Record<string, string> = { starter: "Starter", pro: "Pro", enterprise: "Enterprise" };

export function PlanStep({ token, onAdvance }: StepProps) {
  const [plans, setPlans] = useState<PlanOption[] | null>(null);
  const [trialDays, setTrialDays] = useState<number>(0);
  const [cycle, setCycle] = useState<"monthly" | "quarterly" | "annual">("monthly");
  const [submitting, setSubmitting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    onboarding
      .plans()
      .then((r) => {
        if (cancelled) return;
        setPlans(r.plans);
        setTrialDays(r.trial_days);
      })
      .catch(() => !cancelled && setError("Couldn't load plans. Please refresh."));
    return () => {
      cancelled = true;
    };
  }, []);

  async function choose(plan: string) {
    setSubmitting(plan);
    setError(null);
    try {
      const res = await onboarding.submitPlan(token, plan, cycle);
      onAdvance(res.current_step);
    } catch (err) {
      setError(err instanceof ApiClientError ? err.detail.message || "Couldn't select that plan." : "Network error. Please try again.");
      setSubmitting(null);
    }
  }

  const forCycle = (plans ?? []).filter((p) => p.billing_cycle === cycle);

  return (
    <StepCard title="Choose your plan" description={trialDays ? `Start with a ${trialDays}-day free trial. Cancel anytime.` : "Pick the plan that fits your business."}>
      <ErrorBanner message={error} />

      <div className="mb-5 inline-flex rounded-lg border border-border bg-background/40 p-1">
        {CYCLES.map((c) => (
          <button
            key={c.key}
            onClick={() => setCycle(c.key)}
            className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${cycle === c.key ? "bg-indigo-600 text-white" : "text-muted-foreground hover:text-foreground"}`}
          >
            {c.label}
          </button>
        ))}
      </div>

      {!plans ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          {[0, 1, 2].map((i) => <Skeleton key={i} className="h-44 w-full" />)}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          {forCycle.map((p) => (
            <div key={`${p.plan}-${p.billing_cycle}`} className="flex flex-col rounded-xl border border-border bg-card p-5">
              <p className="text-sm font-semibold text-foreground">{PLAN_LABELS[p.plan] ?? p.plan}</p>
              <p className="mt-2 text-2xl font-bold text-foreground">
                {formatILS(p.total_with_vat_minor_units, { minorUnits: true, decimals: true })}
              </p>
              <p className="text-xs text-muted-foreground">incl. VAT · per {cycle.replace("ly", "")}</p>
              {p.discount_pct > 0 && (
                <p className="mt-1 text-xs font-medium text-emerald-400">Save {p.discount_pct}%</p>
              )}
              <Button
                onClick={() => choose(p.plan)}
                disabled={submitting !== null}
                className="mt-auto w-full bg-indigo-600 text-white hover:bg-indigo-700"
              >
                {submitting === p.plan ? "Selecting…" : "Select"}
              </Button>
            </div>
          ))}
        </div>
      )}
    </StepCard>
  );
}
