"use client";

import { useState } from "react";
import { onboarding, ApiClientError } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { StepCard, ErrorBanner, StubNotice, StepProps, inputClass } from "./common";

type Props = StepProps & { channel: "email" | "phone" };

export function OtpStep({ token, state, onAdvance, channel }: Props) {
  const isEmail = channel === "email";
  const [target, setTarget] = useState(isEmail ? state.user.email : "");
  const [code, setCode] = useState("");
  const [sent, setSent] = useState(false);
  const [devCode, setDevCode] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function send() {
    setLoading(true);
    setError(null);
    try {
      const res = await onboarding.sendOtp(token, channel, target.trim());
      setSent(true);
      setDevCode(res.dev_only_code ?? null);
    } catch (err) {
      setError(err instanceof ApiClientError ? err.detail.message || "Couldn't send the code." : "Network error. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  async function verify() {
    setLoading(true);
    setError(null);
    try {
      const res = await onboarding.verifyOtp(token, channel, target.trim(), code.trim());
      onAdvance(res.current_step);
    } catch (err) {
      setError(err instanceof ApiClientError ? err.detail.message || "Invalid or expired code." : "Network error. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <StepCard
      title={isEmail ? "Verify your email" : "Verify your phone"}
      description={isEmail ? "We'll send a 6-digit code to confirm your email." : "We'll text a 6-digit code to confirm your phone."}
    >
      <ErrorBanner message={error} />
      {devCode && (
        <StubNotice>
          Test mode (OTP_BACKEND=stub): your code is <span className="font-mono font-semibold">{devCode}</span>. In
          production this is delivered via {isEmail ? "email (SendGrid)" : "SMS/WhatsApp"} and not shown here.
        </StubNotice>
      )}

      <div className="space-y-4">
        <div>
          <label className="mb-1.5 block text-sm font-medium text-foreground">
            {isEmail ? "Email" : "Phone (E.164, e.g. +9725…)"}
          </label>
          <Input
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            disabled={isEmail || sent}
            placeholder={isEmail ? "" : "+9725XXXXXXXX"}
            className={inputClass}
          />
        </div>

        {!sent ? (
          <Button onClick={send} disabled={loading || target.trim().length < 3} className="w-full bg-indigo-600 text-white hover:bg-indigo-700">
            {loading ? "Sending…" : "Send code"}
          </Button>
        ) : (
          <>
            <div>
              <label className="mb-1.5 block text-sm font-medium text-foreground">6-digit code</label>
              <Input
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                inputMode="numeric"
                placeholder="••••••"
                className={`text-center text-lg tracking-[0.5em] ${inputClass}`}
              />
            </div>
            <div className="flex gap-2">
              <Button type="button" variant="outline" onClick={send} disabled={loading} className="flex-1">
                Resend
              </Button>
              <Button onClick={verify} disabled={loading || code.length !== 6} className="flex-1 bg-indigo-600 text-white hover:bg-indigo-700">
                {loading ? "Verifying…" : "Verify"}
              </Button>
            </div>
          </>
        )}
      </div>
    </StepCard>
  );
}
