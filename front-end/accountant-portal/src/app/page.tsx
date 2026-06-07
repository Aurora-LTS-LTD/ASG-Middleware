"use client";

/**
 * Aurora LTS Accountant Portal — Login + post-login stub.
 *
 * Production auth: email + password is the primary sign-in (real bcrypt on M1).
 * A one-time email code (OTP) remains available as an alternate, and
 * "Forgot password?" routes to the email recovery flow at /forgot-password.
 *
 * Three states (controlled by `useAuth().status`):
 *   • initializing  — keychain bootstrap in progress (split-second flash)
 *   • signed_out    — login form (password | email-code)
 *   • signed_in     — placeholder dashboard with sign-out + device summary
 *
 * Error rendering surfaces the backend's structured error codes
 * (invalid_credentials, otp_invalid, otp_locked, otp_rate_limited, …).
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { z } from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";

import { Button } from "@/components/ui/button";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

import { useAuth } from "@/lib/auth/context";
import { ApiClientError } from "@/lib/api/client";
import { isTauriRuntime } from "@/lib/tauri/keychain";

const emailSchema = z.object({
  email: z.string().email({
    message: "Please enter a valid work email address.",
  }),
});

const passwordSchema = z.object({
  email: z.string().email({
    message: "Please enter a valid work email address.",
  }),
  password: z.string().min(1, { message: "Enter your password." }),
});

const otpSchema = z.object({
  otp: z
    .string()
    .length(6, { message: "Security code must be exactly 6 digits." })
    .regex(/^\d{6}$/, { message: "Security code is digits only." }),
});

// ─────────────────────────────────────────────────────────────
// Root — chooses between login + signed-in stub
// ─────────────────────────────────────────────────────────────

export default function Home() {
  const { status, user } = useAuth();

  if (status === "initializing") {
    return <Centered><InitializingPulse /></Centered>;
  }

  if (status === "signed_in" && user) {
    return <SignedInView />;
  }

  return <LoginView />;
}

// ─────────────────────────────────────────────────────────────
// Login flow — password primary, email-code alternate
// ─────────────────────────────────────────────────────────────

function LoginView() {
  const { requestOtp, verifyOtp, loginWithPassword } = useAuth();

  const [mode, setMode] = useState<"password" | "otp">("password");
  const [step, setStep] = useState<"email" | "otp">("email");
  const [loading, setLoading] = useState(false);
  const [currentEmail, setCurrentEmail] = useState("");
  const [sentTo, setSentTo] = useState<string | null>(null);
  const [error, setError] = useState<{ code: string; message: string; retry_after_seconds?: number } | null>(null);

  const passwordForm = useForm<z.infer<typeof passwordSchema>>({
    resolver: zodResolver(passwordSchema),
    defaultValues: { email: "", password: "" },
  });

  const emailForm = useForm<z.infer<typeof emailSchema>>({
    resolver: zodResolver(emailSchema),
    defaultValues: { email: "" },
  });

  const otpForm = useForm<z.infer<typeof otpSchema>>({
    resolver: zodResolver(otpSchema),
    defaultValues: { otp: "" },
  });

  async function onPasswordSubmit(values: z.infer<typeof passwordSchema>) {
    setLoading(true);
    setError(null);
    try {
      await loginWithPassword({ email: values.email, password: values.password });
      // useAuth() transitions to signed_in → Home re-renders SignedInView
    } catch (err) {
      handleApiError(err, setError);
    } finally {
      setLoading(false);
    }
  }

  async function onEmailSubmit(values: z.infer<typeof emailSchema>) {
    setLoading(true);
    setError(null);
    try {
      const res = await requestOtp(values.email);
      setCurrentEmail(values.email);
      setSentTo(res.sent_to);
      setStep("otp");
    } catch (err) {
      handleApiError(err, setError);
    } finally {
      setLoading(false);
    }
  }

  async function onOtpSubmit(values: z.infer<typeof otpSchema>) {
    setLoading(true);
    setError(null);
    try {
      await verifyOtp({ email: currentEmail, otp: values.otp });
    } catch (err) {
      handleApiError(err, setError);
    } finally {
      setLoading(false);
    }
  }

  const description =
    mode === "password"
      ? "Secure accountant access terminal"
      : step === "email"
        ? "We'll email you a one-time sign-in code"
        : sentTo
          ? `Enter the code we sent to ${sentTo}`
          : "Enter the verification code";

  const inputClass =
    "bg-zinc-950 border-zinc-800 text-zinc-100 placeholder:text-zinc-600 focus-visible:ring-indigo-500";

  return (
    <Centered>
      <Card className="w-[420px] bg-zinc-900 border-zinc-800 text-zinc-100 shadow-2xl shadow-black/50">
        <CardHeader className="space-y-1">
          <div className="flex items-center justify-center mb-6">
            <div className="h-12 w-12 bg-indigo-500 rounded-lg flex items-center justify-center shadow-lg shadow-indigo-500/20">
              <span className="text-xl font-bold text-white">A</span>
            </div>
          </div>
          <CardTitle className="text-2xl text-center font-semibold tracking-tight">
            Aurora LTS Portal
          </CardTitle>
          <CardDescription className="text-center text-zinc-400">
            {description}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {error && <InlineError error={error} onDismiss={() => setError(null)} />}

          {mode === "password" && (
            <Form {...passwordForm}>
              <form onSubmit={passwordForm.handleSubmit(onPasswordSubmit)} className="space-y-4">
                <FormField
                  control={passwordForm.control}
                  name="email"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel className="text-zinc-300">Work Email</FormLabel>
                      <FormControl>
                        <Input placeholder="accountant@agency.com" autoComplete="email" {...field} className={inputClass} />
                      </FormControl>
                      <FormMessage className="text-red-400" />
                    </FormItem>
                  )}
                />
                <FormField
                  control={passwordForm.control}
                  name="password"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel className="text-zinc-300">Password</FormLabel>
                      <FormControl>
                        <Input type="password" placeholder="••••••••" autoComplete="current-password" {...field} className={inputClass} />
                      </FormControl>
                      <FormMessage className="text-red-400" />
                    </FormItem>
                  )}
                />
                <Button
                  type="submit"
                  className="w-full bg-indigo-600 hover:bg-indigo-700 text-white transition-colors"
                  disabled={loading}
                >
                  {loading ? "Signing in…" : "Sign In"}
                </Button>
                <div className="flex items-center justify-between pt-1">
                  <button
                    type="button"
                    onClick={() => { setMode("otp"); setStep("email"); setError(null); }}
                    className="text-sm text-zinc-500 hover:text-zinc-300 transition-colors"
                  >
                    Sign in with email code
                  </button>
                  <Link href="/forgot-password" className="text-sm text-indigo-400 hover:text-indigo-300 transition-colors">
                    Forgot password?
                  </Link>
                </div>
              </form>
            </Form>
          )}

          {mode === "otp" && step === "email" && (
            <Form {...emailForm}>
              <form onSubmit={emailForm.handleSubmit(onEmailSubmit)} className="space-y-4">
                <FormField
                  control={emailForm.control}
                  name="email"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel className="text-zinc-300">Work Email</FormLabel>
                      <FormControl>
                        <Input placeholder="accountant@agency.com" autoComplete="email" {...field} className={inputClass} />
                      </FormControl>
                      <FormMessage className="text-red-400" />
                    </FormItem>
                  )}
                />
                <Button
                  type="submit"
                  className="w-full bg-indigo-600 hover:bg-indigo-700 text-white transition-colors"
                  disabled={loading}
                >
                  {loading ? "Sending code…" : "Send code"}
                </Button>
                <div className="text-center pt-1">
                  <button
                    type="button"
                    onClick={() => { setMode("password"); setError(null); }}
                    className="text-sm text-zinc-500 hover:text-zinc-300 transition-colors"
                  >
                    Use password instead
                  </button>
                </div>
              </form>
            </Form>
          )}

          {mode === "otp" && step === "otp" && (
            <Form {...otpForm}>
              <form onSubmit={otpForm.handleSubmit(onOtpSubmit)} className="space-y-4">
                <FormField
                  control={otpForm.control}
                  name="otp"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel className="text-zinc-300">Security Code</FormLabel>
                      <FormControl>
                        <Input
                          placeholder="000000"
                          maxLength={6}
                          inputMode="numeric"
                          autoComplete="one-time-code"
                          {...field}
                          className="bg-zinc-950 border-zinc-800 text-zinc-100 text-center tracking-widest text-lg font-mono focus-visible:ring-indigo-500"
                        />
                      </FormControl>
                      <FormMessage className="text-red-400" />
                    </FormItem>
                  )}
                />
                <Button
                  type="submit"
                  className="w-full bg-indigo-600 hover:bg-indigo-700 text-white transition-colors"
                  disabled={loading}
                >
                  {loading ? "Verifying…" : "Verify & Sign In"}
                </Button>
                <div className="text-center mt-4">
                  <button
                    type="button"
                    onClick={() => {
                      setStep("email");
                      setError(null);
                      otpForm.reset();
                    }}
                    className="text-sm text-zinc-500 hover:text-zinc-300 transition-colors"
                  >
                    Use a different email
                  </button>
                </div>
              </form>
            </Form>
          )}
        </CardContent>
      </Card>

      <div className="mt-8 text-xs text-zinc-600 flex items-center gap-2">
        <svg
          xmlns="http://www.w3.org/2000/svg"
          width="12"
          height="12"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10" />
          <path d="m9 12 2 2 4-4" />
        </svg>
        Zero-Trust B2B Secured Connection
        {isTauriRuntime() ? null : (
          <span className="text-amber-500/70">・ browser mode (no keychain)</span>
        )}
      </div>
    </Centered>
  );
}

// ─────────────────────────────────────────────────────────────
// Signed-in stub view (Phase 3 will replace this with the dashboard)
// ─────────────────────────────────────────────────────────────

function SignedInView() {
  const { user, deviceId, isNewDevice, signOut } = useAuth();
  const [signingOut, setSigningOut] = useState(false);

  return (
    <Centered>
      <Card className="w-[480px] bg-zinc-900 border-zinc-800 text-zinc-100 shadow-2xl shadow-black/50">
        <CardHeader>
          <div className="flex items-center gap-3">
            <div className="h-10 w-10 bg-emerald-500/20 border border-emerald-500/40 rounded-lg flex items-center justify-center">
              <svg
                xmlns="http://www.w3.org/2000/svg"
                width="20"
                height="20"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
                strokeLinecap="round"
                strokeLinejoin="round"
                className="text-emerald-400"
              >
                <path d="M20 6 9 17l-5-5" />
              </svg>
            </div>
            <div>
              <CardTitle className="text-lg font-semibold tracking-tight">
                Signed in
              </CardTitle>
              <CardDescription className="text-zinc-400 mt-0.5 text-sm">
                {user?.email}
              </CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {isNewDevice && (
            <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3">
              <div className="text-xs uppercase tracking-wider text-amber-400 mb-1">
                New device detected
              </div>
              <div className="text-sm text-zinc-300">
                We&apos;ve emailed you about this sign-in. Review or revoke
                bound devices anytime from the security panel.
              </div>
            </div>
          )}

          <dl className="grid grid-cols-3 gap-3 text-sm">
            <div className="col-span-1 text-zinc-500">Name</div>
            <div className="col-span-2 text-zinc-200">{user?.name || "—"}</div>

            {user?.firm_name && (
              <>
                <div className="col-span-1 text-zinc-500">Firm</div>
                <div className="col-span-2 text-zinc-200">{user.firm_name}</div>
              </>
            )}

            <div className="col-span-1 text-zinc-500">Role</div>
            <div className="col-span-2 text-zinc-200 font-mono text-xs uppercase tracking-wider">
              {user?.role}
            </div>

            <div className="col-span-1 text-zinc-500">Device</div>
            <div className="col-span-2 text-zinc-200 font-mono text-xs">
              #{deviceId}
            </div>

            <div className="col-span-1 text-zinc-500">Runtime</div>
            <div className="col-span-2 text-zinc-200 font-mono text-xs">
              {isTauriRuntime() ? "Tauri native shell" : "Browser preview"}
            </div>
          </dl>

          <div className="border-t border-zinc-800 pt-4">
            <div className="text-xs text-zinc-500 mb-3">
              Phase 2 stub. The Phase 3 dashboard (client list, pending tasks,
              metrics) will replace this view.
            </div>
            <Button
              variant="outline"
              className="w-full border-zinc-700 bg-zinc-950 hover:bg-zinc-800 text-zinc-100"
              onClick={async () => {
                setSigningOut(true);
                try {
                  await signOut();
                } finally {
                  setSigningOut(false);
                }
              }}
              disabled={signingOut}
            >
              {signingOut ? "Signing out…" : "Sign out"}
            </Button>
          </div>
        </CardContent>
      </Card>
    </Centered>
  );
}

// ─────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center p-6 sm:p-12 bg-zinc-950">
      {children}
    </main>
  );
}

function InitializingPulse() {
  return (
    <div className="flex items-center gap-2 text-zinc-500 text-sm">
      <div className="h-2 w-2 rounded-full bg-indigo-500 animate-pulse" />
      Initializing secure session…
    </div>
  );
}

function InlineError({
  error,
  onDismiss,
}: {
  error: { code: string; message: string; retry_after_seconds?: number };
  onDismiss: () => void;
}) {
  const isLockout =
    error.code === "otp_locked" ||
    error.code === "otp_rate_limited" ||
    error.code === "reset_locked" ||
    error.code === "reset_rate_limited";
  return (
    <div
      role="alert"
      className={`mb-4 rounded-lg border px-3 py-2.5 text-sm ${
        isLockout
          ? "border-amber-500/40 bg-amber-500/10 text-amber-300"
          : "border-red-500/40 bg-red-500/10 text-red-300"
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 leading-snug">{error.message}</div>
        <button
          onClick={onDismiss}
          className="text-zinc-500 hover:text-zinc-300 text-xs"
          aria-label="Dismiss"
        >
          ✕
        </button>
      </div>
      {error.retry_after_seconds && error.retry_after_seconds > 0 && (
        <Countdown seconds={error.retry_after_seconds} />
      )}
    </div>
  );
}

function Countdown({ seconds }: { seconds: number }) {
  const [remaining, setRemaining] = useState(seconds);
  useEffect(() => {
    if (remaining <= 0) return;
    const t = setInterval(() => setRemaining((s) => Math.max(0, s - 1)), 1000);
    return () => clearInterval(t);
  }, [remaining]);
  const mm = String(Math.floor(remaining / 60)).padStart(2, "0");
  const ss = String(remaining % 60).padStart(2, "0");
  return (
    <div className="text-xs font-mono mt-1 opacity-80">
      {remaining > 0 ? `Retry in ${mm}:${ss}` : "You can retry now."}
    </div>
  );
}

function handleApiError(
  err: unknown,
  setError: (e: { code: string; message: string; retry_after_seconds?: number }) => void,
): void {
  if (err instanceof ApiClientError) {
    setError({
      code: err.errorCode,
      message: err.detail.message || err.message,
      retry_after_seconds:
        typeof err.detail.retry_after_seconds === "number"
          ? err.detail.retry_after_seconds
          : undefined,
    });
    return;
  }
  // Mock layer throws plain objects { status, body }
  if (typeof err === "object" && err !== null && "body" in err) {
    const e = err as { status?: number; body?: { detail?: { error?: string; message?: string; retry_after_seconds?: number } } };
    setError({
      code: e.body?.detail?.error || "unknown",
      message: e.body?.detail?.message || "Something went wrong.",
      retry_after_seconds: e.body?.detail?.retry_after_seconds,
    });
    return;
  }
  console.error("[handleApiError] unexpected error shape:", err);
  setError({
    code: "unknown",
    message: err instanceof Error ? err.message : "Unknown error.",
  });
}
