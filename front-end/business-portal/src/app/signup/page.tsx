"use client";

/**
 * Business Owner Portal — registration entry ("/signup").
 * Email + password → POST /api/v1/onboarding/start → stores the onboarding
 * session (separate from the main auth session) → routes into the wizard.
 */
import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { z } from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";

import { onboarding, ApiClientError } from "@/lib/api/client";
import { setOnboardingSession } from "@/lib/onboarding/session";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

// Password rules mirror the M1 backend (StartRequest: min 8). We add a light
// strength nudge on top — the backend remains the source of truth.
const signupSchema = z
  .object({
    email: z.string().email({ message: "Enter a valid email address." }),
    password: z
      .string()
      .min(8, { message: "At least 8 characters." })
      .max(200, { message: "Too long." })
      .regex(/[A-Za-z]/, { message: "Include at least one letter." })
      .regex(/[0-9]/, { message: "Include at least one number." }),
    confirm: z.string(),
  })
  .refine((v) => v.password === v.confirm, {
    path: ["confirm"],
    message: "Passwords don't match.",
  });

const inputClass =
  "bg-background border-border text-foreground placeholder:text-muted-foreground focus-visible:ring-indigo-500";

export default function SignupPage() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const form = useForm<z.infer<typeof signupSchema>>({
    resolver: zodResolver(signupSchema),
    defaultValues: { email: "", password: "", confirm: "" },
  });

  async function onSubmit(values: z.infer<typeof signupSchema>) {
    setLoading(true);
    setError(null);
    try {
      const res = await onboarding.start(values.email.trim().toLowerCase(), values.password, "en");
      setOnboardingSession({ token: res.access_token, userId: res.user.id, email: res.user.email });
      router.push("/onboarding");
    } catch (err) {
      if (err instanceof ApiClientError) {
        if (err.status === 409) {
          setError("This account is already active. Please sign in instead.");
        } else if (err.status === 401) {
          setError("That email is already registered. If it's yours, sign in — otherwise use a different email.");
        } else {
          setError(err.detail.message || "Couldn't start registration. Please try again.");
        }
      } else {
        setError("Couldn't reach the server. Check your connection and try again.");
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-background p-6 sm:p-12">
      <Card className="w-full max-w-[440px] border-border bg-card text-foreground shadow-2xl shadow-black/30">
        <CardHeader className="space-y-1">
          <div className="mb-4 flex items-center justify-center">
            <div className="flex h-12 w-12 items-center justify-center rounded-lg bg-indigo-600 shadow-lg shadow-indigo-500/20">
              <span className="text-xl font-bold text-white">A</span>
            </div>
          </div>
          <CardTitle className="text-center text-2xl font-semibold tracking-tight">
            Create your Aurora account
          </CardTitle>
          <CardDescription className="text-center text-muted-foreground">
            Set up your business in a few steps — invoicing &amp; Israeli tax compliance.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {error && (
            <div role="alert" className="mb-4 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2.5 text-sm text-red-300">
              {error}
            </div>
          )}
          <Form {...form}>
            <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
              <FormField
                control={form.control}
                name="email"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel className="text-foreground">Email</FormLabel>
                    <FormControl>
                      <Input placeholder="you@business.co.il" autoComplete="email" {...field} className={inputClass} />
                    </FormControl>
                    <FormMessage className="text-red-400" />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="password"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel className="text-foreground">Password</FormLabel>
                    <FormControl>
                      <Input type="password" placeholder="At least 8 characters" autoComplete="new-password" {...field} className={inputClass} />
                    </FormControl>
                    <FormMessage className="text-red-400" />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="confirm"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel className="text-foreground">Confirm password</FormLabel>
                    <FormControl>
                      <Input type="password" placeholder="Re-enter your password" autoComplete="new-password" {...field} className={inputClass} />
                    </FormControl>
                    <FormMessage className="text-red-400" />
                  </FormItem>
                )}
              />
              <Button type="submit" className="w-full bg-indigo-600 text-white transition-colors hover:bg-indigo-700" disabled={loading}>
                {loading ? "Creating account…" : "Create account"}
              </Button>
            </form>
          </Form>
          <p className="mt-6 text-center text-sm text-muted-foreground">
            Already have an account?{" "}
            <Link href="/" className="font-medium text-indigo-400 hover:text-indigo-300">
              Sign in
            </Link>
          </p>
        </CardContent>
      </Card>
    </main>
  );
}
