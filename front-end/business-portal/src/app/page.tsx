"use client";

/**
 * Business Owner Portal — login ("/").
 * Signed-out → email+password login. Signed-in → redirect to /dashboard.
 */
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
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
  const { status } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (status === "signed_in") router.replace("/dashboard");
  }, [status, router]);

  if (status === "signed_out") return <LoginView />;
  // initializing OR signed_in (mid-redirect) → brief spinner
  return (
    <Centered>
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <div className="h-2 w-2 animate-pulse rounded-full bg-indigo-500" />
        Loading…
      </div>
    </Centered>
  );
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
          <p className="mt-6 text-center text-sm text-muted-foreground">
            New to Aurora?{" "}
            <Link href="/signup" className="font-medium text-indigo-400 hover:text-indigo-300">
              Create an account
            </Link>
          </p>
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
