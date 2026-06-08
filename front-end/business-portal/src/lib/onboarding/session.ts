"use client";

/**
 * Onboarding session store — separate from the main auth session on purpose.
 *
 * The /onboarding/start JWT lets a user who has NOT finished registration call
 * the wizard endpoints. We must NOT put it in the main token store, or the
 * (authed) shell would treat a half-registered user as fully signed in. On
 * activate() the backend returns a real session token, which we hand to the
 * AuthProvider (adoptSession) and then clear this onboarding session.
 */
const KEY = "aurora_onboarding_session";

export interface OnboardingSession {
  token: string;
  userId: number;
  email: string;
}

const isBrowser = () => typeof window !== "undefined";

export function getOnboardingSession(): OnboardingSession | null {
  if (!isBrowser()) return null;
  const raw = window.localStorage.getItem(KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as OnboardingSession;
  } catch {
    return null;
  }
}

export function setOnboardingSession(s: OnboardingSession): void {
  if (!isBrowser()) return;
  window.localStorage.setItem(KEY, JSON.stringify(s));
}

export function clearOnboardingSession(): void {
  if (!isBrowser()) return;
  window.localStorage.removeItem(KEY);
}
