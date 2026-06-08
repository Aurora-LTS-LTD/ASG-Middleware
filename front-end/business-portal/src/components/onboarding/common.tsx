"use client";

/** Shared scaffolding for onboarding wizard steps. */
import type { OnboardingStateResponse } from "@/types/onboarding";

export interface StepProps {
  token: string;
  state: OnboardingStateResponse;
  /** Called with the backend's next `current_step` after a successful submit. */
  onAdvance: (next: string) => void;
}

export const inputClass =
  "bg-background border-border text-foreground placeholder:text-muted-foreground focus-visible:ring-indigo-500";

export function StepCard({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      <h2 className="text-xl font-semibold tracking-tight text-foreground">{title}</h2>
      {description && <p className="text-sm text-muted-foreground">{description}</p>}
      <div className="pt-4">{children}</div>
    </div>
  );
}

export function ErrorBanner({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <div role="alert" className="mb-4 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2.5 text-sm text-red-300">
      {message}
    </div>
  );
}

/** A clearly-labelled banner shown when a provider backend is in stub/test mode. */
export function StubNotice({ children }: { children: React.ReactNode }) {
  return (
    <div className="mb-4 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2.5 text-xs text-amber-300">
      {children}
    </div>
  );
}
