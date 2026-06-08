"use client";

/**
 * Business Owner Portal — onboarding wizard host ("/onboarding").
 *
 * Driven entirely by the backend FSM: it loads /onboarding/state and renders
 * the component for `current_step`. Each step, on success, triggers a re-load
 * so `current_step` + the draft payload stay authoritative server-side. On
 * activate() the wizard hands a real session to the AuthProvider and leaves.
 */
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { onboarding, ApiClientError, AuthFatalError } from "@/lib/api/client";
import { getOnboardingSession, clearOnboardingSession } from "@/lib/onboarding/session";
import type { OnboardingStateResponse } from "@/types/onboarding";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorBoundary } from "@/components/shell/ErrorBoundary";

import { IdentityStep } from "@/components/onboarding/IdentityStep";
import { OtpStep } from "@/components/onboarding/OtpStep";
import { DocumentsStep } from "@/components/onboarding/DocumentsStep";
import { PlanStep } from "@/components/onboarding/PlanStep";
import { PaymentStep } from "@/components/onboarding/PaymentStep";
import { ReviewStep } from "@/components/onboarding/ReviewStep";
import { ActivateStep } from "@/components/onboarding/ActivateStep";

const STEPPER: Array<{ key: string; label: string }> = [
  { key: "identity", label: "Details" },
  { key: "email_otp", label: "Email" },
  { key: "phone_otp", label: "Phone" },
  { key: "documents", label: "Documents" },
  { key: "plan", label: "Plan" },
  { key: "payment_method", label: "Payment" },
  { key: "review", label: "Review" },
  { key: "activate", label: "Activate" },
];

export default function OnboardingPage() {
  const router = useRouter();
  const [token, setToken] = useState<string | null>(null);
  const [state, setState] = useState<OnboardingStateResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (tok: string) => {
      try {
        const s = await onboarding.state(tok);
        setState(s);
        setError(null);
      } catch (err) {
        if (err instanceof AuthFatalError) {
          clearOnboardingSession();
          router.replace("/signup");
          return;
        }
        if (err instanceof ApiClientError && err.status === 404) {
          // No active onboarding session (likely already completed) → sign in.
          clearOnboardingSession();
          router.replace("/");
          return;
        }
        setError("Couldn't load your registration. Please try again.");
      }
    },
    [router],
  );

  useEffect(() => {
    const sess = getOnboardingSession();
    if (!sess) {
      router.replace("/signup");
      return;
    }
    setToken(sess.token);
    load(sess.token);
  }, [load, router]);

  const onAdvance = useCallback(() => {
    if (token) load(token);
  }, [token, load]);

  return (
    <main className="flex min-h-screen flex-col items-center bg-background px-4 py-10 sm:py-16">
      <div className="w-full max-w-2xl">
        <div className="mb-8 flex items-center justify-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-indigo-600">
            <span className="text-base font-bold text-white">A</span>
          </div>
          <span className="text-sm font-medium text-muted-foreground">Aurora LTS — Get started</span>
        </div>

        {state && <Stepper current={state.current_step} completed={state.completed_steps} />}

        <div className="mt-6 rounded-2xl border border-border bg-card p-6 shadow-2xl shadow-black/30 sm:p-8">
          {error ? (
            <div role="alert" className="space-y-3 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2.5 text-sm text-red-300">
              <p>{error}</p>
              {token && (
                <button
                  type="button"
                  onClick={() => load(token)}
                  className="rounded-md border border-red-500/40 px-3 py-1.5 text-xs font-medium text-red-200 transition-colors hover:bg-red-500/20"
                >
                  Try again
                </button>
              )}
            </div>
          ) : !state || !token ? (
            <div className="space-y-4">
              <Skeleton className="h-6 w-1/3" />
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-2/3" />
            </div>
          ) : (
            <ErrorBoundary>
              <StepRenderer token={token} state={state} onAdvance={onAdvance} />
            </ErrorBoundary>
          )}
        </div>
      </div>
    </main>
  );
}

function StepRenderer({
  token,
  state,
  onAdvance,
}: {
  token: string;
  state: OnboardingStateResponse;
  onAdvance: () => void;
}) {
  const props = { token, state, onAdvance };
  switch (state.current_step) {
    case "identity":
      return <IdentityStep {...props} />;
    case "email_otp":
      return <OtpStep {...props} channel="email" />;
    case "phone_otp":
      return <OtpStep {...props} channel="phone" />;
    case "documents":
      return <DocumentsStep {...props} />;
    case "plan":
      return <PlanStep {...props} />;
    case "payment_method":
      return <PaymentStep {...props} />;
    case "review":
      return <ReviewStep {...props} />;
    default:
      // activate / complete / any terminal state → final commit
      return <ActivateStep {...props} />;
  }
}

function Stepper({ current, completed }: { current: string; completed: string[] }) {
  const done = new Set(completed);
  return (
    <div className="flex flex-wrap items-center justify-center gap-x-2 gap-y-1 text-xs">
      {STEPPER.map((s, i) => {
        const isCurrent = s.key === current;
        const isDone = done.has(s.key);
        return (
          <div key={s.key} className="flex items-center gap-2">
            <span
              className={
                isCurrent
                  ? "font-semibold text-indigo-400"
                  : isDone
                    ? "text-emerald-400"
                    : "text-muted-foreground"
              }
            >
              {isDone ? "✓ " : ""}
              {s.label}
            </span>
            {i < STEPPER.length - 1 && <span className="text-muted-foreground/40">›</span>}
          </div>
        );
      })}
    </div>
  );
}
