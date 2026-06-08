"use client";

/**
 * Business Owner Portal — login + signed-in placeholder.
 *
 * v1 auth: email + password against M1's /api/v1/auth/login. Once signed in,
 * batch 3 replaces the placeholder with the real invoice dashboard.
 */
import { useState } from "react";
import { z } from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";

import { useAuth } from "@/lib/auth/context";
import { ApiClientError } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Form, FormControl, FormField, FormItem, FormLabel, FormMessage,
} from "@/components/ui/form";
import {
  Card, CardContent, CardDescription, CardHeader, CardTitle,
} from "@/components/ui/card";

const loginSchema = z.object({
  email: z.string().email({ message: "Enter a valid email address." }),
  password: z.string().min(1, { message: "Enter your password." }),
});

const inputClass =
  "bg-background border-border text-foreground placeholder:text-muted-foreground focus-visible:ring-indigo-500";

export default function Home() {
  const { status, user } = useAuth();
  if (status === "initializing") {
    return (
      <Centered>
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <div className="h-2 w-2 animate-pulse rounded-full bg-indigo-500" />
          Loading…
        </div>
      </Centered>
    );
  }
  if (status === "signed_in" && user) return <SignedInView />;
  return <LoginView />;
}

function LoginView() {
  const { loginWithPassword } = useAuth();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const form = useForm<z.infer<typeof loginSchema>>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: "", password: "" },
  });

  async function onSubmit(values: z.infer<typeof loginSchema>) {
    setLoading(true);
    setError(null);
    try {
      await loginWithPassword(values.email, values.password);
    } catch (err) {
      setError(
        err instanceof ApiClientError
          ? err.detail.message || "Email or password is incorrect."
          : "Couldn't sign in. Please try again.",
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <Centered>
      <Card className="w-[420px] border-border bg-card text-foreground shadow-2xl shadow-black/30">
        <CardHeader className="space-y-1">
          <div className="mb-6 flex items-center justify-center">
            <div className="flex h-12 w-12 items-center justify-center rounded-lg bg-indigo-600 shadow-lg shadow-indigo-500/20">
              <span className="text-xl font-bold text-white">A</span>
            </div>
          </div>
          <CardTitle className="text-center text-2xl font-semibold tracking-tight">
            Aurora LTS — Business Portal
          </CardTitle>
          <CardDescription className="text-center text-muted-foreground">
            Sign in to manage your invoices and tax compliance
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
                      <Input type="password" placeholder="••••••••" autoComplete="current-password" {...field} className={inputClass} />
                    </FormControl>
                    <FormMessage className="text-red-400" />
                  </FormItem>
                )}
              />
              <Button type="submit" className="w-full bg-indigo-600 text-white transition-colors hover:bg-indigo-700" disabled={loading}>
                {loading ? "Signing in…" : "Sign In"}
              </Button>
            </form>
          </Form>
        </CardContent>
      </Card>
    </Centered>
  );
}

function SignedInView() {
  const { user, signOut } = useAuth();
  return (
    <Centered>
      <Card className="w-[460px] border-border bg-card text-foreground shadow-2xl shadow-black/30">
        <CardHeader>
          <CardTitle className="text-lg font-semibold tracking-tight">Signed in</CardTitle>
          <CardDescription className="text-muted-foreground">{user?.email}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <dl className="grid grid-cols-3 gap-2 text-sm">
            <dt className="text-muted-foreground">Name</dt>
            <dd className="col-span-2 text-foreground">{user?.full_name || "—"}</dd>
            <dt className="text-muted-foreground">Role</dt>
            <dd className="col-span-2 font-mono text-xs uppercase tracking-wider text-foreground">{user?.role}</dd>
          </dl>
          <p className="text-xs text-muted-foreground">
            Batch 3 replaces this with your invoice dashboard (status KPIs + list);
            batch 4 adds the per-invoice lifecycle timeline.
          </p>
          <Button
            variant="outline"
            className="w-full border-border bg-background text-foreground hover:bg-accent"
            onClick={signOut}
          >
            Sign out
          </Button>
        </CardContent>
      </Card>
    </Centered>
  );
}

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-background p-6 sm:p-12">
      {children}
    </main>
  );
}
