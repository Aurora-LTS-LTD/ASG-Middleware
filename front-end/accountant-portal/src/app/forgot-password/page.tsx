"use client";

/**
 * Aurora LTS Accountant Portal — email password recovery.
 *
 * Two steps on one screen:
 *   1. request — enter work email → backend emails a single-use code (real
 *      SendGrid; anti-enumeration so the response is identical for unknown
 *      emails).
 *   2. reset   — enter the emailed code + a new password → password is rotated
 *      and every existing session is revoked. Then sign in again.
 */

import { useState } from "react";
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

const requestSchema = z.object({
  email: z.string().email({ message: "Please enter a valid work email address." }),
});

const resetSchema = z
  .object({
    code: z.string().min(6, { message: "Enter the code from your email." }).max(16),
    new_password: z
      .string()
      .min(10, { message: "Password must be at least 10 characters." })
      .regex(/[A-Za-z]/, { message: "Include at least one letter." })
      .regex(/\d/, { message: "Include at least one number." }),
    confirm: z.string(),
  })
  .refine((d) => d.new_password === d.confirm, {
    message: "Passwords don't match.",
    path: ["confirm"],
  });

const inputClass =
  "bg-zinc-950 border-zinc-800 text-zinc-100 placeholder:text-zinc-600 focus-visible:ring-indigo-500";

export default function ForgotPasswordPage() {
  const { requestPasswordReset, resetPassword } = useAuth();

  const [step, setStep] = useState<"request" | "reset" | "done">("request");
  const [loading, setLoading] = useState(false);
  const [email, setEmail] = useState("");
  const [sentTo, setSentTo] = useState<string | null>(null);
  const [error, setError] = useState<{ message: string } | null>(null);

  const requestForm = useForm<z.infer<typeof requestSchema>>({
    resolver: zodResolver(requestSchema),
    defaultValues: { email: "" },
  });
  const resetForm = useForm<z.infer<typeof resetSchema>>({
    resolver: zodResolver(resetSchema),
    defaultValues: { code: "", new_password: "", confirm: "" },
  });

  function toMessage(err: unknown): string {
    if (err instanceof ApiClientError) return err.detail.message || err.message;
    return err instanceof Error ? err.message : "Something went wrong. Please try again.";
  }

  async function onRequest(values: z.infer<typeof requestSchema>) {
    setLoading(true);
    setError(null);
    try {
      const res = await requestPasswordReset(values.email);
      setEmail(values.email);
      setSentTo(res.sent_to);
      setStep("reset");
    } catch (err) {
      setError({ message: toMessage(err) });
    } finally {
      setLoading(false);
    }
  }

  async function onReset(values: z.infer<typeof resetSchema>) {
    setLoading(true);
    setError(null);
    try {
      await resetPassword({ email, code: values.code.trim(), new_password: values.new_password });
      setStep("done");
    } catch (err) {
      setError({ message: toMessage(err) });
    } finally {
      setLoading(false);
    }
  }

  const description =
    step === "request"
      ? "Enter your work email and we'll send a reset code"
      : step === "reset"
        ? sentTo
          ? `Enter the code we sent to ${sentTo} and choose a new password`
          : "Enter your reset code and choose a new password"
        : "Your password has been reset";

  return (
    <main className="flex min-h-screen flex-col items-center justify-center p-6 sm:p-12 bg-zinc-950">
      <Card className="w-[420px] bg-zinc-900 border-zinc-800 text-zinc-100 shadow-2xl shadow-black/50">
        <CardHeader className="space-y-1">
          <div className="flex items-center justify-center mb-6">
            <div className="h-12 w-12 bg-indigo-500 rounded-lg flex items-center justify-center shadow-lg shadow-indigo-500/20">
              <span className="text-xl font-bold text-white">A</span>
            </div>
          </div>
          <CardTitle className="text-2xl text-center font-semibold tracking-tight">
            Reset password
          </CardTitle>
          <CardDescription className="text-center text-zinc-400">
            {description}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {error && (
            <div
              role="alert"
              className="mb-4 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2.5 text-sm text-red-300 leading-snug"
            >
              {error.message}
            </div>
          )}

          {step === "request" && (
            <Form {...requestForm}>
              <form onSubmit={requestForm.handleSubmit(onRequest)} className="space-y-4">
                <FormField
                  control={requestForm.control}
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
                <Button type="submit" className="w-full bg-indigo-600 hover:bg-indigo-700 text-white transition-colors" disabled={loading}>
                  {loading ? "Sending code…" : "Send reset code"}
                </Button>
              </form>
            </Form>
          )}

          {step === "reset" && (
            <Form {...resetForm}>
              <form onSubmit={resetForm.handleSubmit(onReset)} className="space-y-4">
                <FormField
                  control={resetForm.control}
                  name="code"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel className="text-zinc-300">Reset code</FormLabel>
                      <FormControl>
                        <Input
                          placeholder="ABCD2345"
                          autoComplete="one-time-code"
                          {...field}
                          className="bg-zinc-950 border-zinc-800 text-zinc-100 text-center tracking-widest font-mono uppercase focus-visible:ring-indigo-500"
                        />
                      </FormControl>
                      <FormMessage className="text-red-400" />
                    </FormItem>
                  )}
                />
                <FormField
                  control={resetForm.control}
                  name="new_password"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel className="text-zinc-300">New password</FormLabel>
                      <FormControl>
                        <Input type="password" placeholder="••••••••" autoComplete="new-password" {...field} className={inputClass} />
                      </FormControl>
                      <FormMessage className="text-red-400" />
                    </FormItem>
                  )}
                />
                <FormField
                  control={resetForm.control}
                  name="confirm"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel className="text-zinc-300">Confirm password</FormLabel>
                      <FormControl>
                        <Input type="password" placeholder="••••••••" autoComplete="new-password" {...field} className={inputClass} />
                      </FormControl>
                      <FormMessage className="text-red-400" />
                    </FormItem>
                  )}
                />
                <Button type="submit" className="w-full bg-indigo-600 hover:bg-indigo-700 text-white transition-colors" disabled={loading}>
                  {loading ? "Resetting…" : "Reset password"}
                </Button>
                <div className="text-center pt-1">
                  <button
                    type="button"
                    onClick={() => { setStep("request"); setError(null); resetForm.reset(); }}
                    className="text-sm text-zinc-500 hover:text-zinc-300 transition-colors"
                  >
                    Didn&apos;t get a code? Try again
                  </button>
                </div>
              </form>
            </Form>
          )}

          {step === "done" && (
            <div className="space-y-4 text-center">
              <p className="text-sm text-zinc-300 leading-relaxed">
                Your password has been reset and all existing sessions were signed out.
                You can now sign in with your new password.
              </p>
              <Link
                href="/"
                className="inline-flex w-full items-center justify-center rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 transition-colors"
              >
                Back to sign in
              </Link>
            </div>
          )}

          {step !== "done" && (
            <div className="mt-6 text-center">
              <Link href="/" className="text-sm text-zinc-500 hover:text-zinc-300 transition-colors">
                Back to sign in
              </Link>
            </div>
          )}
        </CardContent>
      </Card>
    </main>
  );
}
